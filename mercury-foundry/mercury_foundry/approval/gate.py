"""Approval Gate — le candidate diventano definitive solo con un'azione umana
esplicita.

Questo è l'UNICO punto del sistema autorizzato a modificare `target_project`
reale: `approve_candidate` promuove atomicamente le differenze registrate
nello staging della candidate, dopo aver riverificato che il target non sia
cambiato dal momento in cui la candidate è stata creata (altrimenti blocca
fail-closed invece di sovrascrivere silenziosamente). `reject_candidate` non
tocca mai il target: pulisce solo lo staging.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mercury_foundry.audit.logger import log_action
from mercury_foundry.policy.errors import TargetConflictError
from mercury_foundry.sandbox.staging import DiffManifest, discard_staging, promote_staging, verify_target_unchanged
from mercury_foundry.state import models


class CandidateNotFoundError(ValueError):
    pass


class InvalidCandidateStateError(ValueError):
    pass


def _diff_from_manifest(candidate: sqlite3.Row) -> DiffManifest:
    manifest = json.loads(candidate["manifest_json"]) if candidate["manifest_json"] else {}
    files = manifest.get("files", {})
    return DiffManifest(
        created=list(files.get("created", [])),
        modified=list(files.get("modified", [])),
        deleted=list(files.get("deleted", [])),
        final_hashes=dict(files.get("final_hashes", {})),
        final_sizes=dict(files.get("final_sizes", {})),
    )


def approve_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    rationale: str | None = None,
    *,
    target_root: Path | None = None,
) -> None:
    """Approva e promuove ATOMICAMENTE una candidate al target reale.

    Sequenza fail-closed:
    1. la candidate deve esistere ed essere `pending_review`;
    2. il target reale deve essere ancora, byte-per-byte, nello stato
       registrato quando lo staging della candidate è stato creato — se è
       cambiato (un'altra candidate promossa nel frattempo, una modifica
       manuale, ecc.) l'approvazione si blocca con `TargetConflictError`,
       SENZA scrivere nulla e SENZA promuovere automaticamente;
    3. solo a questo punto le differenze vengono applicate al target in
       un'unica operazione atomica (`promote_staging`): se una qualunque
       scrittura fallisce a metà, tutto ciò che questa chiamata ha già
       toccato nel target viene ripristinato prima di rilanciare l'errore —
       il target non resta mai parzialmente promosso;
    4. solo dopo una promozione riuscita lo stato passa ad `approved` e lo
       staging (non più necessario) viene eliminato.
    """
    candidate = models.get_candidate(conn, candidate_id)
    if candidate is None:
        raise CandidateNotFoundError(f"Candidate {candidate_id} non trovata")
    if candidate["status"] != "pending_review":
        raise InvalidCandidateStateError(
            f"Candidate {candidate_id} è in stato '{candidate['status']}', non 'pending_review'"
        )

    staging_root = Path(candidate["staging_root"]) if candidate["staging_root"] else None
    target_snapshot_hash = candidate["target_snapshot_hash"]
    resolved_target_root = Path(target_root) if target_root is not None else None
    if resolved_target_root is None and staging_root is not None:
        # Ricavato dal manifest, se disponibile: evita di dover passare
        # sempre esplicitamente il target da ogni chiamante.
        manifest = json.loads(candidate["manifest_json"]) if candidate["manifest_json"] else {}
        recorded_target = manifest.get("target_root")
        if recorded_target:
            resolved_target_root = Path(recorded_target)

    if staging_root is not None and target_snapshot_hash is not None and resolved_target_root is not None:
        if not verify_target_unchanged(resolved_target_root, target_snapshot_hash):
            log_action(
                conn,
                entity_type="candidate",
                entity_id=candidate_id,
                action="CANDIDATE_APPROVAL_CONFLICT",
                actor="system",
                payload={
                    "reason": "target_changed_since_candidate_created",
                    "expected_target_snapshot_hash": target_snapshot_hash,
                },
            )
            raise TargetConflictError(
                f"Il target_project è cambiato dopo la creazione della candidate {candidate_id}: "
                "promozione bloccata fail-closed, nessuna scrittura effettuata."
            )

        diff = _diff_from_manifest(candidate)
        try:
            promote_staging(staging_root, resolved_target_root, diff)
        except Exception as exc:
            log_action(
                conn,
                entity_type="candidate",
                entity_id=candidate_id,
                action="CANDIDATE_PROMOTION_FAILED",
                actor="system",
                payload={"error_type": type(exc).__name__, "message": str(exc)},
            )
            raise

    models.update_candidate_status(conn, candidate_id, "approved")
    models.create_decision(
        conn,
        task_id=candidate["task_id"],
        candidate_id=candidate_id,
        decision_type="approve",
        actor="human",
        rationale=rationale,
    )
    log_action(
        conn,
        entity_type="candidate",
        entity_id=candidate_id,
        action="CANDIDATE_APPROVED",
        actor="human",
        payload={
            "rationale": rationale,
            # Snapshot dell'identità del provider al momento dell'approvazione:
            # l'audit deve poter mostrare, anche in futuro, se l'umano ha
            # approvato codice simulato o generato da un provider reale.
            "provider_name": candidate["provider_name"],
            "is_simulated": bool(candidate["is_simulated"]),
            "promoted": staging_root is not None,
        },
    )
    if staging_root is not None:
        # Pulizia post-promozione: lo staging ha esaurito il suo scopo, la
        # verità ora vive nel target reale appena promosso.
        discard_staging(staging_root)
    models.maybe_complete_goal(conn, candidate["goal_id"])


def reject_candidate(conn: sqlite3.Connection, candidate_id: int, rationale: str | None = None) -> None:
    candidate = models.get_candidate(conn, candidate_id)
    if candidate is None:
        raise CandidateNotFoundError(f"Candidate {candidate_id} non trovata")
    if candidate["status"] != "pending_review":
        raise InvalidCandidateStateError(
            f"Candidate {candidate_id} è in stato '{candidate['status']}', non 'pending_review'"
        )

    models.update_candidate_status(conn, candidate_id, "rejected")
    models.create_decision(
        conn,
        task_id=candidate["task_id"],
        candidate_id=candidate_id,
        decision_type="reject",
        actor="human",
        rationale=rationale,
    )
    log_action(
        conn,
        entity_type="candidate",
        entity_id=candidate_id,
        action="CANDIDATE_REJECTED",
        actor="human",
        payload={
            "rationale": rationale,
            "provider_name": candidate["provider_name"],
            "is_simulated": bool(candidate["is_simulated"]),
        },
    )
    # Un reject non tocca MAI il target reale: il target non è mai stato
    # scritto da questa candidate (viveva solo nel suo staging). Lo staging,
    # non più necessario, viene eliminato qui.
    if candidate["staging_root"]:
        discard_staging(Path(candidate["staging_root"]))
    models.update_goal_status(conn, candidate["goal_id"], "blocked")

"""Approval Gate — le candidate diventano definitive solo con un'azione umana
esplicita.

Questo è l'UNICO punto del sistema autorizzato a modificare `target_project`
reale: `approve_candidate` promuove atomicamente le differenze registrate
nello staging della candidate, dopo aver riverificato che (a) il target e
(b) lo staging stesso non siano cambiati dal momento in cui la candidate è
stata creata — altrimenti blocca fail-closed invece di sovrascrivere
silenziosamente. `reject_candidate` non tocca mai il target: pulisce solo lo
staging.

MF-FIX-005 aggiunge a questo modulo:
- una riverifica dell'integrità dello staging (non solo del target) prima
  della promozione (`CandidateIntegrityError` se lo staging è stato alterato);
- una procedura di approvazione coordinata: backup restorabile del target,
  promozione filesystem, poi UNA SOLA transazione DB (status + decision +
  audit); un fallimento DB dopo la promozione ripristina il target dal
  backup; se anche il ripristino fallisse, la candidate passa a
  `recovery_required` (backup e staging preservati, nessun retry automatico);
- approve/reject idempotenti: una seconda chiamata su una candidate già
  `approved`/`rejected` non ripete alcuna scrittura, non duplica audit/decision.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mercury_foundry import config
from mercury_foundry.audit.logger import log_action
from mercury_foundry.policy.errors import CandidateIntegrityError, CandidateRecoveryRequiredError, TargetConflictError
from mercury_foundry.sandbox.staging import (
    DiffManifest,
    create_backup,
    discard_backup,
    discard_staging,
    promote_staging,
    restore_backup,
    verify_staging_integrity,
    verify_target_unchanged,
)
from mercury_foundry.state import models


class CandidateNotFoundError(ValueError):
    pass


class InvalidCandidateStateError(ValueError):
    pass


def _diff_from_manifest(manifest: dict) -> DiffManifest:
    files = manifest.get("files", {})
    return DiffManifest(
        created=list(files.get("created", [])),
        modified=list(files.get("modified", [])),
        deleted=list(files.get("deleted", [])),
        final_hashes=dict(files.get("final_hashes", {})),
        final_sizes=dict(files.get("final_sizes", {})),
    )


def _resolve_target_root(candidate: sqlite3.Row, manifest: dict, target_root: Path | None) -> Path | None:
    if target_root is not None:
        return Path(target_root)
    recorded_target = manifest.get("target_root")
    if recorded_target:
        return Path(recorded_target)
    return None


def _default_backup_root(run_id: str | None, candidate_id: int, backup_base_dir: Path) -> Path:
    return (backup_base_dir / (run_id or "unknown_run") / str(candidate_id)).resolve()


def approve_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    rationale: str | None = None,
    *,
    target_root: Path | None = None,
    backup_base_dir: Path | None = None,
) -> None:
    """Approva e promuove ATOMICAMENTE una candidate al target reale.

    Sequenza fail-closed (MF-FIX-005):
    1. la candidate deve esistere; se è già `approved` la chiamata è un
       no-op idempotente (nessuna scrittura, nessun duplicato); se è
       `rejected` o `recovery_required` è rifiutata esplicitamente — non
       esiste un percorso che approvi una candidate rifiutata o in stato di
       recovery non risolto; altrimenti deve essere `pending_review`;
    2. il target reale deve essere ancora, byte-per-byte, nello stato
       registrato quando lo staging della candidate è stato creato
       (`TargetConflictError` altrimenti, nessuna scrittura);
    3. lo STAGING deve essere ancora, byte-per-byte, nello stato registrato
       alla creazione della candidate (`CandidateIntegrityError` altrimenti,
       nessuna scrittura, staging preservato per diagnosi);
    4. viene creato un backup restorabile del target PRIMA di qualunque
       scrittura sul target;
    5. le differenze vengono promosse ATOMICAMENTE sul target
       (`promote_staging`: rollback interno automatico su fallimento parziale);
    6. status/decision/audit vengono scritti in UNA sola transazione DB;
       se questa transazione fallisce DOPO la promozione filesystem, il
       target viene ripristinato dal backup e la candidate resta
       `pending_review` (nessuno stato intermedio nascosto); se anche il
       ripristino fallisse, la candidate passa a `recovery_required` (backup
       e staging preservati, nessun retry automatico);
    7. solo dopo il successo completo (DB incluso) backup e staging vengono
       eliminati.
    """
    candidate = models.get_candidate(conn, candidate_id)
    if candidate is None:
        raise CandidateNotFoundError(f"Candidate {candidate_id} non trovata")

    status = candidate["status"]
    if status == "approved":
        # Idempotente: una seconda approvazione della stessa candidate non
        # ritocca filesystem/DB e non duplica decision/audit. Solo un audit
        # NOOP viene registrato, per trasparenza sul tentativo duplicato.
        log_action(
            conn,
            entity_type="candidate",
            entity_id=candidate_id,
            action="CANDIDATE_APPROVE_NOOP_ALREADY_APPROVED",
            actor="human",
            payload={"rationale": rationale},
        )
        return
    if status in ("rejected", "recovery_required"):
        raise InvalidCandidateStateError(
            f"Candidate {candidate_id} è in stato '{status}': non può essere approvata"
        )
    if status != "pending_review":
        raise InvalidCandidateStateError(
            f"Candidate {candidate_id} è in stato '{status}', non 'pending_review'"
        )

    manifest = json.loads(candidate["manifest_json"]) if candidate["manifest_json"] else {}
    staging_root = Path(candidate["staging_root"]) if candidate["staging_root"] else None
    target_snapshot_hash = candidate["target_snapshot_hash"]
    resolved_target_root = _resolve_target_root(candidate, manifest, target_root)
    resolved_backup_base_dir = Path(backup_base_dir) if backup_base_dir is not None else config.BACKUP_BASE_DIR

    # Candidate legacy (pre-MF-FIX-004, senza staging): niente da verificare
    # o promuovere a livello di filesystem, si limita alla transazione DB.
    if staging_root is None or target_snapshot_hash is None or resolved_target_root is None:
        _commit_approval(conn, candidate, candidate_id, rationale, promoted=False)
        models.maybe_complete_goal(conn, candidate["goal_id"])
        return

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

    integrity = verify_staging_integrity(staging_root, manifest.get("staging_manifest"))
    if not integrity.passed:
        # Nessuna informazione sensibile nel payload: solo percorsi relativi
        # e conteggi, già privi di contenuto/segreti.
        log_action(
            conn,
            entity_type="candidate",
            entity_id=candidate_id,
            action="CANDIDATE_INTEGRITY_VIOLATION",
            actor="system",
            payload={
                "reasons": integrity.reasons,
                "extra_files": integrity.extra_files,
                "missing_files": integrity.missing_files,
                "changed_files": integrity.changed_files,
            },
        )
        # Fail-closed: target non toccato, candidate resta pending_review,
        # staging preservato (NON scartato) per permettere la diagnosi.
        raise CandidateIntegrityError(
            f"Lo staging della candidate {candidate_id} è stato alterato dopo la sua creazione: "
            f"promozione bloccata fail-closed. Motivi: {'; '.join(integrity.reasons)}"
        )

    diff = _diff_from_manifest(manifest)
    backup_root = _default_backup_root(candidate["run_id"], candidate_id, resolved_backup_base_dir)
    create_backup(resolved_target_root, backup_root)
    models.set_candidate_backup_root(conn, candidate_id, str(backup_root))

    try:
        promote_staging(staging_root, resolved_target_root, diff)
    except Exception as exc:
        # La promozione stessa fa già rollback interno di ciò che ha
        # toccato: il target resta comunque al suo stato pre-promozione. Il
        # backup non serve più in questo caso specifico, ma lo lasciamo:
        # nessuna scrittura distruttiva aggiuntiva è necessaria qui.
        log_action(
            conn,
            entity_type="candidate",
            entity_id=candidate_id,
            action="CANDIDATE_PROMOTION_FAILED",
            actor="system",
            payload={"error_type": type(exc).__name__, "message": str(exc)},
        )
        discard_backup(backup_root)
        models.set_candidate_backup_root(conn, candidate_id, None)
        raise

    try:
        _commit_approval(conn, candidate, candidate_id, rationale, promoted=True)
    except Exception as db_exc:
        conn.rollback()
        try:
            restore_backup(backup_root, resolved_target_root)
        except Exception as restore_exc:
            # Ripristino stesso fallito: NON dichiariamo un successo, né un
            # semplice errore riprovabile automaticamente. Backup e staging
            # vengono preservati per una diagnosi/ripristino manuale.
            conn.execute(
                "UPDATE candidates SET status = ? WHERE id = ?", ("recovery_required", candidate_id)
            )
            conn.commit()
            log_action(
                conn,
                entity_type="candidate",
                entity_id=candidate_id,
                action="CANDIDATE_RECOVERY_REQUIRED",
                actor="system",
                payload={
                    "db_error_type": type(db_exc).__name__,
                    "db_error_message": str(db_exc),
                    "restore_error_type": type(restore_exc).__name__,
                    "restore_error_message": str(restore_exc),
                    "backup_root": str(backup_root),
                    "staging_root": str(staging_root),
                },
            )
            raise CandidateRecoveryRequiredError(
                f"Candidate {candidate_id}: promozione riuscita, scrittura DB fallita, E il ripristino "
                "automatico del target dal backup è fallito a sua volta. Richiesto intervento manuale: "
                f"backup preservato in {backup_root}, staging preservato in {staging_root}."
            ) from restore_exc

        # Rollback DB riuscito E target ripristinato dal backup: la
        # candidate resta pending_review (il rollback ha già annullato
        # l'UPDATE di stato non commesso). Nessuno stato nascosto: l'errore
        # originale viene rilanciato dopo un audit dedicato (fresh commit,
        # fuori dalla transazione fallita).
        log_action(
            conn,
            entity_type="candidate",
            entity_id=candidate_id,
            action="CANDIDATE_PROMOTION_DB_FAILURE_ROLLED_BACK",
            actor="system",
            payload={
                "error_type": type(db_exc).__name__,
                "message": str(db_exc),
                "target_restored_from_backup": True,
            },
        )
        raise

    # Successo completo (filesystem + DB): backup e staging non servono più.
    discard_backup(backup_root)
    models.set_candidate_backup_root(conn, candidate_id, None)
    discard_staging(staging_root)
    models.maybe_complete_goal(conn, candidate["goal_id"])


def _commit_approval(
    conn: sqlite3.Connection,
    candidate: sqlite3.Row,
    candidate_id: int,
    rationale: str | None,
    *,
    promoted: bool,
) -> None:
    """Scrive status/decision/audit in UNA sola transazione: tutte le
    scritture qui usano varianti SENZA commit, poi un unico `conn.commit()`
    finale. Se qualunque passo (incluso il commit) solleva un'eccezione, il
    chiamante fa `conn.rollback()`: nessuna delle scritture qui diventa
    visibile a metà."""
    models.update_candidate_status_no_commit(conn, candidate_id, "approved")
    models.create_decision_no_commit(
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
            "provider_name": candidate["provider_name"],
            "is_simulated": bool(candidate["is_simulated"]),
            "promoted": promoted,
        },
        commit=False,
    )
    conn.commit()


def reject_candidate(conn: sqlite3.Connection, candidate_id: int, rationale: str | None = None) -> None:
    """Rifiuta una candidate. Non tocca MAI il target reale. Idempotente:
    una seconda chiamata su una candidate già `rejected` è un no-op (nessuna
    scrittura, nessun duplicato di decision/audit). Una candidate `rejected`
    non può mai più essere approvata (vedi `approve_candidate`)."""
    candidate = models.get_candidate(conn, candidate_id)
    if candidate is None:
        raise CandidateNotFoundError(f"Candidate {candidate_id} non trovata")

    status = candidate["status"]
    if status == "rejected":
        log_action(
            conn,
            entity_type="candidate",
            entity_id=candidate_id,
            action="CANDIDATE_REJECT_NOOP_ALREADY_REJECTED",
            actor="human",
            payload={"rationale": rationale},
        )
        return
    if status != "pending_review":
        raise InvalidCandidateStateError(
            f"Candidate {candidate_id} è in stato '{status}', non 'pending_review'"
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

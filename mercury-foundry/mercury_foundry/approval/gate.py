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
from mercury_foundry.policy.errors import (
    CandidateIntegrityError,
    CandidateRecoveryRequiredError,
    LegacyCandidateNotPromotableError,
    TargetConflictError,
)
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


def _missing_promotion_guarantees(
    staging_root: Path | None,
    target_snapshot_hash: str | None,
    resolved_target_root: Path | None,
    manifest: dict,
) -> list[str]:
    """Elenca (MF-FIX-006, requisito 2) quali garanzie introdotte da
    MF-FIX-004/MF-FIX-005 mancano per questa candidate. Una lista NON vuota
    significa che `approve_candidate` deve fermarsi fail-closed PRIMA di
    qualunque verifica di conflitto/integrità o scrittura — non esiste un
    percorso DB-only per candidate prive di queste garanzie."""
    reasons: list[str] = []
    if staging_root is None:
        reasons.append("staging_root mancante")
    if target_snapshot_hash is None:
        reasons.append("target_snapshot_hash mancante")
    if resolved_target_root is None:
        reasons.append("target_root non registrato")

    staging_manifest = manifest.get("staging_manifest")
    if not staging_manifest or not isinstance(staging_manifest, dict):
        reasons.append("staging_manifest mancante o incompleto")

    diff = manifest.get("files")
    if not isinstance(diff, dict) or not {"created", "modified", "deleted", "final_hashes", "final_sizes"} <= set(
        diff.keys()
    ):
        reasons.append("diff manifest mancante o non valido")

    return reasons


def approve_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    rationale: str | None = None,
    *,
    target_root: Path | None = None,
    backup_base_dir: Path | None = None,
) -> None:
    """Approva e promuove ATOMICAMENTE una candidate al target reale.

    Sequenza fail-closed (MF-FIX-005 + MF-FIX-006):
    1. la candidate deve esistere; se è già `approved` la chiamata è un
       no-op idempotente (nessuna scrittura, nessun duplicato); se è
       `rejected` o `recovery_required` è rifiutata esplicitamente — non
       esiste un percorso che approvi una candidate rifiutata o in stato di
       recovery non risolto; altrimenti deve essere `pending_review`;
    2. la candidate deve possedere TUTTE le garanzie MF-FIX-004/MF-FIX-005:
       staging_root, target_snapshot_hash, target_root registrato, uno
       staging_manifest completo e un diff manifest valido. Se anche una
       sola manca (es. le candidate legacy pre-MF-FIX-004), la promozione si
       ferma fail-closed con `LegacyCandidateNotPromotableError` — NON esiste
       più un percorso di approvazione DB-only per queste candidate: restano
       `pending_review` per sempre a meno di un rifiuto manuale esplicito;
    3. il target reale deve essere ancora, byte-per-byte, nello stato
       registrato quando lo staging della candidate è stato creato
       (`TargetConflictError` altrimenti, nessuna scrittura);
    4. lo STAGING deve essere ancora, byte-per-byte, nello stato registrato
       alla creazione della candidate (`CandidateIntegrityError` altrimenti,
       nessuna scrittura, staging preservato per diagnosi);
    5. viene creato un backup restorabile del target PRIMA di qualunque
       scrittura sul target;
    6. le differenze vengono promosse ATOMICAMENTE sul target
       (`promote_staging`: rollback interno automatico su fallimento parziale);
    7. status/decision/audit vengono scritti in UNA sola transazione DB;
       se questa transazione fallisce DOPO la promozione filesystem, il
       target viene ripristinato dal backup e la candidate resta
       `pending_review` (nessuno stato intermedio nascosto); se anche il
       ripristino fallisse, la candidate passa a `recovery_required` (backup
       e staging preservati, nessun retry automatico);
    8. solo dopo il successo completo (DB incluso) backup e staging vengono
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

    # MF-FIX-006: il bypass DB-only per candidate legacy è stato rimosso.
    # Nessuna candidate priva delle garanzie MF-FIX-004/MF-FIX-005 (staging,
    # snapshot del target, target_root registrato, manifest completi) può
    # diventare `approved` — fail-closed PRIMA di qualunque verifica di
    # conflitto/integrità, PRIMA di qualunque backup: non c'è nulla di utile
    # da riverificare o da cui ripristinare per una candidate così incompleta.
    missing = _missing_promotion_guarantees(staging_root, target_snapshot_hash, resolved_target_root, manifest)
    if missing:
        log_action(
            conn,
            entity_type="candidate",
            entity_id=candidate_id,
            action="CANDIDATE_LEGACY_NOT_PROMOTABLE",
            actor="system",
            payload={"reasons": missing},
        )
        raise LegacyCandidateNotPromotableError(
            f"Candidate {candidate_id} non è promuovibile: mancano le garanzie "
            f"introdotte da MF-FIX-004/MF-FIX-005 ({'; '.join(missing)}). "
            "Nessuna scrittura effettuata: la candidate resta 'pending_review' "
            "e può solo essere rifiutata manualmente."
        )

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
        _commit_approval(conn, candidate, candidate_id, rationale)
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
            "promoted": True,
        },
        commit=False,
    )
    conn.commit()


class ApprovalRevokeConflictError(ValueError):
    """Il target non corrisponde esattamente ai file promossi dalla candidate:
    operazione compensativa bloccata fail-closed, nessuna rimozione effettuata."""


def revoke_approval_incident(
    conn: sqlite3.Connection,
    candidate_id: int,
    rationale: str,
    *,
    target_root: Path | None = None,
) -> None:
    """Operazione compensativa auditabile per una promozione involontaria (MF-INCIDENT-001).

    Rimuove ESCLUSIVAMENTE i file che il manifest della candidate attesta come
    promossi nel target, dopo aver verificato byte per byte che coincidano.
    Non modifica né cancella la decisione `approve` o l'audit `CANDIDATE_APPROVED`
    originali: la storia è immutabile. Registra una nuova decisione
    `approval_revoke_incident` e un audit `CANDIDATE_APPROVAL_REVOKED_INCIDENT`.

    Fail-closed: se QUALSIASI file del manifest non coincide con il target
    (hash o dimensione diversi, o file mancante), l'operazione si ferma senza
    rimuovere nulla.
    """
    import hashlib

    candidate = models.get_candidate(conn, candidate_id)
    if candidate is None:
        raise CandidateNotFoundError(f"Candidate {candidate_id} non trovata")

    status = candidate["status"]
    if status != "approved":
        raise InvalidCandidateStateError(
            f"Candidate {candidate_id} è in stato '{status}', non 'approved': "
            "revoke_approval_incident si applica solo a candidate approvate."
        )

    # MF-FIX-007: leggi il goal PRIMA di qualsiasi DML, per usarlo nella
    # transazione atomica finale (invariante goal/candidate).
    goal_id = candidate["goal_id"]
    goal = models.get_goal(conn, goal_id)
    goal_was_done = (goal is not None and goal["status"] == "done")

    manifest = json.loads(candidate["manifest_json"]) if candidate["manifest_json"] else {}
    final_hashes: dict = manifest.get("files", {}).get("final_hashes", {})
    final_sizes: dict = manifest.get("files", {}).get("final_sizes", {})
    promoted_files: list[str] = (
        list(manifest.get("files", {}).get("created", []))
        + list(manifest.get("files", {}).get("modified", []))
    )

    resolved_target_root = _resolve_target_root(candidate, manifest, target_root)
    if resolved_target_root is None:
        raise InvalidCandidateStateError(
            f"Candidate {candidate_id}: target_root non determinabile dal manifest."
        )

    # Verifica byte-per-byte che OGNI file promosso coincida. Fail-closed su
    # qualsiasi discrepanza — nessuna rimozione avviene prima della verifica completa.
    mismatches: list[str] = []
    for rel_path in promoted_files:
        target_file = resolved_target_root / rel_path
        if not target_file.exists():
            mismatches.append(f"{rel_path}: mancante nel target")
            continue
        content = target_file.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest()
        actual_size = len(content)
        expected_hash = final_hashes.get(rel_path)
        expected_size = final_sizes.get(rel_path)
        if actual_hash != expected_hash:
            mismatches.append(
                f"{rel_path}: hash atteso {expected_hash!r}, trovato {actual_hash!r}"
            )
        elif expected_size is not None and actual_size != expected_size:
            mismatches.append(
                f"{rel_path}: dimensione attesa {expected_size}, trovata {actual_size}"
            )

    if mismatches:
        log_action(
            conn,
            entity_type="candidate",
            entity_id=candidate_id,
            action="CANDIDATE_APPROVAL_REVOKE_BLOCKED_MISMATCH",
            actor="human",
            payload={"mismatches": mismatches, "rationale": rationale},
        )
        raise ApprovalRevokeConflictError(
            f"Candidate {candidate_id}: il target non coincide con il manifest della candidate — "
            f"operazione compensativa bloccata fail-closed. Discrepanze: {mismatches}"
        )

    # Verifica superata: rimuovi SOLO i file promossi, nell'ordine del manifest.
    hashes_before = {
        rel: {"hash": final_hashes[rel], "size": final_sizes.get(rel)}
        for rel in promoted_files
    }
    for rel_path in promoted_files:
        (resolved_target_root / rel_path).unlink()

    # Aggiorna stato candidate e registra decisione + audit nella stessa transazione.
    # MF-FIX-007: tutta la sezione DB (candidate + decision + audit + eventuale goal)
    # è in un'unica transazione atomica: o tutto viene committato o nulla.
    models.update_candidate_status_no_commit(conn, candidate_id, "approval_revoked")
    models.create_decision_no_commit(
        conn,
        task_id=candidate["task_id"],
        candidate_id=candidate_id,
        decision_type="approval_revoke_incident",
        actor="human",
        rationale=rationale,
    )
    log_action(
        conn,
        entity_type="candidate",
        entity_id=candidate_id,
        action="CANDIDATE_APPROVAL_REVOKED_INCIDENT",
        actor="human",
        payload={
            "rationale": rationale,
            "run_id": candidate["run_id"],
            "candidate_id": candidate_id,
            "files_removed": promoted_files,
            "hashes_before": hashes_before,
            "target_root": str(resolved_target_root),
            "original_approve_decision_preserved": True,
            "original_audit_preserved": True,
        },
        commit=False,
    )

    # MF-FIX-007 — invariante goal/candidate: un goal non può restare DONE
    # se la sua candidate approvata viene revocata. Se il goal era 'done',
    # lo riportiamo ad 'awaiting_approval' (stato già esistente nella state
    # machine) nella stessa transazione atomica.
    if goal_was_done:
        models.update_goal_status_no_commit(conn, goal_id, "awaiting_approval")
        log_action(
            conn,
            entity_type="goal",
            entity_id=goal_id,
            action="GOAL_AWAITING_APPROVAL_REVERTED_AFTER_REVOKE",
            actor="system",
            payload={
                "candidate_id": candidate_id,
                "rationale": rationale,
                "previous_goal_status": "done",
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

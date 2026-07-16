"""MF-FIX-007 — Test invariante goal/candidate.

Verifica che:
1. Revoca di candidate legata a goal DONE → goal torna 'awaiting_approval'.
2. Revoca di candidate legata a goal non concluso → goal non viene alterato.
3. Rollback DB completo se un'eccezione viene sollevata prima di conn.commit().
4. Audit event GOAL_AWAITING_APPROVAL_REVERTED_AFTER_REVOKE prodotto correttamente.
5. Dopo revoke non è possibile trovare goal DONE + candidate approval_revoked.
6. Seconda revoca su stessa candidate → InvalidCandidateStateError (già revocata).

Nessuna scrittura fuori da tmp_path. Zero chiamate al provider.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mercury_foundry.approval import gate
from mercury_foundry.approval.gate import (
    InvalidCandidateStateError,
    revoke_approval_incident,
)
from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.state import db, models
from mercury_foundry.wiring import build_foundry


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _build_isolated(tmp_path: Path):
    return build_foundry(
        db_path=tmp_path / "mf.db",
        sandbox_root=tmp_path / "target",
        provider_name="fake",
    )


def _create_approved_candidate(tmp_path: Path):
    """Setup: goal → run → candidate → approvazione. Ritorna (foundry, candidate_id)."""
    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test invariante goal candidate")
    run_result = foundry.orchestrator.run_goal(goal_id)
    candidate_id = run_result.task_outcomes[0].candidate_id
    gate.approve_candidate(
        foundry.conn,
        candidate_id,
        backup_base_dir=foundry.backup_base_dir,
    )
    return foundry, candidate_id


# ---------------------------------------------------------------------------
# Test 1 — Revoca con goal DONE → goal torna awaiting_approval
# ---------------------------------------------------------------------------

def test_revoke_with_done_goal_reverts_goal_to_awaiting_approval(tmp_path):
    """Quando la candidate approvata viene revocata, il goal DONE torna awaiting_approval."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)
    conn = foundry.conn
    target_root = foundry.workspace.root

    candidate = models.get_candidate(conn, candidate_id)
    goal_id = candidate["goal_id"]

    # Pre-condizione: goal è DONE dopo l'approvazione.
    goal_before = models.get_goal(conn, goal_id)
    assert goal_before["status"] == "done", (
        f"Pre-condizione fallita: goal atteso 'done', trovato '{goal_before['status']}'"
    )

    revoke_approval_incident(
        conn, candidate_id,
        rationale="test: verifica invariante goal done → awaiting_approval",
        target_root=target_root,
    )

    # Post-condizione: candidate è revocata, goal è awaiting_approval.
    candidate_after = models.get_candidate(conn, candidate_id)
    assert candidate_after["status"] == "approval_revoked", (
        f"Candidate attesa 'approval_revoked', trovata '{candidate_after['status']}'"
    )

    goal_after = models.get_goal(conn, goal_id)
    assert goal_after["status"] == "awaiting_approval", (
        f"Goal atteso 'awaiting_approval', trovato '{goal_after['status']}'"
    )


# ---------------------------------------------------------------------------
# Test 2 — Revoca con goal non concluso → goal non viene alterato
# ---------------------------------------------------------------------------

def test_revoke_with_non_done_goal_does_not_alter_goal_status(tmp_path):
    """Se il goal non era 'done' al momento della revoca, il suo status non cambia."""
    foundry = _build_isolated(tmp_path)
    conn = foundry.conn

    # Crea una candidate approvata senza che il goal completi (forziamo status manually).
    goal_id = foundry.orchestrator.submit_goal("test invariante goal non done")
    run_result = foundry.orchestrator.run_goal(goal_id)
    candidate_id = run_result.task_outcomes[0].candidate_id

    # Approva la candidate (goal diventa done per effetto di maybe_complete_goal).
    gate.approve_candidate(conn, candidate_id, backup_base_dir=foundry.backup_base_dir)

    # Imposta manualmente il goal su un stato diverso da 'done' prima della revoca,
    # per testare il ramo "goal non era done".
    models.update_goal_status(conn, goal_id, "awaiting_approval")
    goal_before_revoke = models.get_goal(conn, goal_id)
    assert goal_before_revoke["status"] == "awaiting_approval"

    revoke_approval_incident(
        conn, candidate_id,
        rationale="test: goal non done, non deve essere alterato",
        target_root=foundry.workspace.root,
    )

    goal_after = models.get_goal(conn, goal_id)
    assert goal_after["status"] == "awaiting_approval", (
        f"Goal atteso 'awaiting_approval' (immutato), trovato '{goal_after['status']}'"
    )


# ---------------------------------------------------------------------------
# Test 3 — Rollback DB se eccezione prima di conn.commit()
# ---------------------------------------------------------------------------

def test_revoke_db_rollback_on_exception_before_commit(tmp_path):
    """Se un'eccezione viene sollevata tra le operazioni no-commit e conn.commit(),
    il DB non deve mostrare nessuna modifica (transaction non committata)."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)
    conn = foundry.conn
    target_root = foundry.workspace.root

    candidate = models.get_candidate(conn, candidate_id)
    goal_id = candidate["goal_id"]

    goal_status_before = models.get_goal(conn, goal_id)["status"]
    candidate_status_before = models.get_candidate(conn, candidate_id)["status"]
    audit_count_before = conn.execute("SELECT COUNT(*) as n FROM audit_log").fetchone()["n"]

    # Monkeypatch update_goal_status_no_commit per sollevare un'eccezione
    # DOPO le operazioni no-commit su candidate/decision/audit, ma PRIMA di commit().
    # Questo simula un fallimento a metà transazione.
    original_update_goal = models.update_goal_status_no_commit

    def _raise_mid_transaction(c, gid, status):
        raise RuntimeError("Errore simulato a metà transazione — test rollback")

    with patch.object(models, "update_goal_status_no_commit", side_effect=_raise_mid_transaction):
        with pytest.raises(RuntimeError, match="Errore simulato"):
            revoke_approval_incident(
                conn, candidate_id,
                rationale="test: rollback su eccezione",
                target_root=target_root,
            )

    # Dopo l'eccezione, la connessione potrebbe avere un'operazione pendente.
    # Facciamo rollback esplicito e poi verifichiamo tramite una nuova connessione.
    try:
        conn.rollback()
    except Exception:
        pass

    # Apri una connessione NUOVA per leggere lo stato committato del DB.
    fresh_conn = db.connect(tmp_path / "mf.db")

    goal_after = models.get_goal(fresh_conn, goal_id)
    candidate_after = models.get_candidate(fresh_conn, candidate_id)
    audit_count_after = fresh_conn.execute("SELECT COUNT(*) as n FROM audit_log").fetchone()["n"]

    assert candidate_after["status"] == candidate_status_before, (
        f"Candidate non doveva cambiare stato nel DB committato: "
        f"atteso '{candidate_status_before}', trovato '{candidate_after['status']}'"
    )
    assert goal_after["status"] == goal_status_before, (
        f"Goal non doveva cambiare stato nel DB committato: "
        f"atteso '{goal_status_before}', trovato '{goal_after['status']}'"
    )
    assert audit_count_after == audit_count_before, (
        f"Il conteggio audit non deve cambiare dopo rollback: "
        f"prima {audit_count_before}, dopo {audit_count_after}"
    )


# ---------------------------------------------------------------------------
# Test 4 — Audit event GOAL_AWAITING_APPROVAL_REVERTED_AFTER_REVOKE prodotto
# ---------------------------------------------------------------------------

def test_revoke_produces_goal_revert_audit_event(tmp_path):
    """revoke_approval_incident deve produrre un audit event
    GOAL_AWAITING_APPROVAL_REVERTED_AFTER_REVOKE quando il goal era DONE."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)
    conn = foundry.conn
    target_root = foundry.workspace.root

    candidate = models.get_candidate(conn, candidate_id)
    goal_id = candidate["goal_id"]

    revoke_approval_incident(
        conn, candidate_id,
        rationale="test: verifica audit event goal revert",
        target_root=target_root,
    )

    audit_rows = list_audit_log(conn, limit=1000)
    goal_revert_events = [
        r for r in audit_rows
        if r["action"] == "GOAL_AWAITING_APPROVAL_REVERTED_AFTER_REVOKE"
        and r["entity_id"] == goal_id
    ]
    assert len(goal_revert_events) == 1, (
        f"Atteso esattamente 1 audit GOAL_AWAITING_APPROVAL_REVERTED_AFTER_REVOKE, "
        f"trovati {len(goal_revert_events)}"
    )

    event = goal_revert_events[0]
    assert event["actor"] == "system"
    assert event["entity_type"] == "goal"

    payload = json.loads(event["payload_json"])
    assert payload["candidate_id"] == candidate_id
    assert payload["previous_goal_status"] == "done"


# ---------------------------------------------------------------------------
# Test 5 — Impossibilità di goal DONE + candidate approval_revoked dopo fix
# ---------------------------------------------------------------------------

def test_goal_done_with_revoked_candidate_impossible_after_fix(tmp_path):
    """Dopo revoke_approval_incident non deve essere possibile trovare
    goal DONE + candidate approval_revoked sullo stesso goal."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)
    conn = foundry.conn
    target_root = foundry.workspace.root

    candidate = models.get_candidate(conn, candidate_id)
    goal_id = candidate["goal_id"]

    # Pre-condizione: lo stato inconsistente (quello che MF-FIX-007 corregge).
    assert models.get_goal(conn, goal_id)["status"] == "done"
    assert models.get_candidate(conn, candidate_id)["status"] == "approved"

    revoke_approval_incident(
        conn, candidate_id,
        rationale="test: verifica impossibilità stato inconsistente",
        target_root=target_root,
    )

    goal_final = models.get_goal(conn, goal_id)
    candidate_final = models.get_candidate(conn, candidate_id)

    # Lo stato inconsistente non può esistere.
    inconsistent = (
        goal_final["status"] == "done"
        and candidate_final["status"] == "approval_revoked"
    )
    assert not inconsistent, (
        f"Stato inconsistente trovato: goal={goal_final['status']}, "
        f"candidate={candidate_final['status']}"
    )

    # Verifica i valori esatti attesi.
    assert candidate_final["status"] == "approval_revoked"
    assert goal_final["status"] == "awaiting_approval"


# ---------------------------------------------------------------------------
# Test 6 — Seconda revoca → InvalidCandidateStateError (idempotenza controllata)
# ---------------------------------------------------------------------------

def test_second_revoke_raises_invalid_candidate_state_error(tmp_path):
    """Una seconda chiamata a revoke_approval_incident su una candidate già
    approval_revoked solleva InvalidCandidateStateError (fail-closed)."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)
    conn = foundry.conn
    target_root = foundry.workspace.root

    # Prima revoca: deve riuscire.
    revoke_approval_incident(
        conn, candidate_id,
        rationale="prima revoca",
        target_root=target_root,
    )

    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "approval_revoked"

    # Seconda revoca: deve sollevare InvalidCandidateStateError.
    with pytest.raises(InvalidCandidateStateError) as exc_info:
        revoke_approval_incident(
            conn, candidate_id,
            rationale="seconda revoca — deve fallire",
            target_root=target_root,
        )

    msg = str(exc_info.value)
    assert "approval_revoked" in msg or "approved" in msg, (
        f"Messaggio di errore non descrive lo stato: {msg}"
    )

    # Il goal non deve essere stato alterato dalla seconda revoca fallita.
    goal_after_second = models.get_goal(conn, candidate["goal_id"])
    assert goal_after_second["status"] == "awaiting_approval", (
        f"Il goal non deve cambiare dopo la seconda revoca fallita: {goal_after_second['status']}"
    )

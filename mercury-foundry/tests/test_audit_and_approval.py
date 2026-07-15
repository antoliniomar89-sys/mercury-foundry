"""Test dedicati per Approval Gate obbligatorio e comportamento append-only dell'audit log."""

import sqlite3

from mercury_foundry.approval import gate
from mercury_foundry.audit.logger import list_audit_log, log_action
from mercury_foundry.state import models
from mercury_foundry.wiring import build_foundry


def test_goal_does_not_become_done_without_explicit_approval(tmp_path):
    foundry = build_foundry(
        db_path=tmp_path / "mercury_foundry.db",
        sandbox_root=tmp_path / "target_project",
        provider_name="fake",
    )
    goal_id = foundry.orchestrator.submit_goal("aggiungi una capability health check")
    foundry.orchestrator.run_goal(goal_id)

    goal = models.get_goal(foundry.conn, goal_id)
    assert goal["status"] == "awaiting_approval"  # non 'done': nessuna approvazione automatica

    candidates = models.list_candidates(foundry.conn, goal_id)
    assert all(c["status"] == "pending_review" for c in candidates)


def test_rejecting_a_candidate_blocks_the_goal_and_never_marks_it_done(tmp_path):
    foundry = build_foundry(
        db_path=tmp_path / "mercury_foundry.db",
        sandbox_root=tmp_path / "target_project",
        provider_name="fake",
    )
    goal_id = foundry.orchestrator.submit_goal("aggiungi una capability health check")
    goal_run = foundry.orchestrator.run_goal(goal_id)
    candidate_id = goal_run.task_outcomes[0].candidate_id

    gate.reject_candidate(foundry.conn, candidate_id, rationale="Non conforme")

    goal = models.get_goal(foundry.conn, goal_id)
    assert goal["status"] == "blocked"
    candidate = models.get_candidate(foundry.conn, candidate_id)
    assert candidate["status"] == "rejected"


def test_approving_a_candidate_twice_is_idempotent_and_safe(tmp_path):
    foundry = build_foundry(
        db_path=tmp_path / "mercury_foundry.db",
        sandbox_root=tmp_path / "target_project",
        provider_name="fake",
    )
    goal_id = foundry.orchestrator.submit_goal("aggiungi una capability health check")
    goal_run = foundry.orchestrator.run_goal(goal_id)
    candidate_id = goal_run.task_outcomes[0].candidate_id

    gate.approve_candidate(foundry.conn, candidate_id, backup_base_dir=foundry.backup_base_dir)
    # MF-FIX-005: approve è idempotente — una seconda chiamata su una
    # candidate già approvata NON solleva più un errore, è un no-op sicuro
    # (nessuna riscrittura di filesystem/DB, nessun decision/audit duplicato).
    candidate_before_second_call = models.get_candidate(foundry.conn, candidate_id)
    audit_rows_before = list_audit_log(foundry.conn, limit=1000)

    gate.approve_candidate(foundry.conn, candidate_id, backup_base_dir=foundry.backup_base_dir)

    candidate_after_second_call = models.get_candidate(foundry.conn, candidate_id)
    assert dict(candidate_after_second_call) == dict(candidate_before_second_call)
    audit_rows_after = list_audit_log(foundry.conn, limit=1000)
    assert len(audit_rows_after) == len(audit_rows_before) + 1
    assert audit_rows_after[-1]["action"] == "CANDIDATE_APPROVE_NOOP_ALREADY_APPROVED"


def test_audit_log_is_append_only_previous_rows_never_change(tmp_path):
    foundry = build_foundry(
        db_path=tmp_path / "mercury_foundry.db",
        sandbox_root=tmp_path / "target_project",
        provider_name="fake",
    )
    goal_id = foundry.orchestrator.submit_goal("aggiungi una capability health check")
    foundry.orchestrator.run_goal(goal_id)

    rows_before = list_audit_log(foundry.conn)
    snapshot_before = [(r["id"], r["action"], r["payload_json"], r["created_at"]) for r in rows_before]

    # Ulteriori azioni di sistema (un nuovo log_action) devono solo AGGIUNGERE righe.
    log_action(foundry.conn, entity_type="goal", entity_id=goal_id, action="NOOP_PROBE", actor="system")

    rows_after = list_audit_log(foundry.conn, limit=1000)
    snapshot_after_same_prefix = [
        (r["id"], r["action"], r["payload_json"], r["created_at"]) for r in rows_after[: len(snapshot_before)]
    ]

    assert snapshot_after_same_prefix == snapshot_before
    assert len(rows_after) == len(rows_before) + 1

    # Non esiste alcuna API di update/delete esposta dal logger: solo insert + select.
    import mercury_foundry.audit.logger as logger_module

    exported_names = [name for name in dir(logger_module) if not name.startswith("_")]
    assert not any("update" in n.lower() or "delete" in n.lower() for n in exported_names)


def test_audit_log_survives_direct_connection_reopen(tmp_path):
    """Le righe scritte restano identiche anche riaprendo la connessione al DB."""
    from mercury_foundry.state import db

    db_path = tmp_path / "mercury_foundry.db"
    foundry = build_foundry(db_path=db_path, sandbox_root=tmp_path / "target_project", provider_name="fake")
    goal_id = foundry.orchestrator.submit_goal("aggiungi una capability health check")
    foundry.orchestrator.run_goal(goal_id)
    foundry.conn.close()

    reopened: sqlite3.Connection = db.connect(db_path)
    rows = list_audit_log(reopened, limit=1000)
    assert len(rows) > 0
    assert rows[0]["action"] == "GOAL_SUBMITTED"

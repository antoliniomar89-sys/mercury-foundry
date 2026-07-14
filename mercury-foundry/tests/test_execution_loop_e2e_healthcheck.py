"""Test end-to-end richiesto dal brief:

la Foundry riceve "aggiungi una capability health check", crea il piano,
implementa la capability, genera ed esegue REALMENTE i test, corregge
l'errore reale del tentativo 1 (FIX), e produce una candidate verificata
in attesa di approvazione umana. Nessun risultato è simulato: solo il
provider AI (FakeModel) è dichiarato come simulazione controllata.
"""

from mercury_foundry.approval import gate
from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.state import models
from mercury_foundry.wiring import build_foundry


def test_end_to_end_health_check(tmp_path):
    db_path = tmp_path / "mercury_foundry.db"
    sandbox_path = tmp_path / "target_project"

    foundry = build_foundry(db_path=db_path, sandbox_root=sandbox_path, provider_name="fake")
    assert foundry.ai_provider.is_simulated is True

    goal_id = foundry.orchestrator.submit_goal("aggiungi una capability health check")
    goal_run = foundry.orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "awaiting_approval"
    assert len(goal_run.task_outcomes) == 1

    outcome = goal_run.task_outcomes[0]
    assert outcome.status == "candidate_created"
    # Il tentativo 1 fallisce davvero (bug noto), il tentativo 2 corregge: 2 tentativi usati.
    assert outcome.attempts_used == 2
    assert outcome.candidate_id is not None

    # Verifica reale nel DB: due attempt, il primo fallito, il secondo passato.
    attempts = models.get_attempts_for_task(foundry.conn, outcome.task_id)
    assert len(attempts) == 2
    assert attempts[0]["status"] == "failure"
    assert attempts[1]["status"] == "success"

    test_results_attempt_1 = models.get_test_results_for_attempt(foundry.conn, attempts[0]["id"])
    assert test_results_attempt_1[0]["passed"] == 0
    test_results_attempt_2 = models.get_test_results_for_attempt(foundry.conn, attempts[1]["id"])
    assert test_results_attempt_2[0]["passed"] == 1

    # La candidate esiste ma non è ancora approvata: serve un'azione umana.
    candidate = models.get_candidate(foundry.conn, outcome.candidate_id)
    assert candidate["status"] == "pending_review"
    # La candidate porta con sé l'identità del provider e il flag di simulazione:
    # non deve poter essere scambiata per una generazione AI reale.
    assert candidate["provider_name"] == "fake-deterministic"
    assert bool(candidate["is_simulated"]) is True

    # Audit log copre l'intero ciclo.
    audit_rows = list_audit_log(foundry.conn)
    actions = [row["action"] for row in audit_rows]
    for expected in [
        "GOAL_SUBMITTED",
        "TASK_CREATED",
        "TASK_STARTED",
        "BUILD_STARTED",
        "BUILD_COMPLETED",
        "TEST_STARTED",
        "TEST_COMPLETED",
        "FIX_REQUIRED",
        "VERIFY_PASSED",
        "CANDIDATE_CREATED",
        "GOAL_AWAITING_APPROVAL",
    ]:
        assert expected in actions, f"azione mancante nell'audit log: {expected}"

    # Approvazione umana esplicita (Approval Gate).
    gate.approve_candidate(foundry.conn, outcome.candidate_id, rationale="Test end-to-end superato")
    candidate_after = models.get_candidate(foundry.conn, outcome.candidate_id)
    assert candidate_after["status"] == "approved"

    goal_after = models.get_goal(foundry.conn, goal_id)
    assert goal_after["status"] == "done"

    actions_after = [row["action"] for row in list_audit_log(foundry.conn)]
    assert "CANDIDATE_APPROVED" in actions_after

    # Il record di audit dell'approvazione conserva uno snapshot dell'identità
    # del provider e della simulazione al momento della decisione umana.
    approval_row = next(r for r in list_audit_log(foundry.conn) if r["action"] == "CANDIDATE_APPROVED")
    import json as _json

    approval_payload = _json.loads(approval_row["payload_json"])
    assert approval_payload["provider_name"] == "fake-deterministic"
    assert approval_payload["is_simulated"] is True


def test_max_three_attempts_blocks_task_when_always_failing(tmp_path, monkeypatch):
    """Verifica il vincolo 'massimo 3 tentativi automatici per task'."""
    from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal

    class AlwaysBrokenProvider(AIProvider):
        name = "always-broken-fake"
        is_simulated = True

        def propose_plan(self, goal_description: str) -> list[str]:
            return ["task che fallisce sempre"]

        def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
            return PatchProposal(
                summary="Patch che fallisce sempre di proposito",
                files=[FileChange(path="broken.py", content="def f():\n    return 1\n")],
                test_files=[
                    FileChange(
                        path="tests/test_broken.py",
                        content="import broken\n\n\ndef test_always_fails():\n    assert broken.f() == 2\n",
                    )
                ],
                provider_name=self.name,
                is_simulated=True,
            )

    from mercury_foundry.agents.builder import Builder
    from mercury_foundry.agents.evaluator import Evaluator
    from mercury_foundry.execution.loop import ExecutionLoop
    from mercury_foundry.orchestrator.orchestrator import Orchestrator
    from mercury_foundry.sandbox.workspace import Workspace
    from mercury_foundry.state import db
    from mercury_foundry.testing.runner import TestRunner

    db_path = tmp_path / "mercury_foundry.db"
    sandbox_path = tmp_path / "target_project"

    conn = db.connect(db_path)
    ai_provider = AlwaysBrokenProvider()
    workspace = Workspace(sandbox_path)
    builder = Builder(ai_provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    execution_loop = ExecutionLoop(conn, builder, evaluator)
    orchestrator = Orchestrator(conn, ai_provider, execution_loop)

    goal_id = orchestrator.submit_goal("task che fallisce sempre")
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "blocked"
    outcome = goal_run.task_outcomes[0]
    assert outcome.status == "blocked"
    assert outcome.attempts_used == 3

    attempts = models.get_attempts_for_task(conn, outcome.task_id)
    assert len(attempts) == 3
    assert all(a["status"] == "failure" for a in attempts)

    task = models.get_task(conn, outcome.task_id)
    assert task["status"] == "blocked"

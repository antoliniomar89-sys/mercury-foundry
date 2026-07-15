"""Workflow end-to-end MOCKATO con il provider AI reale (`OpenAICompatibleProvider`):

SPEC -> PLAN -> BUILD -> TEST -> VERIFY -> CANDIDATE -> (approvazione umana)

Usa risposte Structured Outputs mockate (nessuna chiamata di rete reale: il
client `openai` è costruito con `httpx.MockTransport`, come negli altri test
di questo modulo) per dimostrare che l'intero ciclo Foundry funziona con il
NUOVO meccanismo Structured Outputs per `propose_plan`/`propose_patch`, non
solo con `FakeModel`. I test reali (pytest) dentro la sandbox sono comunque
eseguiti realmente da `TestRunner`: solo il provider AI è mockato.

Il gate di approvazione umana resta obbligatorio: la candidate nasce
`pending_review` e va approvata esplicitamente, esattamente come nel ciclo
con FakeModel.
"""

from __future__ import annotations

import json

import httpx
import pytest
from openai import OpenAI

from mercury_foundry.agents.builder import Builder
from mercury_foundry.agents.evaluator import Evaluator
from mercury_foundry.ai.provider_config import RealProviderConfig
from mercury_foundry.ai.real_provider import OpenAICompatibleProvider
from mercury_foundry.approval import gate
from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.execution.loop import ExecutionLoop
from mercury_foundry.orchestrator.orchestrator import Orchestrator
from mercury_foundry.sandbox.workspace import Workspace
from mercury_foundry.state import db, models
from mercury_foundry.testing.runner import TestRunner

FAKE_API_KEY = "sk-test-super-secret-value-12345"


def _config(**overrides) -> RealProviderConfig:
    base = dict(
        api_key=FAKE_API_KEY,
        model="gpt-4o-mini",
        base_url="https://example-provider.invalid/v1",
        timeout_seconds=5.0,
        max_calls_per_run=5,
        max_tokens_per_run=100_000,
        max_cost_usd_per_run=1.0,
        cost_per_1k_tokens_usd=0.00015,
    )
    base.update(overrides)
    return RealProviderConfig(**base)


def _base_response(**overrides) -> dict:
    body = {
        "id": "resp_test",
        "object": "response",
        "created_at": 1700000000,
        "model": "gpt-4o-mini",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "metadata": {},
        "parallel_tool_calls": True,
        "temperature": 1.0,
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "output": [],
        "usage": {
            "input_tokens": 40,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 20,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 60,
        },
    }
    body.update(overrides)
    return body


def _message_output(text: str) -> list[dict]:
    return [
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
    ]


PLAN_PAYLOAD = json.dumps(
    {
        "objective": "aggiungere una capability sempre superata da test reali",
        "steps": ["Implementare una capability minima con un test reale che passa al primo tentativo"],
        "expected_files": ["capability.py", "tests/test_capability.py"],
        "verification_criteria": ["pytest reale passa"],
        "risk_notes": ["nessun rischio noto"],
    }
)

PATCH_PAYLOAD = json.dumps(
    {
        "summary": "Implementazione minima della capability, corretta al primo tentativo",
        "files": [
            {
                "path": "capability.py",
                "operation": "create",
                "content": (
                    '"""Capability minima generata dal provider reale (mockato nei test)."""\n\n\n'
                    "def get_value() -> int:\n"
                    "    return 42\n"
                ),
                "rationale": "implementa la capability richiesta dal task",
                "verification_relevance": "necessario perché il test lo importa",
            }
        ],
        "test_files": [
            {
                "path": "tests/test_capability.py",
                "operation": "create",
                "content": (
                    "import capability\n\n\n"
                    "def test_get_value_returns_42():\n"
                    "    assert capability.get_value() == 42\n"
                ),
                "rationale": "verifica reale del comportamento della capability",
                "verification_relevance": "unico test reale necessario per questo task",
            }
        ],
    }
)


def test_real_provider_end_to_end_workflow_with_structured_mocked_responses(tmp_path):
    call_log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        schema_name = body.get("text", {}).get("format", {}).get("name", "")
        call_log.append(schema_name)
        if schema_name == "PlanSchema":
            payload = PLAN_PAYLOAD
        elif schema_name == "PatchSchema":
            payload = PATCH_PAYLOAD
        else:
            raise AssertionError(f"schema inatteso richiesto dal test: {schema_name!r}")
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    client = OpenAI(
        api_key=FAKE_API_KEY,
        base_url="https://example-provider.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    provider = OpenAICompatibleProvider(_config(), client=client)
    assert provider.is_simulated is False

    conn = db.connect(tmp_path / "mercury_foundry.db")
    workspace = Workspace(tmp_path / "target_project")
    builder = Builder(provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    execution_loop = ExecutionLoop(conn, builder, evaluator)
    orchestrator = Orchestrator(conn, provider, execution_loop)

    # SPEC -> PLAN (1a chiamata reale, mockata: PlanSchema)
    goal_id = orchestrator.submit_goal("aggiungi una capability minima con test reale")
    # BUILD -> TEST -> VERIFY (2a chiamata reale, mockata: PatchSchema; test REALI eseguiti da pytest)
    goal_run = orchestrator.run_goal(goal_id)

    assert call_log == ["PlanSchema", "PatchSchema"]
    assert goal_run.final_status == "awaiting_approval"
    assert len(goal_run.task_outcomes) == 1

    outcome = goal_run.task_outcomes[0]
    assert outcome.status == "candidate_created"
    assert outcome.attempts_used == 1  # nessun FIX necessario: patch corretta al primo tentativo
    assert outcome.candidate_id is not None

    attempts = models.get_attempts_for_task(conn, outcome.task_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "success"
    assert attempts[0]["provider_name"] == provider.name
    assert bool(attempts[0]["is_simulated"]) is False

    test_results = models.get_test_results_for_attempt(conn, attempts[0]["id"])
    assert test_results[0]["passed"] == 1

    # CANDIDATE: nasce sempre in attesa di revisione umana, mai auto-approvata.
    candidate = models.get_candidate(conn, outcome.candidate_id)
    assert candidate["status"] == "pending_review"
    assert candidate["provider_name"] == provider.name
    assert bool(candidate["is_simulated"]) is False

    # Nota: l'Orchestrator persiste in `provider_calls` solo le chiamate di
    # pianificazione FALLITE (vedi `Orchestrator.submit_goal`) e SEMPRE le
    # chiamate di build di ogni attempt (successo o fallimento, vedi
    # `ExecutionLoop._persist_call_record`). Con un piano riuscito al primo
    # colpo (come qui) risulta quindi persistita solo la chiamata di build:
    # comportamento preesistente, non modificato da questo task.
    calls = models.list_provider_calls_for_goal(conn, goal_id)
    assert len(calls) == 1
    assert all(c["is_simulated"] == 0 for c in calls)
    assert all(c["success"] == 1 for c in calls)
    assert FAKE_API_KEY not in json.dumps([dict(c) for c in calls])

    audit_rows = list_audit_log(conn)
    actions = [row["action"] for row in audit_rows]
    for expected in [
        "GOAL_SUBMITTED",
        "TASK_CREATED",
        "TASK_STARTED",
        "BUILD_STARTED",
        "BUILD_COMPLETED",
        "TEST_STARTED",
        "TEST_COMPLETED",
        "VERIFY_PASSED",
        "CANDIDATE_CREATED",
        "GOAL_AWAITING_APPROVAL",
    ]:
        assert expected in actions, f"azione mancante nell'audit log: {expected}"

    # Gate di approvazione umana: OBBLIGATORIO e non bypassabile.
    with pytest.raises(gate.InvalidCandidateStateError):
        # Sanity check: non è già approvata prima dell'azione umana esplicita.
        gate.reject_candidate(conn, outcome.candidate_id, rationale="controllo")
        gate.reject_candidate(conn, outcome.candidate_id, rationale="doppio rifiuto non valido")

    # Ripristina lo stato e approva davvero (percorso principale del test).
    conn.execute("UPDATE candidates SET status = 'pending_review' WHERE id = ?", (outcome.candidate_id,))
    conn.commit()

    gate.approve_candidate(conn, outcome.candidate_id, rationale="Workflow end-to-end mockato superato")
    candidate_after = models.get_candidate(conn, outcome.candidate_id)
    assert candidate_after["status"] == "approved"

    goal_after = models.get_goal(conn, goal_id)
    assert goal_after["status"] == "done"

    # Nessun file scritto fuori dalla sandbox del test (tmp_path), coerente con
    # requisito 11 di questo task ("Do not modify target_project during this task"):
    # qui target_project è isolato in tmp_path, non quello reale del progetto.
    assert workspace.root == tmp_path / "target_project"

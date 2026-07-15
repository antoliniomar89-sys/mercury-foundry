"""Test dell'architettura di BUILD atomica introdotta da MF-FIX-003.

Regressione diretta del blocco osservato in MF-RUN-002: un piano con più
step veniva scomposto in N task indipendenti, ciascuno con il proprio ciclo
BUILD->TEST; un task che produceva solo il file applicativo (senza il file di
test) causava un TEST prematuro ("no tests ran"), e il retry di FIX era
bloccato dal budget di chiamate reali. Nessuno di questi test fa chiamate
reali al provider AI (solo `AIProvider` fake locali) e nessuno scrive fuori
da `tmp_path`.
"""

from __future__ import annotations

import pytest

from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal
from mercury_foundry.policy.errors import BuildIncompleteError
from mercury_foundry.policy.literal_constraints import (
    LiteralConstraints,
    compute_build_completeness,
)
from mercury_foundry.sandbox.workspace import Workspace


def _build_foundry_with_provider(tmp_path, provider):
    from mercury_foundry.agents.builder import Builder
    from mercury_foundry.agents.evaluator import Evaluator
    from mercury_foundry.execution.loop import ExecutionLoop
    from mercury_foundry.orchestrator.orchestrator import Orchestrator
    from mercury_foundry.state import db
    from mercury_foundry.testing.runner import TestRunner

    conn = db.connect(tmp_path / "mercury_foundry.db")
    workspace = Workspace(tmp_path / "target_project")
    builder = Builder(provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    execution_loop = ExecutionLoop(conn, builder, evaluator)
    orchestrator = Orchestrator(conn, provider, execution_loop)
    return conn, workspace, orchestrator


# --- aggregazione a livello di Orchestrator: piano multi-step -> un solo task ------


class _FragmentingProvider(AIProvider):
    """Simula esattamente il piano osservato nella run reale bloccata:
    un obiettivo "crea due file" scomposto in più step di piano."""

    name = "fragmenting-fake"
    is_simulated = True

    def propose_plan(self, goal_description: str) -> list[str]:
        return [
            "crea il file applicativo capability.py",
            "crea il file di test tests/test_capability.py",
        ]

    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        # Il provider, ricevendo la descrizione AGGREGATA (entrambi gli step
        # insieme), può produrre in un'unica proposta sia il file applicativo
        # sia il file di test — questo è esattamente il punto dell'aggregazione.
        return PatchProposal(
            summary="capability + test creati in un'unica BUILD atomica",
            files=[FileChange(path="capability.py", content="def get_value():\n    return 42\n")],
            test_files=[
                FileChange(
                    path="tests/test_capability.py",
                    content="import capability\n\n\ndef test_get_value():\n    assert capability.get_value() == 42\n",
                )
            ],
            provider_name=self.name,
            is_simulated=True,
        )


def test_multi_step_plan_is_aggregated_into_a_single_task(tmp_path):
    """Un piano con più step produce UN SOLO task (BUILD atomica), non N task
    indipendenti con N cicli BUILD->TEST separati."""
    from mercury_foundry.audit.logger import list_audit_log
    from mercury_foundry.state import models

    provider = _FragmentingProvider()
    conn, workspace, orchestrator = _build_foundry_with_provider(tmp_path, provider)

    goal_id = orchestrator.submit_goal("crea la capability con applicativo e test")
    tasks = models.get_tasks_for_goal(conn, goal_id)

    assert len(tasks) == 1
    assert "crea il file applicativo capability.py" in tasks[0]["description"]
    assert "crea il file di test tests/test_capability.py" in tasks[0]["description"]

    actions = [row["action"] for row in list_audit_log(conn)]
    assert "PLAN_STEPS_AGGREGATED_INTO_ATOMIC_BUILD" in actions


def test_two_mandatory_files_split_across_plan_steps_both_end_up_written(tmp_path):
    """Il caso end-to-end del bug originale: entrambi i file richiesti
    (uno per step di piano) vengono scritti da UNA SOLA BUILD atomica, e TEST
    parte solo dopo che entrambi esistono davvero."""
    provider = _FragmentingProvider()
    conn, workspace, orchestrator = _build_foundry_with_provider(tmp_path, provider)

    goal_id = orchestrator.submit_goal("crea la capability con applicativo e test")
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "awaiting_approval"
    assert len(goal_run.task_outcomes) == 1
    assert (workspace.root / "capability.py").exists()
    assert (workspace.root / "tests" / "test_capability.py").exists()


def test_single_step_plan_still_creates_exactly_one_task_unaggregated(tmp_path):
    """Comportamento storico invariato: un piano a un solo step non attiva
    l'aggregazione e non produce log di aggregazione."""
    from mercury_foundry.ai.fake_model import FakeModel
    from mercury_foundry.audit.logger import list_audit_log
    from mercury_foundry.state import models

    provider = FakeModel()
    conn, workspace, orchestrator = _build_foundry_with_provider(tmp_path, provider)

    goal_id = orchestrator.submit_goal("aggiungi una capability health check")
    tasks = models.get_tasks_for_goal(conn, goal_id)
    assert len(tasks) == 1

    actions = [row["action"] for row in list_audit_log(conn)]
    assert "PLAN_STEPS_AGGREGATED_INTO_ATOMIC_BUILD" not in actions


# --- gate di completezza della BUILD: nessun TEST prima che sia soddisfatto -------


def test_compute_build_completeness_flags_missing_required_file():
    constraints = LiteralConstraints(required_files=("a.py", "tests/test_a.py"))
    proposal = PatchProposal(
        summary="solo un file",
        files=[FileChange(path="a.py", content="x = 1\n")],
        test_files=[],
        provider_name="fake",
        is_simulated=True,
    )

    result = compute_build_completeness(proposal, constraints)

    assert result.complete is False
    assert "tests/test_a.py" in result.missing_files


def test_compute_build_completeness_passes_when_all_required_files_present():
    constraints = LiteralConstraints(required_files=("a.py", "tests/test_a.py"))
    proposal = PatchProposal(
        summary="entrambi i file",
        files=[FileChange(path="a.py", content="x = 1\n")],
        test_files=[FileChange(path="tests/test_a.py", content="def test_x():\n    assert True\n")],
        provider_name="fake",
        is_simulated=True,
    )

    result = compute_build_completeness(proposal, constraints)

    assert result.complete is True
    assert result.missing_files == []


def test_compute_build_completeness_flags_empty_proposal_even_without_constraints():
    proposal = PatchProposal(
        summary="niente da fare", files=[], test_files=[], provider_name="fake", is_simulated=True
    )

    result = compute_build_completeness(proposal, constraints=None)

    assert result.complete is False


class _MissingTestFileProvider(AIProvider):
    """Propone SOLO il file applicativo, mai il file di test richiesto — il
    caso esatto della run reale bloccata (task 1 del piano frammentato)."""

    name = "missing-test-file-fake"
    is_simulated = True

    def propose_plan(self, goal_description: str) -> list[str]:
        return ["crea solo il file applicativo"]

    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        return PatchProposal(
            summary="manca il file di test richiesto",
            files=[FileChange(path="capability.py", content="def get_value():\n    return 42\n")],
            test_files=[],
            provider_name=self.name,
            is_simulated=True,
        )


def test_build_incomplete_blocks_before_test_starts_and_writes_nothing(tmp_path):
    """Se manca un `required_files`, il task si blocca PRIMA che TEST_STARTED
    venga anche solo loggato, e nessun file viene scritto in sandbox — non
    lasciando che pytest riporti fuorviantemente "no tests ran"."""
    from mercury_foundry.audit.logger import list_audit_log
    from mercury_foundry.state import models

    constraints = LiteralConstraints(required_files=("capability.py", "tests/test_capability.py"))
    provider = _MissingTestFileProvider()
    conn, workspace, orchestrator = _build_foundry_with_provider(tmp_path, provider)

    goal_id = orchestrator.submit_goal("crea la capability", literal_constraints=constraints)
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "blocked"
    outcome = goal_run.task_outcomes[0]
    assert outcome.status == "blocked"
    assert outcome.attempts_used == 1  # blocco immediato, nessun retry consumato

    # Fail-closed reale: nessun file scritto nella sandbox.
    assert list(workspace.root.glob("**/*")) == []

    actions = [row["action"] for row in list_audit_log(conn)]
    assert "BUILD_INCOMPLETE_BLOCKED" in actions
    assert "TASK_BLOCKED" in actions
    assert "TEST_STARTED" not in actions

    task = models.get_task(conn, outcome.task_id)
    assert task["status"] == "blocked"


# --- scrittura atomica: rollback su fallimento a metà batch -----------------------


def test_write_files_atomic_rolls_back_earlier_writes_on_later_failure(tmp_path):
    """Se la seconda scrittura di un batch fallisce (qui: violazione di
    sandbox), la prima scrittura già eseguita in questa chiamata deve essere
    ripristinata al suo stato precedente, non lasciata come stato parziale."""
    workspace = Workspace(tmp_path / "target_project")
    (workspace.root / "existing.txt").write_text("contenuto originale\n")

    with pytest.raises(Exception):
        workspace.write_files_atomic(
            [
                ("existing.txt", "contenuto nuovo che deve essere annullato\n"),
                ("../outside_sandbox.txt", "questo fallisce\n"),
            ]
        )

    assert (workspace.root / "existing.txt").read_text() == "contenuto originale\n"


def test_write_files_atomic_removes_newly_created_file_on_rollback(tmp_path):
    """Un file che NON esisteva prima del batch, scritto con successo ma poi
    annullato da un fallimento successivo nello stesso batch, deve essere
    rimosso (non lasciato creato a metà)."""
    workspace = Workspace(tmp_path / "target_project")

    with pytest.raises(Exception):
        workspace.write_files_atomic(
            [
                ("brand_new.txt", "contenuto\n"),
                ("../outside_sandbox.txt", "questo fallisce\n"),
            ]
        )

    assert not (workspace.root / "brand_new.txt").exists()


def test_write_files_atomic_succeeds_when_whole_batch_is_valid(tmp_path):
    workspace = Workspace(tmp_path / "target_project")

    records = workspace.write_files_atomic(
        [("a.py", "x = 1\n"), ("tests/test_a.py", "def test_x():\n    assert True\n")]
    )

    assert len(records) == 2
    assert (workspace.root / "a.py").read_text() == "x = 1\n"
    assert (workspace.root / "tests" / "test_a.py").read_text() == "def test_x():\n    assert True\n"


# --- required_files nel JSON roundtrip e comportamento di default -----------------


def test_required_files_json_roundtrip():
    original = LiteralConstraints(required_files=("a.py", "b.py"))
    restored = LiteralConstraints.from_json(original.to_json())
    assert restored.required_files == ("a.py", "b.py")


# --- dimostrazione end-to-end con provider reale mockato: piano a 2 step, -----
# --- ESATTAMENTE 2 chiamate reali totali (PLAN + un'unica PATCH aggregata) ----


def test_real_provider_two_step_plan_still_uses_exactly_two_provider_calls(tmp_path):
    """Anche quando il provider REALE (qui mockato via httpx.MockTransport,
    stesso pattern degli altri test di questo modulo su OpenAICompatibleProvider)
    propone un piano a 2 step, l'aggregazione in un'unica BUILD atomica fa sì
    che il ciclo completo SPEC->PLAN->BUILD->TEST->VERIFY->CANDIDATE consumi
    ESATTAMENTE 2 chiamate reali (1 PLAN + 1 PATCH), non una PATCH per step."""
    import json

    import httpx
    from openai import OpenAI

    from mercury_foundry.agents.builder import Builder
    from mercury_foundry.agents.evaluator import Evaluator
    from mercury_foundry.ai.provider_config import RealProviderConfig
    from mercury_foundry.ai.real_provider import OpenAICompatibleProvider
    from mercury_foundry.execution.loop import ExecutionLoop
    from mercury_foundry.orchestrator.orchestrator import Orchestrator
    from mercury_foundry.state import db, models
    from mercury_foundry.testing.runner import TestRunner

    fake_api_key = "sk-test-super-secret-value-12345"

    plan_payload = json.dumps(
        {
            "objective": "creare capability + test in due step di piano",
            "steps": [
                "crea il file applicativo capability.py",
                "crea il file di test tests/test_capability.py",
            ],
            "expected_files": ["capability.py", "tests/test_capability.py"],
            "verification_criteria": ["pytest reale passa"],
            "risk_notes": ["nessuno"],
        }
    )
    patch_payload = json.dumps(
        {
            "summary": "capability + test creati in un'unica BUILD atomica aggregata",
            "files": [
                {
                    "path": "capability.py",
                    "operation": "create",
                    "content": "def get_value():\n    return 42\n",
                    "rationale": "implementa la capability richiesta da entrambi gli step",
                    "verification_relevance": "necessario perché il test lo importa",
                }
            ],
            "test_files": [
                {
                    "path": "tests/test_capability.py",
                    "operation": "create",
                    "content": (
                        "import capability\n\n\n"
                        "def test_get_value():\n"
                        "    assert capability.get_value() == 42\n"
                    ),
                    "rationale": "verifica reale della capability",
                    "verification_relevance": "unico test reale necessario",
                }
            ],
        }
    )

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

    call_log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        schema_name = body.get("text", {}).get("format", {}).get("name", "")
        call_log.append(schema_name)
        payload = plan_payload if schema_name == "PlanSchema" else patch_payload
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    client = OpenAI(
        api_key=fake_api_key,
        base_url="https://example-provider.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    config = RealProviderConfig(
        api_key=fake_api_key,
        model="gpt-4o-mini",
        base_url="https://example-provider.invalid/v1",
        timeout_seconds=5.0,
        max_calls_per_run=2,  # stesso budget usato nella run reale controllata
        max_tokens_per_run=100_000,
        max_cost_usd_per_run=1.0,
        cost_per_1k_tokens_usd=0.00015,
    )
    provider = OpenAICompatibleProvider(config, client=client)

    conn = db.connect(tmp_path / "mercury_foundry.db")
    workspace = Workspace(tmp_path / "target_project")
    builder = Builder(provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    execution_loop = ExecutionLoop(conn, builder, evaluator)
    orchestrator = Orchestrator(conn, provider, execution_loop)

    goal_id = orchestrator.submit_goal("crea la capability con applicativo e test")
    goal_run = orchestrator.run_goal(goal_id)

    assert call_log == ["PlanSchema", "PatchSchema"]  # esattamente 2 chiamate, mai una per step
    assert goal_run.final_status == "awaiting_approval"
    assert len(goal_run.task_outcomes) == 1

    calls = models.list_provider_calls_for_goal(conn, goal_id)
    assert len(calls) == 2
    assert [c["operation"] for c in calls] == ["PLAN", "PATCH"]


def test_required_files_defaults_to_none_and_does_not_block_existing_goals():
    """Nessun goal preesistente (senza `required_files`) viene bloccato dal
    nuovo gate: il campo è opt-in, non retroattivo."""
    constraints = LiteralConstraints(exact_file_path=None, exact_file_content=None)
    proposal = PatchProposal(
        summary="qualunque cosa",
        files=[FileChange(path="qualsiasi.py", content="x = 1\n")],
        test_files=[],
        provider_name="fake",
        is_simulated=True,
    )
    result = compute_build_completeness(proposal, constraints)
    assert result.complete is True

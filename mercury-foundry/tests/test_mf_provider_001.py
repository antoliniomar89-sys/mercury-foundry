"""Test minimi MF-PROVIDER-001 — Real AI Provider Vertical Slice.

8 test mirati sui file modificati da questo task, senza chiamate di rete reali.
Ogni test è etichettato con il requisito che copre.

Nessun test qui esegue una chiamata API reale: il client `openai` usa sempre
`httpx.MockTransport` oppure il provider è FakeModel (zero rete).
"""

from __future__ import annotations

import json

import httpx
import pytest
from openai import OpenAI

from mercury_foundry.ai.fake_model import FakeModel
from mercury_foundry.ai.provider_config import RealProviderConfig, load_real_provider_config
from mercury_foundry.ai.provider_factory import (
    ProviderUnavailableError,
    get_provider,
    list_available_providers,
    resolve_provider_name,
)
from mercury_foundry.ai.real_provider import OpenAICompatibleProvider

# Chiave fittizia usata ESCLUSIVAMENTE nei test: non è mai una credenziale reale.
_FAKE_KEY = "sk-mfprovider001-test-key-never-real"


# ---------------------------------------------------------------------------
# Helpers condivisi
# ---------------------------------------------------------------------------

def _test_config(**overrides) -> RealProviderConfig:
    base = dict(
        api_key=_FAKE_KEY,
        model="test-model",
        base_url="https://invalid.example/v1",
        timeout_seconds=5.0,
        max_calls_per_run=5,
        max_tokens_per_run=10_000,
        max_cost_usd_per_run=1.0,
        cost_per_1k_tokens_usd=0.01,
    )
    base.update(overrides)
    return RealProviderConfig(**base)


def _mock_client(handler) -> OpenAI:
    """Client openai con trasporto HTTP fittizio — nessuna rete reale."""
    return OpenAI(
        api_key=_FAKE_KEY,
        base_url="https://invalid.example/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _ok_response(payload: str) -> dict:
    """Risposta Responses API simulata, conforme allo schema openai."""
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1700000000,
        "model": "test-model",
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
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": payload, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 5,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 15,
        },
    }


_PLAN_PAYLOAD = json.dumps({
    "objective": "implementare una capability minima",
    "steps": ["crea capability.py con get_value()"],
    "expected_files": ["capability.py"],
    "verification_criteria": ["pytest passa"],
    "risk_notes": [],
})

_PATCH_PAYLOAD = json.dumps({
    "summary": "patch minima — test MF-PROVIDER-001",
    "files": [{
        "path": "capability.py",
        "operation": "create",
        "content": "def get_value():\n    return 1\n",
        "rationale": "implementa la capability",
        "verification_relevance": "necessario per il test",
    }],
    "test_files": [{
        "path": "tests/test_cap.py",
        "operation": "create",
        "content": "import capability\n\ndef test_val():\n    assert capability.get_value() == 1\n",
        "rationale": "verifica reale",
        "verification_relevance": "unico test",
    }],
})


# ---------------------------------------------------------------------------
# Test 1 — provider fake selezionato correttamente
# ---------------------------------------------------------------------------

def test_1_fake_provider_selected_and_is_simulated(monkeypatch):
    """MERCURY_AI_PROVIDER=fake produce FakeModel.is_simulated=True."""
    monkeypatch.setenv("MERCURY_AI_PROVIDER", "fake")
    provider = get_provider()
    assert isinstance(provider, FakeModel)
    assert provider.is_simulated is True
    assert provider.name == "fake-deterministic"
    assert provider.last_call_record is None


# ---------------------------------------------------------------------------
# Test 2 — provider reale selezionato tramite environment (openai_compatible)
# ---------------------------------------------------------------------------

def test_2_openai_compatible_selected_via_env_var(monkeypatch):
    """MERCURY_AI_PROVIDER=openai_compatible istanzia OpenAICompatibleProvider."""
    monkeypatch.setenv("MERCURY_AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MERCURY_AI_API_KEY", _FAKE_KEY)
    monkeypatch.setenv("MERCURY_AI_MODEL", "test-model")
    monkeypatch.setenv("MERCURY_AI_BASE_URL", "https://invalid.example/v1")
    monkeypatch.setenv("MERCURY_AI_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("MERCURY_AI_MAX_CALLS_PER_RUN", "3")
    monkeypatch.setenv("MERCURY_AI_MAX_TOKENS_PER_RUN", "1000")
    monkeypatch.setenv("MERCURY_AI_MAX_COST_USD_PER_RUN", "1.0")

    provider = get_provider()  # legge MERCURY_AI_PROVIDER dall'env
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.is_simulated is False
    assert "test-model" in provider.name


def test_2b_openai_compatible_resolve_name():
    """'openai_compatible' è riconosciuto come nome valido nel registro."""
    assert "openai_compatible" in list_available_providers()
    assert resolve_provider_name("openai_compatible") == "openai_compatible"


def test_2c_mercury_ai_base_url_short_name_accepted(monkeypatch):
    """MERCURY_AI_BASE_URL (nome primario MF-PROVIDER-001) è accettato da load_real_provider_config."""
    env = {
        "MERCURY_AI_API_KEY": _FAKE_KEY,
        "MERCURY_AI_MODEL": "test-model",
        "MERCURY_AI_BASE_URL": "https://invalid.example/v1",   # nome primario, senza _API_
        "MERCURY_AI_TIMEOUT_SECONDS": "5",
        "MERCURY_AI_MAX_CALLS_PER_RUN": "3",
        "MERCURY_AI_MAX_TOKENS_PER_RUN": "1000",
        "MERCURY_AI_MAX_COST_USD_PER_RUN": "1.0",
    }
    config = load_real_provider_config(env)
    assert config.base_url == "https://invalid.example/v1"
    assert config.api_key == _FAKE_KEY


def test_2d_mercury_ai_api_base_url_legacy_still_accepted():
    """MERCURY_AI_API_BASE_URL (alias retrocompatibile) continua a funzionare."""
    env = {
        "MERCURY_AI_API_KEY": _FAKE_KEY,
        "MERCURY_AI_MODEL": "test-model",
        "MERCURY_AI_API_BASE_URL": "https://legacy.example/v1",  # alias legacy
        "MERCURY_AI_TIMEOUT_SECONDS": "5",
        "MERCURY_AI_MAX_CALLS_PER_RUN": "3",
        "MERCURY_AI_MAX_TOKENS_PER_RUN": "1000",
        "MERCURY_AI_MAX_COST_USD_PER_RUN": "1.0",
    }
    config = load_real_provider_config(env)
    assert config.base_url == "https://legacy.example/v1"


def test_2e_short_base_url_takes_priority_over_legacy():
    """MERCURY_AI_BASE_URL ha priorità su MERCURY_AI_API_BASE_URL quando entrambi presenti."""
    env = {
        "MERCURY_AI_API_KEY": _FAKE_KEY,
        "MERCURY_AI_MODEL": "test-model",
        "MERCURY_AI_BASE_URL": "https://primary.example/v1",
        "MERCURY_AI_API_BASE_URL": "https://legacy.example/v1",
        "MERCURY_AI_TIMEOUT_SECONDS": "5",
        "MERCURY_AI_MAX_CALLS_PER_RUN": "3",
        "MERCURY_AI_MAX_TOKENS_PER_RUN": "1000",
        "MERCURY_AI_MAX_COST_USD_PER_RUN": "1.0",
    }
    config = load_real_provider_config(env)
    assert config.base_url == "https://primary.example/v1"


# ---------------------------------------------------------------------------
# Test 3 — API key mancante produce errore esplicito (fail-closed)
# ---------------------------------------------------------------------------

def test_3_missing_api_key_raises_unavailable_error(monkeypatch):
    """openai_compatible senza MERCURY_AI_API_KEY → ProviderUnavailableError esplicito."""
    for var in [
        "MERCURY_AI_API_KEY", "MERCURY_AI_MODEL",
        "MERCURY_AI_BASE_URL", "MERCURY_AI_API_BASE_URL",
        "MERCURY_AI_TIMEOUT_SECONDS", "MERCURY_AI_MAX_CALLS_PER_RUN",
        "MERCURY_AI_MAX_TOKENS_PER_RUN", "MERCURY_AI_MAX_COST_USD_PER_RUN",
    ]:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ProviderUnavailableError) as exc_info:
        get_provider("openai_compatible")

    message = str(exc_info.value)
    # Il messaggio deve essere esplicito sul problema
    assert "openai_compatible" in message or "configurab" in message.lower()
    # Non deve contenere segreti (nessun segreto era impostato, ma verifichiamo il pattern)
    assert _FAKE_KEY not in message


def test_3b_missing_base_url_both_names_raises_error():
    """Nessuna base URL (né primaria né legacy) → ProviderConfigError esplicito."""
    from mercury_foundry.ai.provider_config import ProviderConfigError, missing_required_env_vars

    env = {
        "MERCURY_AI_API_KEY": _FAKE_KEY,
        "MERCURY_AI_MODEL": "test-model",
        # né MERCURY_AI_BASE_URL né MERCURY_AI_API_BASE_URL
        "MERCURY_AI_TIMEOUT_SECONDS": "5",
        "MERCURY_AI_MAX_CALLS_PER_RUN": "3",
        "MERCURY_AI_MAX_TOKENS_PER_RUN": "1000",
        "MERCURY_AI_MAX_COST_USD_PER_RUN": "1.0",
    }
    missing = missing_required_env_vars(env)
    assert "MERCURY_AI_API_BASE_URL" in missing

    with pytest.raises(ProviderConfigError):
        load_real_provider_config(env)


# ---------------------------------------------------------------------------
# Test 4 — risposta HTTP valida convertita nel formato atteso
# ---------------------------------------------------------------------------

def test_4_valid_http_response_converted_to_plan():
    """Una risposta HTTP 200 valida produce list[str] di step per propose_plan."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_response(_PLAN_PAYLOAD))

    provider = OpenAICompatibleProvider(_test_config(), client=_mock_client(handler))
    plan = provider.propose_plan("implementa una capability")

    assert plan == ["crea capability.py con get_value()"]
    assert provider.last_call_record is not None
    assert provider.last_call_record.success is True
    assert provider.last_call_record.is_simulated is False
    assert provider.last_call_record.operation == "PLAN"


def test_4b_valid_http_response_converted_to_patch():
    """Una risposta HTTP 200 valida produce PatchProposal per propose_patch."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_response(_PATCH_PAYLOAD))

    provider = OpenAICompatibleProvider(_test_config(), client=_mock_client(handler))
    proposal = provider.propose_patch("crea capability", {"attempt_number": 1})

    assert proposal.summary == "patch minima — test MF-PROVIDER-001"
    assert proposal.is_simulated is False
    assert [f.path for f in proposal.files] == ["capability.py"]
    assert [f.path for f in proposal.test_files] == ["tests/test_cap.py"]
    assert provider.last_call_record.success is True
    assert provider.last_call_record.operation == "PATCH"


# ---------------------------------------------------------------------------
# Test 5 — timeout/HTTP error propagato correttamente
# ---------------------------------------------------------------------------

def test_5_timeout_propagated_as_provider_timeout_error():
    """Un timeout HTTP → ProviderTimeoutError, not silenziosamente gestito."""
    from mercury_foundry.ai.errors import ProviderTimeoutError

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=req)

    provider = OpenAICompatibleProvider(_test_config(), client=_mock_client(handler))

    with pytest.raises(ProviderTimeoutError):
        provider.propose_plan("qualsiasi obiettivo")

    assert provider.last_call_record is not None
    assert provider.last_call_record.success is False


def test_5b_http_500_propagated_as_malformed_response_error():
    """Un errore HTTP 500 → ProviderMalformedResponseError, non ignorato."""
    from mercury_foundry.ai.errors import ProviderMalformedResponseError

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "internal", "type": "server_error"}})

    provider = OpenAICompatibleProvider(_test_config(), client=_mock_client(handler))

    with pytest.raises(ProviderMalformedResponseError):
        provider.propose_plan("qualsiasi obiettivo")

    assert provider.last_call_record.success is False


# ---------------------------------------------------------------------------
# Test 6 — API key mai nei log / error_summary
# ---------------------------------------------------------------------------

def test_6_api_key_never_in_error_summary_on_failure():
    """La API key non deve mai comparire in error_summary, anche se il provider
    la include nella risposta di errore."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"message": f"auth failed, key={_FAKE_KEY}", "type": "server_error"}},
        )

    provider = OpenAICompatibleProvider(_test_config(), client=_mock_client(handler))
    with pytest.raises(Exception):
        provider.propose_plan("qualsiasi obiettivo")

    record = provider.last_call_record
    assert record is not None
    assert _FAKE_KEY not in (record.error_summary or "")


def test_6b_api_key_never_in_error_on_refusal():
    """La API key non deve mai comparire nell'eccezione sollevata da un refusal."""
    from mercury_foundry.ai.errors import ProviderRefusalError

    refusal_output = [{
        "id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "refusal", "refusal": f"non posso (key={_FAKE_KEY})"}],
    }]

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_response("") | {"output": refusal_output})

    provider = OpenAICompatibleProvider(_test_config(), client=_mock_client(handler))
    with pytest.raises(ProviderRefusalError) as exc_info:
        provider.propose_plan("qualsiasi obiettivo")

    assert _FAKE_KEY not in str(exc_info.value)
    assert _FAKE_KEY not in (provider.last_call_record.error_summary or "")


# ---------------------------------------------------------------------------
# Test 7 — ExecutionLoop continua a funzionare con FakeModel (invariato)
# ---------------------------------------------------------------------------

def test_7_execution_loop_still_works_with_fake_model(tmp_path):
    """Il comportamento con FakeModel non è stato alterato da MF-PROVIDER-001."""
    from mercury_foundry.state import models
    from mercury_foundry.wiring import build_foundry

    foundry = build_foundry(
        db_path=tmp_path / "mf.db",
        sandbox_root=tmp_path / "target",
        provider_name="fake",
    )
    goal_id = foundry.orchestrator.submit_goal("aggiungi una capability health check")
    goal_run = foundry.orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "awaiting_approval"
    assert len(goal_run.task_outcomes) >= 1

    outcome = goal_run.task_outcomes[0]
    assert outcome.status == "candidate_created"
    assert outcome.candidate_id is not None

    candidate = models.get_candidate(foundry.conn, outcome.candidate_id)
    assert candidate["status"] == "pending_review"
    assert bool(candidate["is_simulated"]) is True
    assert candidate["provider_name"] == "fake-deterministic"


# ---------------------------------------------------------------------------
# Test 8 — ExecutionLoop accetta provider reale con risposta HTTP mockata
# ---------------------------------------------------------------------------

def test_8_execution_loop_accepts_real_provider_with_mocked_http(tmp_path):
    """L'intero ciclo SPEC→PLAN→BUILD→TEST→CANDIDATE funziona con
    OpenAICompatibleProvider e risposte Structured Outputs mockate (zero rete).
    """
    from mercury_foundry.agents.builder import Builder
    from mercury_foundry.agents.evaluator import Evaluator
    from mercury_foundry.execution.loop import ExecutionLoop
    from mercury_foundry.orchestrator.orchestrator import Orchestrator
    from mercury_foundry.sandbox.workspace import Workspace
    from mercury_foundry.state import db, models
    from mercury_foundry.testing.runner import TestRunner

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode())
        schema_name = body.get("text", {}).get("format", {}).get("name", "")
        if schema_name == "PlanSchema":
            return httpx.Response(200, json=_ok_response(_PLAN_PAYLOAD))
        if schema_name == "PatchSchema":
            return httpx.Response(200, json=_ok_response(_PATCH_PAYLOAD))
        raise AssertionError(f"schema inatteso: {schema_name!r}")

    client = OpenAI(
        api_key=_FAKE_KEY,
        base_url="https://invalid.example/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    provider = OpenAICompatibleProvider(_test_config(), client=client)
    assert provider.is_simulated is False

    conn = db.connect(tmp_path / "mf.db")
    workspace = Workspace(tmp_path / "target")
    builder = Builder(provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    loop = ExecutionLoop(conn, builder, evaluator, staging_base_dir=tmp_path / "staging")
    orchestrator = Orchestrator(conn, provider, loop)

    goal_id = orchestrator.submit_goal("implementa capability minima")
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "awaiting_approval"
    outcome = goal_run.task_outcomes[0]
    assert outcome.status == "candidate_created"

    candidate = models.get_candidate(conn, outcome.candidate_id)
    assert candidate["status"] == "pending_review"
    assert bool(candidate["is_simulated"]) is False
    assert provider.name in candidate["provider_name"]

    # Nessun segreto nei record persistiti
    calls = models.list_provider_calls_for_goal(conn, goal_id)
    all_data = json.dumps([dict(c) for c in calls])
    assert _FAKE_KEY not in all_data
    assert len(calls) == 2  # PLAN + PATCH

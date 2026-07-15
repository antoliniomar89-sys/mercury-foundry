"""Test del provider AI reale (OpenAI-compatibile) — SOLO con client `openai` mockato.

Nessuno di questi test esegue una chiamata di rete reale: il client `openai` è
sempre costruito con un `httpx.MockTransport` iniettato (stesso pattern di
`test_check_provider_structured_output.py`), così la vera logica di parsing
Structured Outputs dell'SDK ufficiale viene esercitata per `propose_plan` e
`propose_patch`, non solo una funzione mock nostra. Verificano budget, blocco
automatico, redazione dei segreti e persistenza dei metadata del provider.
"""

from __future__ import annotations

import json

import httpx
import pytest
from openai import OpenAI

from mercury_foundry.ai.errors import (
    ProviderCallLimitExceededError,
    ProviderCostBudgetExceededError,
    ProviderIncompleteResponseError,
    ProviderMalformedResponseError,
    ProviderRefusalError,
    ProviderTimeoutError,
    ProviderUnknownModelError,
    ProviderUnsafePatchError,
    ProviderUsageBudgetExceededError,
)
from mercury_foundry.ai.provider_config import (
    ProviderConfigError,
    RealProviderConfig,
    load_real_provider_config,
    missing_required_env_vars,
)
from mercury_foundry.ai.real_provider import OpenAICompatibleProvider

FAKE_API_KEY = "sk-test-super-secret-value-12345"


def _config(**overrides) -> RealProviderConfig:
    base = dict(
        api_key=FAKE_API_KEY,
        model="test-model",
        base_url="https://example-provider.invalid/v1",
        timeout_seconds=5.0,
        max_calls_per_run=3,
        max_tokens_per_run=1000,
        max_cost_usd_per_run=1.0,
        cost_per_1k_tokens_usd=0.01,
    )
    base.update(overrides)
    return RealProviderConfig(**base)


def _mock_client(handler) -> OpenAI:
    """Costruisce un client `openai` reale con trasporto HTTP fittizio.

    Il client e tutta la logica di parsing della SDK ufficiale eseguono
    davvero; solo il livello di trasporto HTTP è sostituito.
    """
    return OpenAI(
        api_key=FAKE_API_KEY,
        base_url="https://example-provider.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _base_response(**overrides) -> dict:
    body = {
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
        "output": [],
        "usage": {
            "input_tokens": 20,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 8,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 28,
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


def _refusal_output(refusal_text: str) -> list[dict]:
    return [
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "refusal", "refusal": refusal_text}],
        }
    ]


def _plan_payload(**overrides) -> str:
    payload = {
        "objective": "aggiungere una capability di test",
        "steps": ["task di test"],
        "expected_files": ["capability.py"],
        "verification_criteria": ["i test reali passano"],
        "risk_notes": ["nessun rischio noto"],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _patch_payload(**overrides) -> str:
    payload = {
        "summary": "patch di test",
        "files": [
            {
                "path": "capability.py",
                "operation": "create",
                "content": "def f():\n    return 1\n",
                "rationale": "implementa la capability richiesta",
                "verification_relevance": "necessario perché il test lo importa",
            }
        ],
        "test_files": [
            {
                "path": "tests/test_capability.py",
                "operation": "create",
                "content": "import capability\n\n\ndef test_f():\n    assert capability.f() == 1\n",
                "rationale": "verifica il comportamento della capability",
                "verification_relevance": "unico test reale per questo task",
            }
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


# --- configurazione ----------------------------------------------------------------


def test_missing_env_vars_are_reported_without_leaking_values(monkeypatch):
    env = {}
    missing = missing_required_env_vars(env)
    assert "MERCURY_AI_API_KEY" in missing
    assert "MERCURY_AI_MODEL" in missing

    with pytest.raises(ProviderConfigError) as exc_info:
        load_real_provider_config(env)
    message = str(exc_info.value)
    assert "MERCURY_AI_API_KEY" in message
    # Il messaggio elenca solo i NOMI delle variabili mancanti, non valori.


def test_complete_env_config_loads_successfully():
    env = {
        "MERCURY_AI_API_KEY": FAKE_API_KEY,
        "MERCURY_AI_MODEL": "test-model",
        "MERCURY_AI_API_BASE_URL": "https://example-provider.invalid/v1",
        "MERCURY_AI_TIMEOUT_SECONDS": "10",
        "MERCURY_AI_MAX_CALLS_PER_RUN": "5",
        "MERCURY_AI_MAX_TOKENS_PER_RUN": "2000",
        "MERCURY_AI_MAX_COST_USD_PER_RUN": "0.50",
    }
    config = load_real_provider_config(env)
    assert config.api_key == FAKE_API_KEY
    assert config.model == "test-model"
    assert config.max_calls_per_run == 5

    redacted = config.redacted_dict()
    assert FAKE_API_KEY not in str(redacted)
    assert redacted["api_key"] == "***configurata***"


# --- propose_plan: chiamata riuscita e metadata -------------------------------------


def test_valid_structured_plan_returns_steps_and_records_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(_plan_payload())))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    plan = provider.propose_plan("aggiungi una capability di test")

    assert plan == ["task di test"]

    record = provider.last_call_record
    assert record is not None
    assert record.success is True
    assert record.is_simulated is False
    assert record.model == "test-model"
    assert record.call_number == 1
    assert record.usage["total_tokens"] == 28
    assert record.estimated_cost_usd == pytest.approx((28 / 1000.0) * 0.01)
    assert record.error_summary is None


def test_refusal_during_planning_raises_and_records_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_base_response(output=_refusal_output(f"non posso farlo (key={FAKE_API_KEY})"))
        )

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderRefusalError):
        provider.propose_plan("obiettivo qualsiasi")

    record = provider.last_call_record
    assert record.success is False
    assert FAKE_API_KEY not in record.error_summary


def test_incomplete_structured_plan_raises_provider_incomplete_response_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_base_response(status="incomplete", incomplete_details={"reason": "max_output_tokens"}, output=[]),
        )

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderIncompleteResponseError):
        provider.propose_plan("obiettivo qualsiasi")

    assert provider.last_call_record.success is False


def test_plan_with_empty_steps_raises_provider_incomplete_response_error():
    """Uno schema tecnicamente valido ma con `steps` vuoto è comunque un piano
    inutilizzabile: bloccato fail-closed, non silenziosamente accettato."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(_plan_payload(steps=[]))))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderIncompleteResponseError):
        provider.propose_plan("obiettivo qualsiasi")


def test_non_conforming_plan_content_raises_malformed_response_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output("questo non e' JSON valido")))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderMalformedResponseError):
        provider.propose_plan("obiettivo qualsiasi")


def test_unknown_model_error_from_provider_is_translated_for_plan():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "message": f"The model does not exist (key={FAKE_API_KEY})",
                    "code": "model_not_found",
                }
            },
        )

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderUnknownModelError) as exc_info:
        provider.propose_plan("obiettivo qualsiasi")

    assert FAKE_API_KEY not in str(exc_info.value)
    assert FAKE_API_KEY not in provider.last_call_record.error_summary


def test_call_limit_exceeded_blocks_further_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(_plan_payload())))

    provider = OpenAICompatibleProvider(_config(max_calls_per_run=1), client=_mock_client(handler))
    provider.propose_plan("primo obiettivo")  # consuma l'unica chiamata concessa

    with pytest.raises(ProviderCallLimitExceededError):
        provider.propose_plan("secondo obiettivo")


def test_usage_budget_exceeded_blocks_and_records_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_base_response(
                output=_message_output(_plan_payload()),
                usage={
                    "input_tokens": 4000,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "output_tokens": 1000,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": 5000,
                },
            ),
        )

    provider = OpenAICompatibleProvider(
        _config(max_tokens_per_run=100, max_calls_per_run=5), client=_mock_client(handler)
    )
    with pytest.raises(ProviderUsageBudgetExceededError):
        provider.propose_plan("obiettivo che consuma troppi token")

    assert provider.last_call_record.success is False


def test_cost_budget_exceeded_blocks_and_records_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(_plan_payload())))

    provider = OpenAICompatibleProvider(
        _config(max_cost_usd_per_run=0.0001, max_tokens_per_run=100_000, cost_per_1k_tokens_usd=1.0),
        client=_mock_client(handler),
    )
    with pytest.raises(ProviderCostBudgetExceededError):
        provider.propose_plan("obiettivo costoso")

    assert provider.last_call_record.success is False


# --- propose_patch: chiamata riuscita e validazione strutturale ---------------------


def test_valid_structured_patch_returns_patch_proposal_and_records_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(_patch_payload())))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    proposal = provider.propose_patch("implementa la capability", {"attempt_number": 1, "previous_failure": None})

    assert proposal.summary == "patch di test"
    assert proposal.is_simulated is False
    assert [f.path for f in proposal.files] == ["capability.py"]
    assert [f.path for f in proposal.test_files] == ["tests/test_capability.py"]
    assert proposal.files[0].content == "def f():\n    return 1\n"

    record = provider.last_call_record
    assert record.success is True
    assert record.is_simulated is False


def test_malformed_patch_missing_content_for_create_is_rejected():
    payload = _patch_payload(
        files=[
            {
                "path": "capability.py",
                "operation": "create",
                "content": None,
                "rationale": "manca il contenuto",
                "verification_relevance": "n/a",
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderUnsafePatchError):
        provider.propose_patch("implementa la capability", {"attempt_number": 1, "previous_failure": None})


def test_unsafe_delete_operation_is_rejected_not_ignored():
    payload = _patch_payload(
        files=[
            {
                "path": "capability.py",
                "operation": "delete",
                "content": None,
                "rationale": "non serve più",
                "verification_relevance": "n/a",
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderUnsafePatchError):
        provider.propose_patch("implementa la capability", {"attempt_number": 1, "previous_failure": None})


def test_path_traversal_in_patch_is_rejected():
    payload = _patch_payload(
        files=[
            {
                "path": "../outside_sandbox.py",
                "operation": "create",
                "content": "x = 1\n",
                "rationale": "tentativo di uscire dalla sandbox",
                "verification_relevance": "n/a",
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderUnsafePatchError):
        provider.propose_patch("implementa la capability", {"attempt_number": 1, "previous_failure": None})


def test_absolute_path_in_patch_is_rejected():
    payload = _patch_payload(
        files=[
            {
                "path": "/etc/passwd",
                "operation": "update",
                "content": "x = 1\n",
                "rationale": "tentativo di path assoluto",
                "verification_relevance": "n/a",
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderUnsafePatchError):
        provider.propose_patch("implementa la capability", {"attempt_number": 1, "previous_failure": None})


def test_multiple_file_patch_rejected_when_spec_permits_only_one_file():
    """Il probe reale ('crea esattamente un file') deve poter richiedere max_files=1
    e vedere rifiutata una patch multi-file, invece di applicarla parzialmente."""
    payload = _patch_payload(
        test_files=[
            {
                "path": "tests/test_capability.py",
                "operation": "create",
                "content": "def test_x():\n    assert True\n",
                "rationale": "test aggiuntivo",
                "verification_relevance": "verifica extra",
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderUnsafePatchError):
        provider.propose_patch(
            "crea esattamente un file",
            {"attempt_number": 1, "previous_failure": None, "max_files": 1},
        )


def test_patch_within_max_files_limit_is_accepted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_base_response(
                output=_message_output(_patch_payload(test_files=[]))
            ),
        )

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    proposal = provider.propose_patch(
        "crea esattamente un file",
        {"attempt_number": 1, "previous_failure": None, "max_files": 1},
    )
    assert len(proposal.files) == 1
    assert len(proposal.test_files) == 0


# --- propose_evaluation: valutazione strutturata supplementare ---------------------


def test_valid_structured_evaluation_returns_summary_and_records_metadata():
    payload = json.dumps(
        {
            "passed": True,
            "failures": [],
            "evidence": ["1 passed in 0.01s"],
            "retry_recommendation": "nessun retry necessario",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    result = provider.propose_evaluation(task_description="task di test", test_output="1 passed in 0.01s")

    assert result == {
        "passed": True,
        "failures": [],
        "evidence": ["1 passed in 0.01s"],
        "retry_recommendation": "nessun retry necessario",
    }
    assert provider.last_call_record.success is True
    assert provider.last_call_record.is_simulated is False


# --- blocco automatico generico (timeout/errori API) --------------------------------


def test_timeout_raises_provider_timeout_error_and_records_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated socket timeout", request=request)

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderTimeoutError):
        provider.propose_plan("obiettivo qualsiasi")

    record = provider.last_call_record
    assert record.success is False
    assert "timed out" in record.error_summary.lower()


def test_secret_never_appears_in_any_persisted_error_summary():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"message": f"internal error, key was {FAKE_API_KEY}", "type": "server_error"}},
        )

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderMalformedResponseError):
        provider.propose_plan("obiettivo qualsiasi")

    assert FAKE_API_KEY not in provider.last_call_record.error_summary


# --- integrazione con il registro provider -----------------------------------------


def test_provider_factory_rejects_openai_without_config(monkeypatch):
    from mercury_foundry.ai.provider_factory import ProviderUnavailableError, get_provider

    for name in [
        "MERCURY_AI_API_KEY", "MERCURY_AI_MODEL", "MERCURY_AI_API_BASE_URL",
        "MERCURY_AI_TIMEOUT_SECONDS", "MERCURY_AI_MAX_CALLS_PER_RUN",
        "MERCURY_AI_MAX_TOKENS_PER_RUN", "MERCURY_AI_MAX_COST_USD_PER_RUN",
    ]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ProviderUnavailableError):
        get_provider("openai")


def test_provider_factory_builds_real_provider_when_fully_configured(monkeypatch):
    from mercury_foundry.ai.provider_factory import get_provider

    monkeypatch.setenv("MERCURY_AI_API_KEY", FAKE_API_KEY)
    monkeypatch.setenv("MERCURY_AI_MODEL", "test-model")
    monkeypatch.setenv("MERCURY_AI_API_BASE_URL", "https://example-provider.invalid/v1")
    monkeypatch.setenv("MERCURY_AI_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("MERCURY_AI_MAX_CALLS_PER_RUN", "3")
    monkeypatch.setenv("MERCURY_AI_MAX_TOKENS_PER_RUN", "1000")
    monkeypatch.setenv("MERCURY_AI_MAX_COST_USD_PER_RUN", "1.0")

    provider = get_provider("openai")
    assert provider.is_simulated is False
    assert "test-model" in provider.name


# --- persistenza dei metadata del provider nel ciclo di esecuzione ----------------


def test_provider_call_metadata_persists_when_real_provider_blocks_task(tmp_path):
    """Un provider reale che fallisce (es. timeout) deve bloccare il task in modo
    sicuro E lasciare traccia della chiamata in `provider_calls`, senza consumare
    un retry automatico e senza scrivere nulla in target_project."""
    from mercury_foundry.agents.builder import Builder
    from mercury_foundry.agents.evaluator import Evaluator
    from mercury_foundry.execution.loop import ExecutionLoop
    from mercury_foundry.orchestrator.orchestrator import Orchestrator
    from mercury_foundry.sandbox.workspace import Workspace
    from mercury_foundry.state import db, models
    from mercury_foundry.testing.runner import TestRunner

    def always_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=request)

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(always_timeout))

    conn = db.connect(tmp_path / "mercury_foundry.db")
    workspace = Workspace(tmp_path / "target_project")
    builder = Builder(provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    execution_loop = ExecutionLoop(conn, builder, evaluator)
    orchestrator = Orchestrator(conn, provider, execution_loop)

    # La pianificazione stessa userebbe il provider reale e fallirebbe: verifichiamo
    # che l'errore si propaghi in modo pulito e che il goal resti bloccato.
    with pytest.raises(ProviderTimeoutError):
        orchestrator.submit_goal("aggiungi una capability qualsiasi")

    goal = models.list_goals(conn)[0]
    assert goal["status"] == "blocked"

    calls = models.list_provider_calls_for_goal(conn, goal["id"])
    assert len(calls) == 1
    assert calls[0]["success"] == 0
    assert calls[0]["is_simulated"] == 0
    assert FAKE_API_KEY not in (calls[0]["error_summary"] or "")

    # Nessun file scritto nella sandbox: il fallimento è avvenuto in fase di piano.
    assert list(workspace.root.glob("**/*.py")) == []

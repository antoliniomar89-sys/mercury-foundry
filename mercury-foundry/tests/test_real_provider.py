"""Test del provider AI reale (OpenAI-compatibile) — SOLO con HTTP mockato.

Nessuno di questi test esegue una chiamata di rete reale: `http_post` è
sempre iniettato come mock. Verificano budget, blocco automatico, redazione
dei segreti e persistenza dei metadata del provider.
"""

from __future__ import annotations

import pytest

from mercury_foundry.ai.errors import (
    ProviderCallLimitExceededError,
    ProviderCostBudgetExceededError,
    ProviderMalformedResponseError,
    ProviderTimeoutError,
    ProviderUnknownModelError,
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


def _success_response(content: str, total_tokens: int = 10) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": total_tokens, "prompt_tokens": total_tokens // 2, "completion_tokens": total_tokens // 2},
    }


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


# --- chiamata riuscita e metadata --------------------------------------------------

def test_healthy_real_provider_call_records_metadata_and_no_secrets_in_error_summary():
    calls = []

    def mock_http_post(url, headers, body, timeout):
        calls.append((url, headers, body, timeout))
        return _success_response('["task di test"]')

    provider = OpenAICompatibleProvider(_config(), http_post=mock_http_post)
    plan = provider.propose_plan("aggiungi una capability di test")

    assert plan == ["task di test"]
    assert len(calls) == 1
    assert FAKE_API_KEY in calls[0][1]["Authorization"]  # l'header REALE contiene la key (necessario per l'API)

    record = provider.last_call_record
    assert record is not None
    assert record.success is True
    assert record.is_simulated is False
    assert record.model == "test-model"
    assert record.call_number == 1
    assert record.usage["total_tokens"] == 10
    assert record.estimated_cost_usd == pytest.approx(0.0001)
    assert record.error_summary is None


# --- blocco automatico --------------------------------------------------------------

def test_timeout_raises_provider_timeout_error_and_records_failure():
    def mock_http_post(url, headers, body, timeout):
        raise TimeoutError("simulated socket timeout")

    provider = OpenAICompatibleProvider(_config(), http_post=mock_http_post)
    with pytest.raises(ProviderTimeoutError):
        provider.propose_plan("obiettivo qualsiasi")

    record = provider.last_call_record
    assert record.success is False
    assert "timeout" in record.error_summary.lower()


def test_malformed_response_raises_and_records_failure():
    def mock_http_post(url, headers, body, timeout):
        return {"unexpected": "shape"}

    provider = OpenAICompatibleProvider(_config(), http_post=mock_http_post)
    with pytest.raises(ProviderMalformedResponseError):
        provider.propose_plan("obiettivo qualsiasi")

    assert provider.last_call_record.success is False


def test_non_json_plan_content_raises_malformed_response_error():
    def mock_http_post(url, headers, body, timeout):
        return _success_response("questo non e' JSON valido")

    provider = OpenAICompatibleProvider(_config(), http_post=mock_http_post)
    with pytest.raises(ProviderMalformedResponseError):
        provider.propose_plan("obiettivo qualsiasi")


def test_unknown_model_error_from_provider_is_translated():
    def mock_http_post(url, headers, body, timeout):
        return {"error": {"code": "model_not_found", "message": f"model not found (key={FAKE_API_KEY})"}}

    provider = OpenAICompatibleProvider(_config(), http_post=mock_http_post)
    with pytest.raises(ProviderUnknownModelError) as exc_info:
        provider.propose_plan("obiettivo qualsiasi")

    # Il messaggio dell'errore applicativo non deve contenere la api_key.
    assert FAKE_API_KEY not in str(exc_info.value)
    assert FAKE_API_KEY not in provider.last_call_record.error_summary


def test_call_limit_exceeded_blocks_further_calls():
    def mock_http_post(url, headers, body, timeout):
        return _success_response('["ok"]')

    provider = OpenAICompatibleProvider(_config(max_calls_per_run=1), http_post=mock_http_post)
    provider.propose_plan("primo obiettivo")  # consuma l'unica chiamata concessa

    with pytest.raises(ProviderCallLimitExceededError):
        provider.propose_plan("secondo obiettivo")


def test_usage_budget_exceeded_blocks_and_records_failure():
    def mock_http_post(url, headers, body, timeout):
        return _success_response('["ok"]', total_tokens=5000)

    provider = OpenAICompatibleProvider(
        _config(max_tokens_per_run=100, max_calls_per_run=5), http_post=mock_http_post
    )
    with pytest.raises(ProviderUsageBudgetExceededError):
        provider.propose_plan("obiettivo che consuma troppi token")

    assert provider.last_call_record.success is False


def test_cost_budget_exceeded_blocks_and_records_failure():
    def mock_http_post(url, headers, body, timeout):
        return _success_response('["ok"]', total_tokens=500)

    provider = OpenAICompatibleProvider(
        _config(max_cost_usd_per_run=0.0001, max_tokens_per_run=100_000, cost_per_1k_tokens_usd=1.0),
        http_post=mock_http_post,
    )
    with pytest.raises(ProviderCostBudgetExceededError):
        provider.propose_plan("obiettivo costoso")

    assert provider.last_call_record.success is False


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

def test_provider_call_metadata_persists_when_real_provider_blocks_task(tmp_path, monkeypatch):
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

    def always_timeout(url, headers, body, timeout):
        raise TimeoutError("simulated timeout")

    provider = OpenAICompatibleProvider(_config(), http_post=always_timeout)

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


def test_secret_never_appears_in_any_persisted_error_summary(tmp_path):
    """Verifica end-to-end di redazione: anche se il provider (mock) restituisce un
    messaggio di errore che include la api_key, la riga persistita non la contiene."""
    def leaky_error_response(url, headers, body, timeout):
        return {"error": {"code": "server_error", "message": f"internal error, key was {FAKE_API_KEY}"}}

    provider = OpenAICompatibleProvider(_config(), http_post=leaky_error_response)
    with pytest.raises(ProviderMalformedResponseError):
        provider.propose_plan("obiettivo qualsiasi")

    assert FAKE_API_KEY not in provider.last_call_record.error_summary

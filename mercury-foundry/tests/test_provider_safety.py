"""Test della sicurezza/robustezza del provider AI: nessun fallback silenzioso,
identità e stato di simulazione sempre distinguibili e persistenti.
"""

import pytest

from mercury_foundry.ai.fake_model import FakeModel
from mercury_foundry.ai.provider_factory import (
    ProviderUnavailableError,
    get_provider,
    list_available_providers,
)


def test_unknown_provider_is_rejected_with_clear_error():
    with pytest.raises(ProviderUnavailableError) as exc_info:
        get_provider("openai-gpt-99")

    message = str(exc_info.value)
    assert "openai-gpt-99" in message
    assert "fake" in message  # elenca i provider disponibili


def test_no_silent_fallback_to_fake_model_on_unknown_provider():
    """Un provider sconosciuto deve fermare l'esecuzione, non tornare FakeModel."""
    try:
        provider = get_provider("mystery-provider")
    except ProviderUnavailableError:
        provider = None

    assert provider is None, "un provider sconosciuto non deve mai risolversi silenziosamente in un provider valido"


def test_known_fake_provider_resolves_and_is_declared_simulated():
    provider = get_provider("fake")
    assert isinstance(provider, FakeModel)
    assert provider.is_simulated is True
    assert provider.name == "fake-deterministic"


def test_default_provider_without_explicit_name_is_fake_and_simulated(monkeypatch):
    monkeypatch.delenv("MERCURY_AI_PROVIDER", raising=False)
    provider = get_provider(None)
    assert provider.is_simulated is True


def test_env_var_selects_provider(monkeypatch):
    monkeypatch.setenv("MERCURY_AI_PROVIDER", "fake")
    provider = get_provider(None)
    assert isinstance(provider, FakeModel)


def test_list_available_providers_includes_fake_and_openai_compatible():
    # Il registro elenca i NOMI dei provider disponibili in questa istanza.
    # MF-PROVIDER-001 aggiunge "openai_compatible" come nome canonico accanto
    # all'alias retrocompatibile "openai". Indipendentemente dal fatto che le
    # credenziali siano già configurate: la richiesta di un provider mal
    # configurato fallisce esplicitamente (vedi test_real_provider.py).
    providers = list_available_providers()
    assert "fake" in providers
    assert "openai" in providers
    assert "openai_compatible" in providers
    assert providers == sorted(providers)  # ordinamento alfabetico garantito


def test_provider_metadata_persists_on_attempt_and_candidate(tmp_path):
    """Ogni attempt e ogni candidate deve conservare provider_name/is_simulated."""
    from mercury_foundry.state import models
    from mercury_foundry.wiring import build_foundry

    foundry = build_foundry(
        db_path=tmp_path / "mercury_foundry.db",
        sandbox_root=tmp_path / "target_project",
        provider_name="fake",
    )
    goal_id = foundry.orchestrator.submit_goal("aggiungi una capability health check")
    goal_run = foundry.orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]

    attempts = models.get_attempts_for_task(foundry.conn, outcome.task_id)
    for attempt in attempts:
        assert attempt["provider_name"] == "fake-deterministic"
        assert bool(attempt["is_simulated"]) is True

    candidate = models.get_candidate(foundry.conn, outcome.candidate_id)
    assert candidate["provider_name"] == "fake-deterministic"
    assert bool(candidate["is_simulated"]) is True

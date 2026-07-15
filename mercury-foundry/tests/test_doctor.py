"""Test del comando/diagnostica 'doctor'."""

from mercury_foundry.diagnostics import (
    OVERALL_NOT_READY,
    OVERALL_READY_REAL,
    OVERALL_READY_SIMULATED,
    run_doctor,
)


def test_doctor_healthy_simulated_mode_reports_ready_simulated(tmp_path):
    report = run_doctor(
        db_path=tmp_path / "mercury_foundry.db",
        sandbox_root=tmp_path / "target_project",
        provider_name="fake",
    )

    assert report.overall_status == OVERALL_READY_SIMULATED
    assert not report.has_errors()

    names = {c.name for c in report.checks}
    for expected in [
        "python_runtime",
        "test_command",
        "database",
        "sandbox_isolation",
        "ai_provider",
        "max_attempts",
        "approval_gate",
        "audit_log",
    ]:
        assert expected in names, f"controllo mancante nel report doctor: {expected}"

    provider_check = next(c for c in report.checks if c.name == "ai_provider")
    assert "SIMULATO" in provider_check.detail


def test_doctor_with_invalid_database_path_is_not_ready(tmp_path):
    # Un file esistente NON-directory al posto della cartella del DB rende
    # impossibile creare/aprire il DB in quel percorso.
    blocking_file = tmp_path / "blocked_as_dir"
    blocking_file.write_text("not a directory")
    bogus_db_path = blocking_file / "mercury_foundry.db"

    report = run_doctor(
        db_path=bogus_db_path,
        sandbox_root=tmp_path / "target_project",
        provider_name="fake",
    )

    assert report.overall_status == OVERALL_NOT_READY
    assert report.has_errors()
    db_check = next(c for c in report.checks if c.name == "database")
    assert db_check.status == "error"


def test_doctor_sandbox_equal_to_project_root_is_not_ready(tmp_path):
    from mercury_foundry import config

    report = run_doctor(
        db_path=tmp_path / "mercury_foundry.db",
        sandbox_root=config.BASE_DIR,
        provider_name="fake",
    )

    assert report.overall_status == OVERALL_NOT_READY
    sandbox_check = next(c for c in report.checks if c.name == "sandbox_isolation")
    assert sandbox_check.status == "error"


def test_doctor_with_unknown_provider_is_not_ready(tmp_path):
    report = run_doctor(
        db_path=tmp_path / "mercury_foundry.db",
        sandbox_root=tmp_path / "target_project",
        provider_name="totally-unknown-provider",
    )

    assert report.overall_status == OVERALL_NOT_READY
    provider_check = next(c for c in report.checks if c.name == "ai_provider")
    assert provider_check.status == "error"


def test_doctor_real_provider_without_credentials_is_not_ready(tmp_path, monkeypatch):
    for name in [
        "MERCURY_AI_API_KEY", "MERCURY_AI_MODEL", "MERCURY_AI_API_BASE_URL",
        "MERCURY_AI_TIMEOUT_SECONDS", "MERCURY_AI_MAX_CALLS_PER_RUN",
        "MERCURY_AI_MAX_TOKENS_PER_RUN", "MERCURY_AI_MAX_COST_USD_PER_RUN",
    ]:
        monkeypatch.delenv(name, raising=False)

    report = run_doctor(
        db_path=tmp_path / "mercury_foundry.db",
        sandbox_root=tmp_path / "target_project",
        provider_name="openai",
    )

    assert report.overall_status == OVERALL_NOT_READY
    provider_check = next(c for c in report.checks if c.name == "ai_provider")
    assert provider_check.status == "error"
    assert "MERCURY_AI_API_KEY" in provider_check.detail


def test_doctor_real_provider_fully_configured_is_ready_real(tmp_path, monkeypatch):
    monkeypatch.setenv("MERCURY_AI_API_KEY", "sk-test-doctor-check")
    monkeypatch.setenv("MERCURY_AI_MODEL", "test-model")
    monkeypatch.setenv("MERCURY_AI_API_BASE_URL", "https://example-provider.invalid/v1")
    monkeypatch.setenv("MERCURY_AI_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("MERCURY_AI_MAX_CALLS_PER_RUN", "3")
    monkeypatch.setenv("MERCURY_AI_MAX_TOKENS_PER_RUN", "1000")
    monkeypatch.setenv("MERCURY_AI_MAX_COST_USD_PER_RUN", "1.0")

    report = run_doctor(
        db_path=tmp_path / "mercury_foundry.db",
        sandbox_root=tmp_path / "target_project",
        provider_name="openai",
    )

    assert report.overall_status == OVERALL_READY_REAL
    provider_check = next(c for c in report.checks if c.name == "ai_provider")
    assert provider_check.status == "ok"
    assert "sk-test-doctor-check" not in provider_check.detail
    assert "reale" in provider_check.detail.lower()

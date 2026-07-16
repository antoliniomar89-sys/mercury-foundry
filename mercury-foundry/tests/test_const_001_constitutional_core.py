"""MF-CONST-001 — Test del Constitutional Core.

Copertura dei 13 casi richiesti dalla specifica + casi di integrazione:
  1.  Caricamento valido della Costituzione
  2.  Configurazione corrotta (JSON invalido, checksum, campi mancanti)
  3.  Ricerca di un principio
  4.  Decisione con evidenza sufficiente
  5.  Decisione senza evidenza
  6.  Organo fuori mandato (CONST-003)
  7.  Richiesta di modifica costituzionale da soggetto non autorizzato (CONST-007)
  8.  Funzionamento in disabled mode
  9.  Funzionamento in shadow mode
  10. Nessun blocco del flusso esistente in shadow mode
  11. Serializzazione del validation result
  12. Audit event generato una sola volta
  13. Compatibilità con i test esistenti
  14. CONST-004 warning reversibilità
  15. CONST-002 auditability
  16. Integrazione con authorize_organ_decision (hook nel flusso autonomy)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.autonomy import models as am
from mercury_foundry.autonomy.authorization import authorize_organ_decision
from mercury_foundry.constitutional.models import (
    ConstitutionLoadError,
    ConstitutionalValidationRequest,
    ConstitutionalValidationResult,
    EnforcementAction,
    PrincipleLevel,
    PrincipleStatus,
    ValidationStatus,
)
from mercury_foundry.constitutional.registry import ConstitutionRegistry, get_default_registry
from mercury_foundry.constitutional.shadow import maybe_validate_constitution
from mercury_foundry.constitutional.validator import ConstitutionalValidator
from mercury_foundry.state import db
from mercury_foundry.wiring import build_foundry


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_VALID_CONSTITUTION = {
    "version": "1.0.0",
    "status": "active",
    "effective_from": "2026-07-16T00:00:00Z",
    "created_at": "2026-07-16T00:00:00Z",
    "approved_by": "test",
    "checksum": "auto",
    "principles": [
        {
            "principle_id": "CONST-001",
            "title": "Evidence Before Investment",
            "description": "...",
            "level": "constitutional",
            "status": "active",
            "enforcement": "audit_only",
            "applies_to": ["budget_critical"],
            "required_evidence": [],
            "source_refs": [],
            "created_at": "2026-07-16T00:00:00Z",
            "updated_at": "2026-07-16T00:00:00Z",
        },
        {
            "principle_id": "CONST-002",
            "title": "Auditability",
            "description": "...",
            "level": "constitutional",
            "status": "active",
            "enforcement": "audit_only",
            "applies_to": ["all"],
            "required_evidence": [],
            "source_refs": [],
            "created_at": "2026-07-16T00:00:00Z",
            "updated_at": "2026-07-16T00:00:00Z",
        },
        {
            "principle_id": "CONST-007",
            "title": "Human Approval for Constitutional Change",
            "description": "...",
            "level": "immutable",
            "status": "active",
            "enforcement": "audit_only",
            "applies_to": ["constitutional_actions"],
            "required_evidence": [],
            "source_refs": [],
            "created_at": "2026-07-16T00:00:00Z",
            "updated_at": "2026-07-16T00:00:00Z",
        },
    ],
}


def _write_constitution(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "test_constitution.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _fresh_conn(tmp_path: Path) -> sqlite3.Connection:
    return db.connect(tmp_path / "mf.db")


def _fresh_request(
    *,
    decision_id: str = "corr-123",
    organ_id: str = "TEST_ORGAN",
    action_type: str = "BUILD_PROBE",
    authority_mode: str = "proposal",
    evidence_refs: list[str] | None = None,
    budget_impact: float | None = None,
    risk_level: str | None = None,
    metadata: dict | None = None,
) -> ConstitutionalValidationRequest:
    return ConstitutionalValidationRequest(
        decision_id=decision_id,
        organ_id=organ_id,
        action_type=action_type,
        authority_mode=authority_mode,
        evidence_refs=evidence_refs or [],
        budget_impact=budget_impact,
        risk_level=risk_level,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# 1 — Caricamento valido della Costituzione
# ---------------------------------------------------------------------------

def test_valid_constitution_loads_without_error(tmp_path):
    """Un file JSON valido viene caricato correttamente."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)

    assert registry.version_string == "1.0.0"
    assert len(registry) == 3


def test_valid_constitution_default_path_loads():
    """La Costituzione di default (constitution_v1.json) è caricabile."""
    registry = ConstitutionRegistry.load()
    assert registry.version_string == "1.0.0"
    assert len(registry) >= 7  # tutti i principi iniziali


def test_constitution_loaded_event_on_audit(tmp_path):
    """log_constitution_loaded emette un audit constitution.loaded."""
    from mercury_foundry.constitutional.shadow import log_constitution_loaded
    conn = _fresh_conn(tmp_path)
    registry = ConstitutionRegistry.load()
    log_constitution_loaded(conn, registry)
    audit = list_audit_log(conn, limit=1000)
    loaded_events = [r for r in audit if r["action"] == "constitution.loaded"]
    assert len(loaded_events) == 1


# ---------------------------------------------------------------------------
# 2 — Configurazione corrotta
# ---------------------------------------------------------------------------

def test_invalid_json_raises_constitution_load_error(tmp_path):
    """File non JSON solleva ConstitutionLoadError."""
    path = tmp_path / "bad.json"
    path.write_text("non sono JSON")
    with pytest.raises(ConstitutionLoadError, match="JSON"):
        ConstitutionRegistry.load(path)


def test_missing_required_field_raises_constitution_load_error(tmp_path):
    """Campo obbligatorio mancante solleva ConstitutionLoadError."""
    bad = dict(_VALID_CONSTITUTION)
    del bad["version"]
    path = _write_constitution(tmp_path, bad)
    with pytest.raises(ConstitutionLoadError, match="obbligatori"):
        ConstitutionRegistry.load(path)


def test_invalid_enum_value_raises_constitution_load_error(tmp_path):
    """Valore enum non riconosciuto in un principio solleva ConstitutionLoadError."""
    bad = json.loads(json.dumps(_VALID_CONSTITUTION))
    bad["principles"][0]["level"] = "cosmic"  # non valido
    path = _write_constitution(tmp_path, bad)
    with pytest.raises(ConstitutionLoadError):
        ConstitutionRegistry.load(path)


def test_missing_file_raises_constitution_load_error(tmp_path):
    """File inesistente solleva ConstitutionLoadError."""
    with pytest.raises(ConstitutionLoadError, match="non trovato"):
        ConstitutionRegistry.load(tmp_path / "nonexistent.json")


def test_duplicate_principle_id_raises_load_error(tmp_path):
    """principle_id duplicati sollevano ConstitutionLoadError."""
    bad = json.loads(json.dumps(_VALID_CONSTITUTION))
    bad["principles"].append(bad["principles"][0])  # duplicato
    path = _write_constitution(tmp_path, bad)
    with pytest.raises(ConstitutionLoadError, match="duplicati"):
        ConstitutionRegistry.load(path)


def test_shadow_mode_does_not_raise_on_corrupt_constitution(tmp_path, monkeypatch):
    """In shadow mode, una Costituzione corrotta non blocca il flusso."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "CONSTITUTIONAL_CORE_MODE", "shadow")

    import mercury_foundry.constitutional.registry as reg_mod
    monkeypatch.setattr(reg_mod, "_default_registry_initialized", False)
    monkeypatch.setattr(reg_mod, "_default_registry", None)
    monkeypatch.setattr(reg_mod, "_default_registry_error", None)

    # Sostituisce get_default_registry con una versione che solleva sempre
    original_get = reg_mod.get_default_registry
    def _broken(*a, **kw):
        raise ConstitutionLoadError("file corrotto simulato")
    monkeypatch.setattr(reg_mod, "get_default_registry", _broken)

    conn = _fresh_conn(tmp_path)
    # In shadow mode, NON deve sollevare
    result = maybe_validate_constitution(
        conn,
        organ_key="TEST",
        decision_type="BUILD",
        authority_mode="proposal",
        subject_type="goal",
        subject_id="1",
    )
    assert result is None, "Shadow mode deve ritornare None su errore tecnico"

    # Deve esserci un audit constitution.configuration.invalid
    audit = list_audit_log(conn, limit=1000)
    conf_events = [r for r in audit if r["action"] == "constitution.configuration.invalid"]
    assert len(conf_events) == 1


# ---------------------------------------------------------------------------
# 3 — Ricerca di un principio
# ---------------------------------------------------------------------------

def test_get_principle_by_id(tmp_path):
    """get_principle restituisce il principio corretto per ID."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)

    p = registry.get_principle("CONST-001")
    assert p is not None
    assert p.principle_id == "CONST-001"
    assert p.title == "Evidence Before Investment"


def test_get_principle_returns_none_for_unknown_id(tmp_path):
    """get_principle restituisce None per ID inesistente."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    assert registry.get_principle("CONST-999") is None


def test_filter_principles_by_level(tmp_path):
    """filter_principles filtra correttamente per livello."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)

    immutable = registry.filter_principles(level=PrincipleLevel.IMMUTABLE)
    assert all(p.level == PrincipleLevel.IMMUTABLE for p in immutable)
    assert any(p.principle_id == "CONST-007" for p in immutable)


def test_list_active_principles_excludes_inactive(tmp_path):
    """list_active_principles esclude principi non in stato active/shadow."""
    const = json.loads(json.dumps(_VALID_CONSTITUTION))
    const["principles"].append({
        "principle_id": "CONST-DEPRECATED",
        "title": "Old",
        "description": "...",
        "level": "local",
        "status": "deprecated",
        "enforcement": "advisory",
        "applies_to": ["all"],
        "required_evidence": [],
        "source_refs": [],
        "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z",
    })
    path = _write_constitution(tmp_path, const)
    registry = ConstitutionRegistry.load(path)
    active = registry.list_active_principles()
    assert not any(p.principle_id == "CONST-DEPRECATED" for p in active)


# ---------------------------------------------------------------------------
# 4 — Decisione con evidenza sufficiente (CONST-001)
# ---------------------------------------------------------------------------

def test_decision_with_evidence_passes_const001(tmp_path):
    """Con budget_impact > 0 e evidence_refs non vuoto, CONST-001 è rispettato."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(
        budget_impact=100.0,
        evidence_refs=["doc/business-case.md"],
    )
    result = validator.validate(request)

    assert "CONST-001" in result.passed_principles
    assert "CONST-001" not in result.violated_principles


def test_decision_without_budget_impact_passes_const001(tmp_path):
    """Senza budget_impact, CONST-001 non è applicabile (non valutato)."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(budget_impact=None, evidence_refs=[])
    result = validator.validate(request)

    assert "CONST-001" not in result.evaluated_principles


# ---------------------------------------------------------------------------
# 5 — Decisione senza evidenza (CONST-001)
# ---------------------------------------------------------------------------

def test_decision_without_evidence_violates_const001(tmp_path):
    """budget_impact > 0 con evidence_refs vuoto viola CONST-001."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(
        budget_impact=50.0,
        evidence_refs=[],
    )
    result = validator.validate(request)

    assert "CONST-001" in result.violated_principles
    assert "CONST-001" not in result.passed_principles
    assert any("CONST-001" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 6 — Organo fuori mandato (CONST-003)
# ---------------------------------------------------------------------------

def test_unknown_authority_mode_violates_const003(tmp_path):
    """authority_mode non riconosciuto viola CONST-003 (Bounded Autonomy)."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)

    # Aggiungi CONST-003 al test constitution
    const = json.loads(path.read_text())
    const["principles"].append({
        "principle_id": "CONST-003",
        "title": "Bounded Autonomy",
        "description": "...",
        "level": "constitutional",
        "status": "active",
        "enforcement": "audit_only",
        "applies_to": ["all"],
        "required_evidence": [],
        "source_refs": [],
        "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z",
    })
    path2 = _write_constitution(tmp_path, const)
    registry = ConstitutionRegistry.load(path2)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(
        organ_id="ROGUE_ORGAN",
        authority_mode="super_autonomous",  # non riconosciuto
    )
    result = validator.validate(request)

    assert "CONST-003" in result.violated_principles
    assert any("CONST-003" in w for w in result.warnings)


def test_empty_organ_id_violates_const003(tmp_path):
    """organ_id vuoto viola CONST-003."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    const = json.loads(path.read_text())
    const["principles"].append({
        "principle_id": "CONST-003",
        "title": "Bounded Autonomy",
        "description": "...",
        "level": "constitutional",
        "status": "active",
        "enforcement": "audit_only",
        "applies_to": ["all"],
        "required_evidence": [],
        "source_refs": [],
        "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z",
    })
    path2 = _write_constitution(tmp_path, const)
    registry = ConstitutionRegistry.load(path2)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(organ_id="")
    result = validator.validate(request)

    assert "CONST-003" in result.violated_principles


# ---------------------------------------------------------------------------
# 7 — Modifica costituzionale da soggetto non autorizzato (CONST-007)
# ---------------------------------------------------------------------------

def test_autonomous_constitutional_action_violates_const007(tmp_path):
    """azione constitutional con authority_mode=autonomous viola CONST-007."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(
        action_type="constitutional_update_principle",
        authority_mode="autonomous",
    )
    result = validator.validate(request)

    assert "CONST-007" in result.violated_principles
    assert any("CONST-007" in w for w in result.warnings)


def test_non_autonomous_constitutional_action_passes_const007(tmp_path):
    """azione constitutional con authority_mode=escalation_required rispetta CONST-007."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(
        action_type="constitutional_change_review",
        authority_mode="escalation_required",
    )
    result = validator.validate(request)

    assert "CONST-007" in result.passed_principles
    assert "CONST-007" not in result.violated_principles


def test_non_constitutional_action_is_not_evaluated_by_const007(tmp_path):
    """azione non constitutional: CONST-007 non è applicabile."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(
        action_type="BUILD_PROBE",
        authority_mode="autonomous",
    )
    result = validator.validate(request)

    assert "CONST-007" not in result.evaluated_principles


# ---------------------------------------------------------------------------
# 8 — Funzionamento in disabled mode
# ---------------------------------------------------------------------------

def test_disabled_mode_is_noop(tmp_path, monkeypatch):
    """In disabled mode maybe_validate_constitution ritorna None immediatamente."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "CONSTITUTIONAL_CORE_MODE", "disabled")

    conn = _fresh_conn(tmp_path)
    audit_before = list_audit_log(conn, limit=1000)

    result = maybe_validate_constitution(
        conn,
        organ_key="ORGAN",
        decision_type="BUILD",
        authority_mode="autonomous",
        subject_type="task",
        subject_id="1",
    )

    assert result is None
    audit_after = list_audit_log(conn, limit=1000)
    # Nessun evento constitution.* deve essere stato prodotto
    new_events = audit_after[len(audit_before):]
    const_events = [r for r in new_events if r["action"].startswith("constitution.")]
    assert len(const_events) == 0, "disabled mode non deve produrre audit events"


# ---------------------------------------------------------------------------
# 9 — Funzionamento in shadow mode
# ---------------------------------------------------------------------------

def test_shadow_mode_produces_audit_event(tmp_path, monkeypatch):
    """In shadow mode, maybe_validate_constitution produce un audit event."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "CONSTITUTIONAL_CORE_MODE", "shadow")

    # Reset singleton del registry
    import mercury_foundry.constitutional.registry as reg_mod
    monkeypatch.setattr(reg_mod, "_default_registry_initialized", False)
    monkeypatch.setattr(reg_mod, "_default_registry", None)
    monkeypatch.setattr(reg_mod, "_default_registry_error", None)

    conn = _fresh_conn(tmp_path)
    audit_before = list_audit_log(conn, limit=1000)

    result = maybe_validate_constitution(
        conn,
        organ_key="FOUNDRY_GOVERNANCE",
        decision_type="CANDIDATE_APPROVAL",
        authority_mode="escalation_required",
        subject_type="candidate",
        subject_id="1",
    )

    assert result is not None
    audit_after = list_audit_log(conn, limit=1000)
    new_events = audit_after[len(audit_before):]
    validation_events = [
        r for r in new_events if r["action"] == "constitution.validation.completed"
    ]
    assert len(validation_events) >= 1


def test_shadow_mode_passes_for_clean_request(tmp_path, monkeypatch):
    """In shadow mode, una richiesta pulita produce status=pass."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "CONSTITUTIONAL_CORE_MODE", "shadow")

    import mercury_foundry.constitutional.registry as reg_mod
    monkeypatch.setattr(reg_mod, "_default_registry_initialized", False)
    monkeypatch.setattr(reg_mod, "_default_registry", None)
    monkeypatch.setattr(reg_mod, "_default_registry_error", None)

    conn = _fresh_conn(tmp_path)
    result = maybe_validate_constitution(
        conn,
        organ_key="FOUNDRY_GOVERNANCE",
        decision_type="BUILD_PROBE",
        authority_mode="proposal",
        subject_type="task",
        subject_id="1",
        evidence_refs=["doc"],
    )

    assert result is not None
    assert result.status in (ValidationStatus.PASS, ValidationStatus.PASS_WITH_WARNINGS)


# ---------------------------------------------------------------------------
# 10 — Nessun blocco del flusso esistente in shadow mode
# ---------------------------------------------------------------------------

def test_shadow_mode_never_blocks_existing_flow(tmp_path, monkeypatch):
    """In shadow mode, anche una violazione costituzionale NON blocca il flusso.

    Verifica l'intera pipeline: submit_goal → run_goal → con validazione
    costituzionale attiva in shadow mode.
    """
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "CONSTITUTIONAL_CORE_MODE", "shadow")
    monkeypatch.setattr(cfg, "AUTONOMY_MODE", "shadow")

    import mercury_foundry.constitutional.registry as reg_mod
    monkeypatch.setattr(reg_mod, "_default_registry_initialized", False)
    monkeypatch.setattr(reg_mod, "_default_registry", None)
    monkeypatch.setattr(reg_mod, "_default_registry_error", None)

    foundry = build_foundry(
        db_path=tmp_path / "mf.db",
        sandbox_root=tmp_path / "target",
        provider_name="fake",
    )

    goal_id = foundry.orchestrator.submit_goal("test constitutional shadow no-block")
    run_result = foundry.orchestrator.run_goal(goal_id)

    from mercury_foundry.state import models
    goal = models.get_goal(foundry.conn, goal_id)
    assert goal["status"] == "awaiting_approval"
    assert len(run_result.task_outcomes) == 1


def test_shadow_mode_with_violation_does_not_block(tmp_path, monkeypatch):
    """Una violazione costituzionale in shadow mode produce un audit ma non blocca."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "CONSTITUTIONAL_CORE_MODE", "shadow")

    import mercury_foundry.constitutional.registry as reg_mod
    monkeypatch.setattr(reg_mod, "_default_registry_initialized", False)
    monkeypatch.setattr(reg_mod, "_default_registry", None)
    monkeypatch.setattr(reg_mod, "_default_registry_error", None)

    conn = _fresh_conn(tmp_path)
    audit_before = list_audit_log(conn, limit=1000)

    # Richiesta costituzionale con autonomous mode → violazione CONST-007
    result = maybe_validate_constitution(
        conn,
        organ_key="ROGUE",
        decision_type="constitutional_autonomous_patch",
        authority_mode="autonomous",
        subject_type="constitution",
        subject_id="1",
    )

    # Deve ritornare un risultato (non None), non sollevare
    assert result is not None
    assert "CONST-007" in result.violated_principles

    audit_after = list_audit_log(conn, limit=1000)
    new_events = audit_after[len(audit_before):]
    violation_events = [r for r in new_events if r["action"] == "constitution.violation.detected"]
    assert len(violation_events) >= 1


# ---------------------------------------------------------------------------
# 11 — Serializzazione del validation result
# ---------------------------------------------------------------------------

def test_validation_result_serializes_to_dict(tmp_path):
    """ConstitutionalValidationResult.to_dict() produce un dict serializzabile in JSON."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request()
    result = validator.validate(request)
    d = result.to_dict()

    # Deve essere serializzabile in JSON
    serialized = json.dumps(d)
    assert isinstance(serialized, str)

    # Round-trip
    restored = ConstitutionalValidationResult.from_dict(json.loads(serialized))
    assert restored.validation_id == result.validation_id
    assert restored.status == result.status
    assert restored.enforcement_action == result.enforcement_action
    assert restored.constitution_version == result.constitution_version


def test_validation_result_has_all_required_fields(tmp_path):
    """Il result contiene tutti i campi richiesti dalla specifica."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    result = validator.validate(_fresh_request())
    d = result.to_dict()

    required_keys = {
        "validation_id", "decision_id", "constitution_version",
        "status", "evaluated_principles", "passed_principles",
        "violated_principles", "warnings", "enforcement_action",
        "explanation", "evaluated_at",
    }
    assert required_keys <= set(d.keys())


# ---------------------------------------------------------------------------
# 12 — Audit event generato una sola volta
# ---------------------------------------------------------------------------

def test_audit_event_generated_exactly_once(tmp_path, monkeypatch):
    """Una singola chiamata a maybe_validate_constitution genera esattamente
    un evento constitution.validation.completed nell'audit log."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "CONSTITUTIONAL_CORE_MODE", "shadow")

    import mercury_foundry.constitutional.registry as reg_mod
    monkeypatch.setattr(reg_mod, "_default_registry_initialized", False)
    monkeypatch.setattr(reg_mod, "_default_registry", None)
    monkeypatch.setattr(reg_mod, "_default_registry_error", None)

    conn = _fresh_conn(tmp_path)
    audit_before = list_audit_log(conn, limit=1000)

    maybe_validate_constitution(
        conn,
        organ_key="ORGAN",
        decision_type="BUILD",
        authority_mode="proposal",
        subject_type="task",
        subject_id="1",
    )

    audit_after = list_audit_log(conn, limit=1000)
    new_events = audit_after[len(audit_before):]
    validation_events = [
        r for r in new_events if r["action"] == "constitution.validation.completed"
    ]
    assert len(validation_events) == 1, (
        f"Atteso esattamente 1 evento constitution.validation.completed, "
        f"trovato {len(validation_events)}"
    )


# ---------------------------------------------------------------------------
# 13 — Compatibilità con i test esistenti (smoke test)
# ---------------------------------------------------------------------------

def test_existing_suite_unaffected_by_constitutional_core(tmp_path, monkeypatch):
    """Il Constitutional Core in shadow mode non interferisce con i test esistenti.

    Verifica tramite un ciclo run completo: submit_goal → run_goal → approvazione.
    """
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "CONSTITUTIONAL_CORE_MODE", "shadow")
    monkeypatch.setattr(cfg, "AUTONOMY_MODE", "shadow")

    import mercury_foundry.constitutional.registry as reg_mod
    monkeypatch.setattr(reg_mod, "_default_registry_initialized", False)
    monkeypatch.setattr(reg_mod, "_default_registry", None)
    monkeypatch.setattr(reg_mod, "_default_registry_error", None)

    from mercury_foundry.approval import gate

    foundry = build_foundry(
        db_path=tmp_path / "mf.db",
        sandbox_root=tmp_path / "target",
        provider_name="fake",
    )

    goal_id = foundry.orchestrator.submit_goal("test compat constitutional")
    run_result = foundry.orchestrator.run_goal(goal_id)
    candidate_id = run_result.task_outcomes[0].candidate_id

    # Approvazione deve riuscire senza blocchi
    gate.approve_candidate(foundry.conn, candidate_id, backup_base_dir=foundry.backup_base_dir)

    from mercury_foundry.state import models
    c = models.get_candidate(foundry.conn, candidate_id)
    assert c["status"] == "approved"


# ---------------------------------------------------------------------------
# 14 — CONST-004 warning reversibilità
# ---------------------------------------------------------------------------

def test_const004_warning_on_high_risk_autonomous_without_rollback(tmp_path):
    """CONST-004 emette un warning per azioni autonomous ad alto rischio senza rollback_plan."""
    path = _write_constitution(tmp_path, {
        **_VALID_CONSTITUTION,
        "principles": [*_VALID_CONSTITUTION["principles"], {
            "principle_id": "CONST-004",
            "title": "Reversible First",
            "description": "...",
            "level": "operational",
            "status": "active",
            "enforcement": "advisory",
            "applies_to": ["autonomous_actions"],
            "required_evidence": [],
            "source_refs": [],
            "created_at": "2026-07-16T00:00:00Z",
            "updated_at": "2026-07-16T00:00:00Z",
        }],
    })
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(
        authority_mode="autonomous",
        risk_level="high",
        metadata={},  # nessun rollback_plan
    )
    result = validator.validate(request)

    assert "CONST-004" in result.passed_principles  # advisory, non violazione
    assert any("CONST-004" in w or "rollback" in w.lower() for w in result.warnings)


def test_const004_no_warning_with_rollback_plan(tmp_path):
    """CONST-004 non emette warning se rollback_plan è presente nel metadata."""
    path = _write_constitution(tmp_path, {
        **_VALID_CONSTITUTION,
        "principles": [*_VALID_CONSTITUTION["principles"], {
            "principle_id": "CONST-004",
            "title": "Reversible First",
            "description": "...",
            "level": "operational",
            "status": "active",
            "enforcement": "advisory",
            "applies_to": ["autonomous_actions"],
            "required_evidence": [],
            "source_refs": [],
            "created_at": "2026-07-16T00:00:00Z",
            "updated_at": "2026-07-16T00:00:00Z",
        }],
    })
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(
        authority_mode="autonomous",
        risk_level="high",
        metadata={"rollback_plan": "revert via backup"},
    )
    result = validator.validate(request)

    assert "CONST-004" in result.passed_principles
    assert not any("rollback" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# 15 — CONST-002 auditability
# ---------------------------------------------------------------------------

def test_const002_fails_on_empty_decision_id(tmp_path):
    """CONST-002 è violato quando decision_id è vuoto."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(decision_id="")
    result = validator.validate(request)

    assert "CONST-002" in result.violated_principles


def test_const002_passes_on_valid_decision_id(tmp_path):
    """CONST-002 è rispettato quando decision_id è presente e non vuoto."""
    path = _write_constitution(tmp_path, _VALID_CONSTITUTION)
    registry = ConstitutionRegistry.load(path)
    validator = ConstitutionalValidator(registry)

    request = _fresh_request(decision_id="corr-abc-123")
    result = validator.validate(request)

    assert "CONST-002" in result.passed_principles


# ---------------------------------------------------------------------------
# 16 — Integrazione con authorize_organ_decision
# ---------------------------------------------------------------------------

def test_constitutional_validation_integrated_in_authorization(tmp_path, monkeypatch):
    """authorize_organ_decision chiama maybe_validate_constitution e produce
    un audit constitution.validation.completed."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "CONSTITUTIONAL_CORE_MODE", "shadow")
    monkeypatch.setattr(cfg, "AUTONOMY_MODE", "shadow")

    import mercury_foundry.constitutional.registry as reg_mod
    monkeypatch.setattr(reg_mod, "_default_registry_initialized", False)
    monkeypatch.setattr(reg_mod, "_default_registry", None)
    monkeypatch.setattr(reg_mod, "_default_registry_error", None)

    conn = _fresh_conn(tmp_path)
    organ_id = am.create_organ(conn, organ_key="TEST_CONST_ORG",
                                name="Test", mission="test")
    am.create_mandate(conn, organ_id=organ_id, decision_type="BUILD_PROBE",
                      authority_mode="autonomous")

    audit_before = list_audit_log(conn, limit=1000)

    authorize_organ_decision(
        conn,
        organ_key="TEST_CONST_ORG",
        decision_type="BUILD_PROBE",
        subject_type="task",
        subject_id="1",
    )

    audit_after = list_audit_log(conn, limit=1000)
    new_events = audit_after[len(audit_before):]
    const_events = [r for r in new_events if r["action"].startswith("constitution.")]
    assert len(const_events) >= 1, "Nessun evento constitution.* trovato"
    assert any(r["action"] == "constitution.validation.completed" for r in const_events)


def test_constitutional_validation_disabled_mode_no_audit_in_authorization(tmp_path, monkeypatch):
    """In disabled mode, authorize_organ_decision non produce audit constitution.*"""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "CONSTITUTIONAL_CORE_MODE", "disabled")
    monkeypatch.setattr(cfg, "AUTONOMY_MODE", "shadow")

    conn = _fresh_conn(tmp_path)
    organ_id = am.create_organ(conn, organ_key="TEST_DISABLED_ORG",
                                name="Test", mission="test")
    am.create_mandate(conn, organ_id=organ_id, decision_type="BUILD_X",
                      authority_mode="autonomous")

    audit_before = list_audit_log(conn, limit=1000)

    authorize_organ_decision(
        conn,
        organ_key="TEST_DISABLED_ORG",
        decision_type="BUILD_X",
        subject_type="task",
        subject_id="1",
    )

    audit_after = list_audit_log(conn, limit=1000)
    new_events = audit_after[len(audit_before):]
    const_events = [r for r in new_events if r["action"].startswith("constitution.")]
    assert len(const_events) == 0, (
        f"disabled mode non deve produrre audit constitution.*: {[r['action'] for r in const_events]}"
    )

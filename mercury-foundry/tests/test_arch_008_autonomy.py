"""MF-ARCH-008 — Test dell'Autonomy Boundary Layer.

Copertura:
  - migrazione idempotente
  - organ_key univoco
  - mandate univoco per organ/decision_type
  - fail-closed quando il mandato manca
  - autonomous → autorizzato
  - proposal → non eseguito direttamente (allowed=False)
  - escalation_required
  - forbidden
  - limite rischio
  - limite budget
  - evidence obbligatoria
  - decision record immutabile nei campi critici / transizioni consentite
  - correlazione organ_event
  - modalità shadow non blocca i flussi esistenti
  - modalità enforced applica il mandato
  - audit scritto per ogni decisione
  - organo pilota FOUNDRY_GOVERNANCE seedato
  - regressione: tutti i flussi esistenti ancora funzionanti (via foundry)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mercury_foundry.autonomy import models as am
from mercury_foundry.autonomy.authorization import (
    AuthorizationResult,
    AutonomyBoundaryViolation,
    authorize_organ_decision,
)
from mercury_foundry.autonomy.seed import (
    GOVERNANCE_ORGAN_KEY,
    INITIAL_MANDATES,
    seed_foundry_governance,
)
from mercury_foundry.autonomy.shadow import maybe_check_governance
from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.state import db
from mercury_foundry.wiring import build_foundry


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _fresh_conn(tmp_path: Path) -> sqlite3.Connection:
    """Connessione fresca con schema + seed autonomy applicati."""
    return db.connect(tmp_path / "mf.db")


def _build_isolated(tmp_path: Path):
    return build_foundry(
        db_path=tmp_path / "mf.db",
        sandbox_root=tmp_path / "target",
        provider_name="fake",
    )


def _make_organ(conn, key="TEST_ORGAN") -> int:
    return am.create_organ(conn, organ_key=key, name="Test", mission="test organ")


def _make_mandate(conn, organ_id, decision_type="DO_THING", authority_mode="autonomous") -> int:
    return am.create_mandate(
        conn, organ_id=organ_id, decision_type=decision_type, authority_mode=authority_mode
    )


# ---------------------------------------------------------------------------
# 1 — Migrazione idempotente
# ---------------------------------------------------------------------------

def test_seed_foundry_governance_is_idempotent(tmp_path):
    """seed_foundry_governance è eseguibile N volte senza errori né duplicati."""
    conn = _fresh_conn(tmp_path)

    organs_before = conn.execute("SELECT COUNT(*) AS n FROM organs").fetchone()["n"]
    mandates_before = conn.execute("SELECT COUNT(*) AS n FROM decision_mandates").fetchone()["n"]

    # Esegui il seed altre 3 volte
    for _ in range(3):
        seed_foundry_governance(conn)

    organs_after = conn.execute("SELECT COUNT(*) AS n FROM organs").fetchone()["n"]
    mandates_after = conn.execute("SELECT COUNT(*) AS n FROM decision_mandates").fetchone()["n"]

    assert organs_after == organs_before, "Il seed ha duplicato organs"
    assert mandates_after == mandates_before, "Il seed ha duplicato mandates"


# ---------------------------------------------------------------------------
# 2 — organ_key univoco
# ---------------------------------------------------------------------------

def test_organ_key_unique_constraint(tmp_path):
    """Inserire due organi con lo stesso organ_key solleva IntegrityError."""
    conn = _fresh_conn(tmp_path)
    _make_organ(conn, "UNIQUE_ORGAN")

    with pytest.raises(sqlite3.IntegrityError):
        am.create_organ(conn, organ_key="UNIQUE_ORGAN", name="Dup", mission="dup")


# ---------------------------------------------------------------------------
# 3 — mandate univoco per organ/decision_type
# ---------------------------------------------------------------------------

def test_mandate_unique_per_organ_decision_type(tmp_path):
    """Inserire due mandati per la stessa coppia organ/decision_type solleva IntegrityError."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn)
    _make_mandate(conn, organ_id, "ACT_A", "autonomous")

    with pytest.raises(sqlite3.IntegrityError):
        am.create_mandate(conn, organ_id=organ_id, decision_type="ACT_A", authority_mode="forbidden")


# ---------------------------------------------------------------------------
# 4 — fail-closed quando il mandato manca
# ---------------------------------------------------------------------------

def test_fail_closed_when_mandate_missing(tmp_path):
    """Senza mandato il servizio rifiuta fail-closed (rejected, allowed=False)."""
    conn = _fresh_conn(tmp_path)
    _make_organ(conn, "ORG_NO_MANDATE")
    # Nessun mandato creato

    result = authorize_organ_decision(
        conn,
        organ_key="ORG_NO_MANDATE",
        decision_type="UNDECLARED_ACTION",
        subject_type="goal",
        subject_id="1",
    )

    assert result.allowed is False
    assert result.resulting_status == "rejected"
    assert "MANDATE_NOT_FOUND" in result.reason


def test_fail_closed_when_organ_missing(tmp_path):
    """Senza organo il servizio rifiuta fail-closed (rejected, allowed=False)."""
    conn = _fresh_conn(tmp_path)

    result = authorize_organ_decision(
        conn,
        organ_key="NONEXISTENT_ORGAN",
        decision_type="ANY_ACTION",
        subject_type="goal",
        subject_id="1",
    )

    assert result.allowed is False
    assert result.resulting_status == "rejected"
    assert "ORGAN_NOT_FOUND" in result.reason


# ---------------------------------------------------------------------------
# 5 — autonomous → autorizzato
# ---------------------------------------------------------------------------

def test_autonomous_mandate_is_authorized(tmp_path):
    """Un mandato autonomous autorizza la decisione immediatamente."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "AUTO_ORG")
    _make_mandate(conn, organ_id, "BUILD_PROBE", "autonomous")

    result = authorize_organ_decision(
        conn,
        organ_key="AUTO_ORG",
        decision_type="BUILD_PROBE",
        subject_type="task",
        subject_id="42",
    )

    assert result.allowed is True
    assert result.authority_mode == "autonomous"
    assert result.resulting_status == "authorized"
    assert result.requires_human_approval is False
    assert result.decision_record_id is not None


# ---------------------------------------------------------------------------
# 6 — proposal → non eseguito direttamente
# ---------------------------------------------------------------------------

def test_proposal_mandate_is_not_directly_executed(tmp_path):
    """Un mandato proposal propone ma non autorizza l'esecuzione diretta."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "PROP_ORG")
    _make_mandate(conn, organ_id, "SUGGEST_PLAN", "proposal")

    result = authorize_organ_decision(
        conn,
        organ_key="PROP_ORG",
        decision_type="SUGGEST_PLAN",
        subject_type="goal",
        subject_id="1",
    )

    assert result.allowed is False
    assert result.authority_mode == "proposal"
    assert result.resulting_status == "proposed"
    assert result.requires_human_approval is True


# ---------------------------------------------------------------------------
# 7 — escalation_required
# ---------------------------------------------------------------------------

def test_escalation_required_mandate_escalates(tmp_path):
    """Un mandato escalation_required produce uno stato escalated, non autorizza."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "ESC_ORG")
    _make_mandate(conn, organ_id, "PROMOTE_FILE", "escalation_required")

    result = authorize_organ_decision(
        conn,
        organ_key="ESC_ORG",
        decision_type="PROMOTE_FILE",
        subject_type="candidate",
        subject_id="7",
    )

    assert result.allowed is False
    assert result.authority_mode == "escalation_required"
    assert result.resulting_status == "escalated"
    assert result.requires_human_approval is True


# ---------------------------------------------------------------------------
# 8 — forbidden
# ---------------------------------------------------------------------------

def test_forbidden_mandate_rejects(tmp_path):
    """Un mandato forbidden rifiuta la decisione senza possibilità di escalation."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "FORB_ORG")
    _make_mandate(conn, organ_id, "WRITE_TO_PROD", "forbidden")

    result = authorize_organ_decision(
        conn,
        organ_key="FORB_ORG",
        decision_type="WRITE_TO_PROD",
        subject_type="db",
        subject_id="prod",
    )

    assert result.allowed is False
    assert result.authority_mode == "forbidden"
    assert result.resulting_status == "rejected"
    assert result.requires_human_approval is False
    assert "FORBIDDEN" in result.reason


# ---------------------------------------------------------------------------
# 9 — limite rischio
# ---------------------------------------------------------------------------

def test_risk_limit_exceeded_escalates(tmp_path):
    """Quando risk_score supera max_risk_score la decisione viene escalata."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "RISK_ORG")
    am.create_mandate(
        conn, organ_id=organ_id, decision_type="RISKY_ACTION",
        authority_mode="autonomous", max_risk_score=0.5,
    )

    result = authorize_organ_decision(
        conn,
        organ_key="RISK_ORG",
        decision_type="RISKY_ACTION",
        subject_type="task",
        subject_id="1",
        risk_score=0.9,
    )

    assert result.allowed is False
    assert result.resulting_status == "escalated"
    assert "RISK_LIMIT_EXCEEDED" in result.reason


def test_risk_within_limit_is_authorized(tmp_path):
    """Quando risk_score è entro max_risk_score, authority_mode=autonomous autorizza."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "RISK_OK_ORG")
    am.create_mandate(
        conn, organ_id=organ_id, decision_type="SAFE_ACTION",
        authority_mode="autonomous", max_risk_score=1.0,
    )

    result = authorize_organ_decision(
        conn,
        organ_key="RISK_OK_ORG",
        decision_type="SAFE_ACTION",
        subject_type="task",
        subject_id="1",
        risk_score=0.3,
    )

    assert result.allowed is True
    assert result.resulting_status == "authorized"


# ---------------------------------------------------------------------------
# 10 — limite budget
# ---------------------------------------------------------------------------

def test_budget_limit_exceeded_escalates(tmp_path):
    """Quando estimated_budget supera max_budget la decisione viene escalata."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "BUDGET_ORG")
    am.create_mandate(
        conn, organ_id=organ_id, decision_type="SPEND_MONEY",
        authority_mode="autonomous", max_budget=10.0,
    )

    result = authorize_organ_decision(
        conn,
        organ_key="BUDGET_ORG",
        decision_type="SPEND_MONEY",
        subject_type="run",
        subject_id="1",
        estimated_budget=100.0,
    )

    assert result.allowed is False
    assert result.resulting_status == "escalated"
    assert "BUDGET_LIMIT_EXCEEDED" in result.reason


# ---------------------------------------------------------------------------
# 11 — evidence obbligatoria
# ---------------------------------------------------------------------------

def test_evidence_required_rejects_when_missing(tmp_path):
    """Quando requires_evidence=True e evidence=None la decisione è rejected."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "EVID_ORG")
    am.create_mandate(
        conn, organ_id=organ_id, decision_type="AUDIT_CHECK",
        authority_mode="autonomous", requires_evidence=True,
    )

    result = authorize_organ_decision(
        conn,
        organ_key="EVID_ORG",
        decision_type="AUDIT_CHECK",
        subject_type="goal",
        subject_id="1",
        evidence=None,
    )

    assert result.allowed is False
    assert result.resulting_status == "rejected"
    assert "EVIDENCE_REQUIRED" in result.reason


def test_evidence_provided_passes_requirement(tmp_path):
    """Quando evidence è fornita il requisito è soddisfatto."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "EVID_OK_ORG")
    am.create_mandate(
        conn, organ_id=organ_id, decision_type="AUDIT_CHECK2",
        authority_mode="autonomous", requires_evidence=True,
    )

    result = authorize_organ_decision(
        conn,
        organ_key="EVID_OK_ORG",
        decision_type="AUDIT_CHECK2",
        subject_type="goal",
        subject_id="1",
        evidence={"metric": "ok"},
    )

    assert result.allowed is True
    assert result.resulting_status == "authorized"


# ---------------------------------------------------------------------------
# 12 — decision record immutabile / transizioni consentite
# ---------------------------------------------------------------------------

def test_decision_record_critical_fields_no_direct_update_api(tmp_path):
    """Il modulo autonomy.models non espone funzioni di update per i campi critici."""
    import mercury_foundry.autonomy.models as m
    public_names = [n for n in dir(m) if not n.startswith("_")]
    update_fns = [n for n in public_names if "update" in n.lower() and n != "transition_decision_record_status"]
    assert update_fns == [], (
        f"Funzioni di update non attese trovate in autonomy.models: {update_fns}"
    )


def test_decision_record_status_transition_allowed(tmp_path):
    """transition_decision_record_status consente transizioni valide."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "TRANS_ORG")
    record_id = am.create_decision_record(
        conn, organ_id=organ_id, decision_type="ACT", authority_mode="autonomous",
        subject_type="task", subject_id="1", status="authorized", reason="test",
    )
    # authorized → executed è consentito
    am.transition_decision_record_status(conn, record_id, "executed")
    record = am.get_decision_record(conn, record_id)
    assert record["status"] == "executed"


def test_decision_record_status_transition_forbidden(tmp_path):
    """transition_decision_record_status nega transizioni non consentite."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "TRANS_ORG2")
    record_id = am.create_decision_record(
        conn, organ_id=organ_id, decision_type="ACT", authority_mode="autonomous",
        subject_type="task", subject_id="1", status="rejected", reason="test",
    )
    # rejected è terminale: nessuna transizione consentita
    with pytest.raises(ValueError, match="terminale|Transizione"):
        am.transition_decision_record_status(conn, record_id, "authorized")

    # Il record deve rimanere invariato
    record = am.get_decision_record(conn, record_id)
    assert record["status"] == "rejected"


# ---------------------------------------------------------------------------
# 13 — correlazione organ_event
# ---------------------------------------------------------------------------

def test_organ_event_created_with_correct_correlation_id(tmp_path):
    """Ogni decisione crea un organ_event con correlation_id coerente."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "EVENT_ORG")
    _make_mandate(conn, organ_id, "EMIT_EVENT", "autonomous")

    result = authorize_organ_decision(
        conn,
        organ_key="EVENT_ORG",
        decision_type="EMIT_EVENT",
        subject_type="task",
        subject_id="1",
    )

    assert result.decision_record_id is not None
    # Cerca eventi correlati
    events = conn.execute(
        "SELECT * FROM organ_events WHERE source_organ_id = ?", (organ_id,)
    ).fetchall()
    assert len(events) >= 1, "Nessun organ_event creato"

    event = events[-1]
    assert event["correlation_id"] is not None and len(event["correlation_id"]) > 0
    assert event["event_type"] in ("DECISION_AUTHORIZED", "DECISION_PROPOSED",
                                   "DECISION_ESCALATED", "DECISION_REJECTED")


def test_multiple_events_with_same_correlation_id_linked(tmp_path):
    """list_events_by_correlation restituisce tutti gli eventi con lo stesso correlation_id."""
    conn = _fresh_conn(tmp_path)
    import uuid
    corr_id = str(uuid.uuid4())

    organ_id = _make_organ(conn, "CORR_ORG")
    am.create_organ_event(conn, source_organ_id=organ_id, event_type="ALPHA",
                          payload={}, correlation_id=corr_id)
    am.create_organ_event(conn, source_organ_id=organ_id, event_type="BETA",
                          payload={}, correlation_id=corr_id)

    linked = am.list_events_by_correlation(conn, corr_id)
    assert len(linked) == 2
    event_types = {e["event_type"] for e in linked}
    assert event_types == {"ALPHA", "BETA"}


# ---------------------------------------------------------------------------
# 14 — modalità shadow non blocca mai il chiamante
# ---------------------------------------------------------------------------

def test_shadow_mode_does_not_raise_on_forbidden_mandate(tmp_path, monkeypatch):
    """In shadow mode, anche con mandato forbidden, maybe_check_governance non
    solleva mai eccezioni: il flusso del chiamante continua normalmente."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "AUTONOMY_MODE", "shadow")

    conn = _fresh_conn(tmp_path)
    # FOUNDRY_GOVERNANCE ha CANDIDATE_APPROVAL → escalation_required di default;
    # lo cambiamo in forbidden per testare il caso più estremo.
    organ = am.get_organ_by_key(conn, "FOUNDRY_GOVERNANCE")
    assert organ is not None
    mandate = am.get_mandate(conn, organ["id"], "CANDIDATE_APPROVAL")
    conn.execute(
        "UPDATE decision_mandates SET authority_mode = 'forbidden' WHERE id = ?",
        (mandate["id"],),
    )
    conn.commit()

    # In shadow mode, maybe_check_governance NON deve mai sollevare.
    result = maybe_check_governance(
        conn,
        decision_type="CANDIDATE_APPROVAL",
        subject_type="candidate",
        subject_id="42",
    )

    # Ritorna AuthorizationResult (non None) anche con mandato forbidden.
    assert result is not None
    assert result.allowed is False              # il mandato ha detto no
    assert result.authority_mode == "forbidden"

    # Deve esserci un audit AUTONOMY_SHADOW_DIVERGENCE nell'audit log.
    audit = list_audit_log(conn, limit=1000)
    divergence = [r for r in audit if r["action"] == "AUTONOMY_SHADOW_DIVERGENCE"]
    assert len(divergence) >= 1, "Audit AUTONOMY_SHADOW_DIVERGENCE non trovato"


def test_shadow_mode_returns_none_and_does_not_raise_on_technical_error(tmp_path, monkeypatch):
    """In shadow mode, se authorize_organ_decision solleva un'eccezione tecnica
    (es. tabella mancante), maybe_check_governance ritorna None senza propagare."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "AUTONOMY_MODE", "shadow")

    conn = _fresh_conn(tmp_path)

    # Simula un errore tecnico patchando authorize_organ_decision
    import mercury_foundry.autonomy.authorization as auth_mod
    import mercury_foundry.autonomy.shadow as shadow_mod

    original = auth_mod.authorize_organ_decision
    def _explode(*a, **kw):
        raise RuntimeError("errore tecnico simulato")

    monkeypatch.setattr(shadow_mod, "authorize_organ_decision", _explode)

    result = maybe_check_governance(
        conn,
        decision_type="CANDIDATE_APPROVAL",
        subject_type="candidate",
        subject_id="1",
    )
    assert result is None, "Shadow mode dovrebbe ritornare None su errore tecnico"


def test_shadow_mode_does_not_affect_gate_approve_flow(tmp_path, monkeypatch):
    """gate.approve_candidate NON chiama maybe_check_governance internamente
    (V0 Autonomy Layer non è ancora integrato nel gate): il flusso di
    approvazione continua a funzionare indipendentemente dalla modalità autonomy."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "AUTONOMY_MODE", "enforced")

    from mercury_foundry.approval import gate

    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test gate indipendente")
    run_result = foundry.orchestrator.run_goal(goal_id)
    candidate_id = run_result.task_outcomes[0].candidate_id

    # In enforced mode il gate NON è ancora integrato con il layer autonomy:
    # approve_candidate deve riuscire normalmente.
    gate.approve_candidate(foundry.conn, candidate_id, backup_base_dir=foundry.backup_base_dir)

    from mercury_foundry.state import models
    candidate = models.get_candidate(foundry.conn, candidate_id)
    assert candidate["status"] == "approved", (
        "gate.approve_candidate non deve essere bloccato dal layer autonomy in V0"
    )


# ---------------------------------------------------------------------------
# 15 — modalità enforced applica il mandato (testato a livello di shadow.py)
# ---------------------------------------------------------------------------

def test_enforced_mode_blocks_on_escalation_required_mandate(tmp_path, monkeypatch):
    """In modalità enforced, un mandato escalation_required blocca la chiamata
    a maybe_check_governance con AutonomyBoundaryViolation."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "AUTONOMY_MODE", "enforced")

    conn = _fresh_conn(tmp_path)
    # FOUNDRY_GOVERNANCE ha CANDIDATE_APPROVAL → escalation_required di default.
    with pytest.raises(AutonomyBoundaryViolation) as exc_info:
        maybe_check_governance(
            conn,
            decision_type="CANDIDATE_APPROVAL",
            subject_type="candidate",
            subject_id="99",
        )

    assert "AUTONOMY_BOUNDARY_ENFORCED" in str(exc_info.value)
    assert "CANDIDATE_APPROVAL" in str(exc_info.value)


def test_enforced_mode_blocks_on_forbidden_mandate(tmp_path, monkeypatch):
    """In modalità enforced, un mandato forbidden blocca l'operazione."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "AUTONOMY_MODE", "enforced")

    conn = _fresh_conn(tmp_path)
    organ = am.get_organ_by_key(conn, "FOUNDRY_GOVERNANCE")
    mandate = am.get_mandate(conn, organ["id"], "PRODUCTION_DB_MUTATION")
    assert mandate["authority_mode"] == "forbidden"

    with pytest.raises(AutonomyBoundaryViolation) as exc_info:
        maybe_check_governance(
            conn,
            decision_type="PRODUCTION_DB_MUTATION",
            subject_type="db",
            subject_id="prod",
        )

    assert "AUTONOMY_BOUNDARY_ENFORCED" in str(exc_info.value)
    assert "PRODUCTION_DB_MUTATION" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 16 — audit scritto per ogni decisione
# ---------------------------------------------------------------------------

def test_audit_written_for_every_decision(tmp_path):
    """Ogni chiamata a authorize_organ_decision produce almeno un audit AUTONOMY_DECISION_*."""
    conn = _fresh_conn(tmp_path)
    organ_id = _make_organ(conn, "AUDIT_ORG")
    _make_mandate(conn, organ_id, "AUDIT_ACT", "autonomous")

    audit_before = list_audit_log(conn, limit=1000)
    n_before = len(audit_before)

    authorize_organ_decision(
        conn,
        organ_key="AUDIT_ORG",
        decision_type="AUDIT_ACT",
        subject_type="task",
        subject_id="99",
    )

    audit_after = list_audit_log(conn, limit=1000)
    n_after = len(audit_after)
    assert n_after > n_before, "Nessun audit prodotto dalla decisione"

    new_events = audit_after[n_before:]
    autonomy_events = [r for r in new_events if r["action"].startswith("AUTONOMY_")]
    assert len(autonomy_events) >= 1, "Nessun audit AUTONOMY_* trovato tra i nuovi eventi"


def test_audit_written_on_rejected_organ_not_found(tmp_path):
    """Anche per organo inesistente viene prodotto un audit AUTONOMY_DECISION_REJECTED."""
    conn = _fresh_conn(tmp_path)

    audit_before = list_audit_log(conn, limit=1000)

    authorize_organ_decision(
        conn,
        organ_key="NO_SUCH_ORGAN",
        decision_type="X",
        subject_type="goal",
        subject_id="1",
    )

    audit_after = list_audit_log(conn, limit=1000)
    new_events = audit_after[len(audit_before):]
    rejected = [r for r in new_events if r["action"] == "AUTONOMY_DECISION_REJECTED"]
    assert len(rejected) >= 1


# ---------------------------------------------------------------------------
# 17 — FOUNDRY_GOVERNANCE seedato con 4 mandati iniziali
# ---------------------------------------------------------------------------

def test_foundry_governance_pilot_organ_is_seeded(tmp_path):
    """L'organo pilota FOUNDRY_GOVERNANCE deve essere presente con 4 mandati."""
    conn = _fresh_conn(tmp_path)

    organ = am.get_organ_by_key(conn, GOVERNANCE_ORGAN_KEY)
    assert organ is not None, "FOUNDRY_GOVERNANCE non presente dopo init_schema"

    mandates = am.list_mandates_for_organ(conn, organ["id"])
    assert len(mandates) == len(INITIAL_MANDATES), (
        f"Attesi {len(INITIAL_MANDATES)} mandati, trovati {len(mandates)}"
    )

    decision_types = {m["decision_type"] for m in mandates}
    expected = {dt for dt, _ in INITIAL_MANDATES}
    assert decision_types == expected


def test_foundry_governance_initial_mandate_modes(tmp_path):
    """I mandati iniziali di FOUNDRY_GOVERNANCE hanno la modalità corretta."""
    conn = _fresh_conn(tmp_path)
    organ = am.get_organ_by_key(conn, GOVERNANCE_ORGAN_KEY)
    mandates_by_type = {m["decision_type"]: m["authority_mode"] for m in
                        am.list_mandates_for_organ(conn, organ["id"])}

    assert mandates_by_type["GOAL_STATUS_TRANSITION"] == "proposal"
    assert mandates_by_type["CANDIDATE_APPROVAL"] == "escalation_required"
    assert mandates_by_type["APPROVAL_REVOCATION"] == "escalation_required"
    assert mandates_by_type["PRODUCTION_DB_MUTATION"] == "forbidden"


# ---------------------------------------------------------------------------
# 18 — Regressione: flussi esistenti ancora funzionanti in shadow mode
# ---------------------------------------------------------------------------

def test_existing_flow_submit_run_goal_unaffected_in_shadow_mode(tmp_path, monkeypatch):
    """Il ciclo submit → run_goal funziona senza regressioni in shadow mode."""
    import mercury_foundry.config as cfg
    monkeypatch.setattr(cfg, "AUTONOMY_MODE", "shadow")

    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("regressione shadow mode")
    run_result = foundry.orchestrator.run_goal(goal_id)

    from mercury_foundry.state import models
    goal = models.get_goal(foundry.conn, goal_id)
    assert goal["status"] == "awaiting_approval"
    assert len(run_result.task_outcomes) == 1
    assert run_result.task_outcomes[0].candidate_id is not None


def test_doctor_returns_ready_shadow(tmp_path):
    """doctor restituisce READY_REPLICATION_CONTRACT_SHADOW dopo MF-REPL-001 (superset di READY_MISSION_SHADOW).

    MF-ARCH-008 garantiva READY_SHADOW. MF-MISSION-001 lo elevava a READY_MISSION_SHADOW.
    MF-REPL-001 lo eleva ulteriormente a READY_REPLICATION_CONTRACT_SHADOW quando anche
    il Replication Layer è inizializzato.
    """
    from mercury_foundry.diagnostics import run_doctor, OVERALL_READY_OUTCOME_SHADOW

    report = run_doctor(
        db_path=tmp_path / "mf.db",
        sandbox_root=tmp_path / "target",
        provider_name="fake",
    )
    assert report.overall_status == OVERALL_READY_OUTCOME_SHADOW
    assert not report.has_errors()

    check_names = {c.name for c in report.checks}
    for expected in [
        "autonomy_boundary_tables",
        "autonomy_boundary_flag",
        "autonomy_boundary_pilot_organ",
        "autonomy_boundary_mandates",
        "autonomy_boundary_no_duplicate_mandates",
        "autonomy_boundary_no_orphan_records",
        "autonomy_boundary_mode",
    ]:
        assert expected in check_names, f"Controllo doctor mancante: {expected}"

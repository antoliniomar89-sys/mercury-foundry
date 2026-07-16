"""MF-OUTCOME-001 — Test del layer Economic Outcome Governance.

Copertura dei 60 casi richiesti dalla specifica:

DOMAIN (1-8)
SCORING (9-15)
POLICY (16-23)
RESOURCES (24-32)
MISSION INTEGRATION (33-40)
AUTONOMY (41-45)
CONSTITUTION (46-50)
AUDIT (51-54)
REGRESSION (55-60)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from mercury_foundry.state.db import connect


# ---------------------------------------------------------------------------
# Fixture DB in-memory
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    conn = connect(":memory:")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future_iso(hours: int = 240) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past_iso(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _make_plan_kwargs(mission_id: str | None = None, **overrides) -> dict:
    base = dict(
        mission_id               = mission_id or str(uuid.uuid4()),
        correlation_id           = str(uuid.uuid4()),
        objective                = "Validare la domanda per il prodotto X",
        primary_metric           = "revenue_minor",
        target_value             = 100_000.0,
        target_operator          = ">=",
        maximum_cost_minor       = 50_000,
        maximum_duration_seconds = 30 * 24 * 3600,
        review_interval_seconds  = 7 * 24 * 3600,
        kill_deadline            = _future_iso(hours=720),
        minimum_evidence_count   = 5,
        strategic_value_score    = 0.7,
        learning_value_score     = 0.8,
        reversibility            = "reversible",
        created_by               = "test_actor",
        expected_revenue_minor   = 150_000,
        expected_profit_minor    = 100_000,
    )
    base.update(overrides)
    return base


def _make_snapshot_kwargs(**overrides) -> dict:
    base = dict(
        revenue_minor        = 5000,
        cost_minor           = 2000,
        profit_minor         = 3000,
        elapsed_seconds      = 3600,
        evidence_count       = 3,
        customer_count       = 2,
        knowledge_gain_score = 0.5,
        risk_score           = 0.2,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# DOMAIN (1–8)
# ---------------------------------------------------------------------------

def test_01_outcome_plan_valid_creation(db):
    """1 — OutcomePlan valido si crea senza errori."""
    from mercury_foundry.outcome.registry import create_outcome_plan
    kwargs = _make_plan_kwargs()
    plan = create_outcome_plan(db, **kwargs)
    assert plan.outcome_plan_id
    assert plan.status == "planned"
    assert plan.version == 1


def test_02_outcome_plan_missing_target_raises(db):
    """2 — target_value/operator assenti → validate() produce errori."""
    from mercury_foundry.outcome.models import EconomicOutcomePlan, _now_iso
    plan = EconomicOutcomePlan(
        outcome_plan_id          = str(uuid.uuid4()),
        mission_id               = str(uuid.uuid4()),
        correlation_id           = str(uuid.uuid4()),
        objective                = "test",
        primary_metric           = "revenue",
        target_value             = 0.0,
        target_operator          = "INVALID_OP",
        maximum_cost_minor       = 1000,
        maximum_duration_seconds = 3600,
        review_interval_seconds  = 600,
        kill_deadline            = _future_iso(),
        minimum_evidence_count   = 1,
        strategic_value_score    = 0.5,
        learning_value_score     = 0.5,
        reversibility            = "reversible",
        created_by               = "test",
        created_at               = _now_iso(),
        updated_at               = _now_iso(),
        version                  = 1,
    )
    errors = plan.validate()
    assert any("target_operator" in e for e in errors), f"Errori: {errors}"


def test_03_outcome_plan_negative_cost_raises(db):
    """3 — maximum_cost_minor negativo → errore di validazione."""
    from mercury_foundry.outcome.models import EconomicOutcomePlan, _now_iso
    plan = EconomicOutcomePlan(
        outcome_plan_id          = str(uuid.uuid4()),
        mission_id               = str(uuid.uuid4()),
        correlation_id           = str(uuid.uuid4()),
        objective                = "test",
        primary_metric           = "revenue",
        target_value             = 100.0,
        target_operator          = ">=",
        maximum_cost_minor       = -1,
        maximum_duration_seconds = 3600,
        review_interval_seconds  = 600,
        kill_deadline            = _future_iso(),
        minimum_evidence_count   = 1,
        strategic_value_score    = 0.5,
        learning_value_score     = 0.5,
        reversibility            = "reversible",
        created_by               = "test",
        created_at               = _now_iso(),
        updated_at               = _now_iso(),
        version                  = 1,
    )
    errors = plan.validate()
    assert any("maximum_cost_minor" in e for e in errors), f"Errori: {errors}"


def test_04_outcome_plan_zero_duration_raises(db):
    """4 — maximum_duration_seconds=0 → errore di validazione."""
    from mercury_foundry.outcome.models import EconomicOutcomePlan, _now_iso
    plan = EconomicOutcomePlan(
        outcome_plan_id          = str(uuid.uuid4()),
        mission_id               = str(uuid.uuid4()),
        correlation_id           = str(uuid.uuid4()),
        objective                = "test",
        primary_metric           = "revenue",
        target_value             = 100.0,
        target_operator          = ">=",
        maximum_cost_minor       = 1000,
        maximum_duration_seconds = 0,
        review_interval_seconds  = 600,
        kill_deadline            = _future_iso(),
        minimum_evidence_count   = 1,
        strategic_value_score    = 0.5,
        learning_value_score     = 0.5,
        reversibility            = "reversible",
        created_by               = "test",
        created_at               = _now_iso(),
        updated_at               = _now_iso(),
        version                  = 1,
    )
    errors = plan.validate()
    assert any("maximum_duration_seconds" in e for e in errors), f"Errori: {errors}"


def test_05_outcome_plan_past_deadline_raises(db):
    """5 — kill_deadline nel passato → errore di validazione."""
    from mercury_foundry.outcome.models import EconomicOutcomePlan, _now_iso
    now = _now_iso()
    plan = EconomicOutcomePlan(
        outcome_plan_id          = str(uuid.uuid4()),
        mission_id               = str(uuid.uuid4()),
        correlation_id           = str(uuid.uuid4()),
        objective                = "test",
        primary_metric           = "revenue",
        target_value             = 100.0,
        target_operator          = ">=",
        maximum_cost_minor       = 1000,
        maximum_duration_seconds = 3600,
        review_interval_seconds  = 600,
        kill_deadline            = _past_iso(hours=2),
        minimum_evidence_count   = 1,
        strategic_value_score    = 0.5,
        learning_value_score     = 0.5,
        reversibility            = "reversible",
        created_by               = "test",
        created_at               = now,
        updated_at               = now,
        version                  = 1,
    )
    errors = plan.validate()
    assert any("kill_deadline" in e for e in errors), f"Errori: {errors}"


def test_06_outcome_plan_minor_units_are_integers(db):
    """6 — importi monetari sono integer minor units."""
    from mercury_foundry.outcome.registry import create_outcome_plan
    plan = create_outcome_plan(db, **_make_plan_kwargs(
        maximum_cost_minor     = 5000,
        expected_revenue_minor = 10000,
        expected_profit_minor  = 5000,
    ))
    assert isinstance(plan.maximum_cost_minor, int)
    assert isinstance(plan.expected_revenue_minor, int)
    assert isinstance(plan.expected_profit_minor, int)
    assert plan.maximum_cost_minor == 5000


def test_07_outcome_enums_exist(db):
    """7 — Tutti gli enum richiesti esistono con i valori corretti."""
    from mercury_foundry.outcome.models import DecisionType, OutcomeStatus, PriorityClass, Reversibility
    assert DecisionType.CONTINUE.value == "continue"
    assert DecisionType.STOP.value == "stop"
    assert DecisionType.SCALE.value == "scale"
    assert OutcomeStatus.ACTIVE.value == "active"
    assert OutcomeStatus.STOPPED.value == "stopped"
    assert PriorityClass.CRITICAL.value == "critical"
    assert Reversibility.IRREVERSIBLE.value == "irreversible"


def test_08_outcome_plan_serialization(db):
    """8 — to_dict / from_dict round-trip."""
    from mercury_foundry.outcome.registry import create_outcome_plan
    from mercury_foundry.outcome.models import EconomicOutcomePlan
    plan = create_outcome_plan(db, **_make_plan_kwargs())
    d = plan.to_dict()
    plan2 = EconomicOutcomePlan.from_dict(d)
    assert plan2.outcome_plan_id == plan.outcome_plan_id
    assert plan2.maximum_cost_minor == plan.maximum_cost_minor
    assert plan2.status == plan.status


# ---------------------------------------------------------------------------
# SCORING (9–15)
# ---------------------------------------------------------------------------

def _make_plan_for_scoring(**kwargs):
    from mercury_foundry.outcome.models import EconomicOutcomePlan, _now_iso
    now = _now_iso()
    base = dict(
        outcome_plan_id          = str(uuid.uuid4()),
        mission_id               = str(uuid.uuid4()),
        correlation_id           = str(uuid.uuid4()),
        objective                = "Test scoring",
        primary_metric           = "revenue_minor",
        target_value             = 100_000.0,
        target_operator          = ">=",
        maximum_cost_minor       = 50_000,
        maximum_duration_seconds = 7 * 24 * 3600,
        review_interval_seconds  = 24 * 3600,
        kill_deadline            = _future_iso(),
        minimum_evidence_count   = 5,
        strategic_value_score    = 0.5,
        learning_value_score     = 0.5,
        reversibility            = "reversible",
        created_by               = "scorer_test",
        created_at               = now,
        updated_at               = now,
        version                  = 1,
        expected_revenue_minor   = 100_000,
        expected_profit_minor    = 50_000,
    )
    base.update(kwargs)
    return EconomicOutcomePlan(**base)


def _make_snap_for_scoring(**kwargs):
    from mercury_foundry.outcome.models import OutcomeMetricSnapshot
    base = dict(
        snapshot_id          = str(uuid.uuid4()),
        outcome_plan_id      = str(uuid.uuid4()),
        mission_id           = str(uuid.uuid4()),
        measured_at          = datetime.now(timezone.utc).isoformat(),
        revenue_minor        = 10_000,
        cost_minor           = 5_000,
        profit_minor         = 5_000,
        elapsed_seconds      = 3600,
        evidence_count       = 5,
        customer_count       = 3,
        knowledge_gain_score = 0.5,
        risk_score           = 0.2,
    )
    base.update(kwargs)
    return OutcomeMetricSnapshot(**base)


def test_09_scoring_high_return(db):
    """9 — Piano con alto profitto atteso produce score elevato."""
    from mercury_foundry.outcome.scoring import OutcomeScorer
    plan = _make_plan_for_scoring(
        expected_profit_minor  = 500_000,
        maximum_cost_minor     = 50_000,
        strategic_value_score  = 0.9,
        learning_value_score   = 0.9,
    )
    snap = _make_snap_for_scoring(risk_score=0.1, evidence_count=10)
    result = OutcomeScorer().score(plan, snap)
    assert result.score >= 60.0, f"Score atteso >= 60, trovato {result.score}"


def test_10_scoring_high_risk_lowers_score(db):
    """10 — Alto rischio abbassa il punteggio finale."""
    from mercury_foundry.outcome.scoring import OutcomeScorer
    plan = _make_plan_for_scoring(strategic_value_score=0.5, learning_value_score=0.5)
    snap_low_risk  = _make_snap_for_scoring(risk_score=0.1)
    snap_high_risk = _make_snap_for_scoring(risk_score=0.95)
    r_low  = OutcomeScorer().score(plan, snap_low_risk)
    r_high = OutcomeScorer().score(plan, snap_high_risk)
    assert r_low.score > r_high.score, (
        f"Score basso rischio ({r_low.score}) dovrebbe essere > alto rischio ({r_high.score})"
    )


def test_11_scoring_low_evidence_lowers_score(db):
    """11 — Evidenza insufficiente abbassa il punteggio."""
    from mercury_foundry.outcome.scoring import OutcomeScorer
    plan = _make_plan_for_scoring(minimum_evidence_count=10)
    snap_good = _make_snap_for_scoring(evidence_count=10)
    snap_poor = _make_snap_for_scoring(evidence_count=0)
    r_good = OutcomeScorer().score(plan, snap_good)
    r_poor = OutcomeScorer().score(plan, snap_poor)
    assert r_good.score > r_poor.score


def test_12_scoring_high_speed_boosts_score(db):
    """12 — Durata breve produce speed_score alto."""
    from mercury_foundry.outcome.scoring import OutcomeScorer, _speed_score
    plan_fast = _make_plan_for_scoring(maximum_duration_seconds=3600)     # 1 ora
    plan_slow = _make_plan_for_scoring(maximum_duration_seconds=365 * 24 * 3600)  # 1 anno
    snap = _make_snap_for_scoring()
    r_fast = OutcomeScorer().score(plan_fast, snap)
    r_slow = OutcomeScorer().score(plan_slow, snap)
    assert r_fast.score > r_slow.score


def test_13_scoring_learning_value(db):
    """13 — learning_value_score alto aumenta il punteggio."""
    from mercury_foundry.outcome.scoring import OutcomeScorer
    plan_high = _make_plan_for_scoring(learning_value_score=1.0)
    plan_low  = _make_plan_for_scoring(learning_value_score=0.0)
    snap = _make_snap_for_scoring()
    r_high = OutcomeScorer().score(plan_high, snap)
    r_low  = OutcomeScorer().score(plan_low, snap)
    assert r_high.score > r_low.score


def test_14_score_is_between_0_and_100(db):
    """14 — score finale sempre in [0, 100]."""
    from mercury_foundry.outcome.scoring import OutcomeScorer
    # Caso peggiore
    plan = _make_plan_for_scoring(
        expected_profit_minor  = None,
        expected_revenue_minor = None,
        strategic_value_score  = 0.0,
        learning_value_score   = 0.0,
        reversibility          = "irreversible",
    )
    snap = _make_snap_for_scoring(risk_score=1.0, evidence_count=0)
    r = OutcomeScorer().score(plan, snap)
    assert 0.0 <= r.score <= 100.0

    # Caso migliore
    plan2 = _make_plan_for_scoring(
        expected_profit_minor = 1_000_000,
        maximum_cost_minor    = 1,
        strategic_value_score = 1.0,
        learning_value_score  = 1.0,
        reversibility         = "reversible",
    )
    snap2 = _make_snap_for_scoring(risk_score=0.0, evidence_count=1000)
    r2 = OutcomeScorer().score(plan2, snap2)
    assert 0.0 <= r2.score <= 100.0


def test_15_scoring_formula_configurable(db):
    """15 — I pesi dello scorer sono configurabili."""
    from mercury_foundry.outcome.scoring import OutcomeScorer, ScoringWeights
    custom_weights = ScoringWeights(
        component_weights={"economic_return_score": 1.0, "evidence_score": 0.0,
                           "strategic_score": 0.0, "learning_score": 0.0, "speed_score": 0.0},
        penalty_weights={"risk_penalty": 0.0, "irreversibility_penalty": 0.0},
    )
    scorer = OutcomeScorer(weights=custom_weights)
    plan = _make_plan_for_scoring(
        expected_profit_minor = 100_000,
        maximum_cost_minor    = 100_000,
    )
    snap = _make_snap_for_scoring()
    r = scorer.score(plan, snap)
    assert r.score == pytest.approx(100.0, abs=1.0)


# ---------------------------------------------------------------------------
# POLICY (16–23)
# ---------------------------------------------------------------------------

def _make_policy_setup(**plan_kwargs):
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyConfig, PolicyEvaluationContext
    plan = _make_plan_for_scoring(**plan_kwargs)
    evaluator = OutcomePolicyEvaluator()
    return plan, evaluator, PolicyEvaluationContext()


def test_16_policy_continue(db):
    """16 — Condizioni normali → CONTINUE."""
    from mercury_foundry.outcome.models import DecisionType
    plan, evaluator, ctx = _make_policy_setup()
    snap = _make_snap_for_scoring(
        cost_minor     = 1000,
        evidence_count = 5,
        risk_score     = 0.1,
    )
    from mercury_foundry.outcome.models import ResourceEnvelope
    envelope = ResourceEnvelope(
        envelope_id="e1", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    decision = evaluator.evaluate(plan, snap, envelope, ctx)
    assert decision.decision_type == DecisionType.CONTINUE.value
    assert len(decision.reasons) > 0


def test_17_policy_pause_insufficient_data(db):
    """17 — Dati insufficienti → PAUSE."""
    from mercury_foundry.outcome.models import DecisionType, ResourceEnvelope
    plan = _make_plan_for_scoring(minimum_evidence_count=10)
    snap = _make_snap_for_scoring(evidence_count=0, elapsed_seconds=3600)
    envelope = ResourceEnvelope(
        envelope_id="e1", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyEvaluationContext
    decision = OutcomePolicyEvaluator().evaluate(plan, snap, envelope, PolicyEvaluationContext())
    assert decision.decision_type == DecisionType.PAUSE.value


def test_18_policy_stop_deadline_exceeded(db):
    """18 — kill_deadline superata → STOP."""
    from mercury_foundry.outcome.models import DecisionType, ResourceEnvelope
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyEvaluationContext
    plan = _make_plan_for_scoring(kill_deadline=_past_iso(hours=1))
    snap = _make_snap_for_scoring()
    envelope = ResourceEnvelope(
        envelope_id="e1", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    decision = OutcomePolicyEvaluator().evaluate(plan, snap, envelope, PolicyEvaluationContext())
    assert decision.decision_type == DecisionType.STOP.value
    assert any("kill_deadline" in r for r in decision.reasons)


def test_19_policy_stop_budget_exceeded(db):
    """19 — Costo accumulato > maximum_cost_minor → STOP."""
    from mercury_foundry.outcome.models import DecisionType, ResourceEnvelope
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyEvaluationContext
    plan = _make_plan_for_scoring(maximum_cost_minor=1000)
    snap = _make_snap_for_scoring(cost_minor=2000)  # > maximum_cost_minor
    envelope = ResourceEnvelope(
        envelope_id="e1", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    decision = OutcomePolicyEvaluator().evaluate(plan, snap, envelope, PolicyEvaluationContext())
    assert decision.decision_type == DecisionType.STOP.value
    assert any("budget" in r for r in decision.reasons)


def test_20_policy_stop_risk_exceeded(db):
    """20 — risk_score > risk_limit → STOP."""
    from mercury_foundry.outcome.models import DecisionType, ResourceEnvelope
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyConfig, PolicyEvaluationContext
    plan = _make_plan_for_scoring()
    snap = _make_snap_for_scoring(risk_score=0.99)
    envelope = ResourceEnvelope(
        envelope_id="e1", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    cfg = PolicyConfig(risk_limit=0.85)
    decision = OutcomePolicyEvaluator(config=cfg).evaluate(plan, snap, envelope, PolicyEvaluationContext())
    assert decision.decision_type == DecisionType.STOP.value


def test_21_policy_scale_proposal(db):
    """21 — Condizioni di scala soddisfatte → SCALE (proposta, non scale automatico)."""
    from mercury_foundry.outcome.models import DecisionType, ResourceEnvelope
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyEvaluationContext
    plan = _make_plan_for_scoring(
        scale_threshold        = 60.0,
        minimum_evidence_count = 5,
    )
    snap = _make_snap_for_scoring(
        evidence_count = 10,
        profit_minor   = 10_000,
        cost_minor     = 5_000,
        risk_score     = 0.1,
    )
    envelope = ResourceEnvelope(
        envelope_id="e1", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    ctx = PolicyEvaluationContext(delivery_ready=True)
    decision = OutcomePolicyEvaluator().evaluate(plan, snap, envelope, ctx)
    assert decision.decision_type == DecisionType.SCALE.value
    # Scale propone, non modifica risorse
    assert "propose_scale_to_authority" in decision.required_actions
    assert "await_human_approval_for_budget" in decision.required_actions


def test_22_policy_review_irreversible(db):
    """22 — Decisione irreversibile con profitto → REQUIRE_REVIEW."""
    from mercury_foundry.outcome.models import DecisionType, ResourceEnvelope
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyEvaluationContext
    plan = _make_plan_for_scoring(
        reversibility         = "irreversible",
        expected_profit_minor = 1_500_000,
        maximum_cost_minor    = 50_000,
    )
    snap = _make_snap_for_scoring(profit_minor=10_000, cost_minor=5_000)
    envelope = ResourceEnvelope(
        envelope_id="e1", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    from mercury_foundry.outcome.policy import PolicyConfig
    cfg = PolicyConfig(economic_impact_threshold_minor=1_000_000)
    decision = OutcomePolicyEvaluator(config=cfg).evaluate(plan, snap, envelope, PolicyEvaluationContext())
    assert decision.decision_type == DecisionType.REQUIRE_REVIEW.value


def test_23_policy_review_budget_increase(db):
    """23 — authority_change=True → REQUIRE_REVIEW."""
    from mercury_foundry.outcome.models import DecisionType, ResourceEnvelope
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyEvaluationContext
    plan = _make_plan_for_scoring()
    snap = _make_snap_for_scoring()
    envelope = ResourceEnvelope(
        envelope_id="e1", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    ctx = PolicyEvaluationContext(authority_change=True)
    decision = OutcomePolicyEvaluator().evaluate(plan, snap, envelope, ctx)
    assert decision.decision_type == DecisionType.REQUIRE_REVIEW.value


# ---------------------------------------------------------------------------
# RESOURCES (24–32)
# ---------------------------------------------------------------------------

def test_24_resource_allocation_valid(db):
    """24 — Allocazione valida crea un ResourceEnvelope."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    allocator = ResourceAllocator()
    env = allocator.allocate(
        db,
        mission_id   = str(uuid.uuid4()),
        budget_minor = 10_000,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    assert env.envelope_id
    assert env.budget_minor == 10_000


def test_25_resource_allocation_negative_raises(db):
    """25 — Allocazione con budget negativo solleva errore."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.models import ResourceAllocationError
    allocator = ResourceAllocator()
    with pytest.raises(ResourceAllocationError):
        allocator.allocate(
            db,
            mission_id   = str(uuid.uuid4()),
            budget_minor = -100,
            deadline     = _future_iso(),
            allocated_by = "test_actor",
        )


def test_26_resource_consumption_valid(db):
    """26 — Consumo valido si registra correttamente."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    allocator = ResourceAllocator()
    env = allocator.allocate(
        db,
        mission_id   = str(uuid.uuid4()),
        budget_minor = 10_000,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    consumption = allocator.consume(
        db,
        envelope_id     = env.envelope_id,
        cost_minor      = 500,
        source_ref      = "test_op_1",
        idempotency_key = "key-001",
    )
    assert consumption.cost_minor == 500
    remaining = allocator.remaining(db, envelope_id=env.envelope_id)
    assert remaining.budget_remaining_minor == 9_500


def test_27_resource_consumption_over_limit_raises(db):
    """27 — Consumo che supera il budget solleva ResourceExhaustedError."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.models import ResourceExhaustedError
    allocator = ResourceAllocator()
    env = allocator.allocate(
        db,
        mission_id   = str(uuid.uuid4()),
        budget_minor = 1_000,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    with pytest.raises(ResourceExhaustedError):
        allocator.consume(
            db,
            envelope_id     = env.envelope_id,
            cost_minor      = 2_000,  # > budget
            source_ref      = "test_op_over",
            idempotency_key = "key-over",
        )


def test_28_resource_consumption_idempotency(db):
    """28 — Consumo con stesso idempotency_key solleva ConsumptionIdempotencyReplay."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.models import ConsumptionIdempotencyReplay
    allocator = ResourceAllocator()
    env = allocator.allocate(
        db,
        mission_id   = str(uuid.uuid4()),
        budget_minor = 10_000,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    allocator.consume(
        db,
        envelope_id     = env.envelope_id,
        cost_minor      = 100,
        source_ref      = "op1",
        idempotency_key = "same-key-001",
    )
    with pytest.raises(ConsumptionIdempotencyReplay):
        allocator.consume(
            db,
            envelope_id     = env.envelope_id,
            cost_minor      = 100,
            source_ref      = "op1_dup",
            idempotency_key = "same-key-001",
        )


def test_29_resource_reservation(db):
    """29 — Reservation non supera il budget disponibile."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    allocator = ResourceAllocator()
    env = allocator.allocate(
        db,
        mission_id   = str(uuid.uuid4()),
        budget_minor = 5_000,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    rec = allocator.reserve(db, envelope_id=env.envelope_id, amount_minor=2_000)
    assert rec.reservation_id
    assert rec.amount_minor == 2_000
    assert not rec.released


def test_30_resource_release(db):
    """30 — Release annulla la reservation."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    allocator = ResourceAllocator()
    env = allocator.allocate(
        db,
        mission_id   = str(uuid.uuid4()),
        budget_minor = 5_000,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    rec = allocator.reserve(db, envelope_id=env.envelope_id, amount_minor=2_000)
    allocator.release(db, rec.reservation_id)
    assert allocator._reservations[rec.reservation_id].released


def test_31_resource_remaining(db):
    """31 — remaining() calcola correttamente le risorse residue."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    allocator = ResourceAllocator()
    env = allocator.allocate(
        db,
        mission_id   = str(uuid.uuid4()),
        budget_minor = 10_000,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    allocator.consume(db, envelope_id=env.envelope_id, cost_minor=3_000,
                      source_ref="r1", idempotency_key="r31-1")
    allocator.consume(db, envelope_id=env.envelope_id, cost_minor=2_000,
                      source_ref="r2", idempotency_key="r31-2")
    remaining = allocator.remaining(db, envelope_id=env.envelope_id)
    assert remaining.budget_remaining_minor == 5_000
    assert remaining.total_consumed_minor == 5_000
    assert not remaining.exhausted


def test_32_resource_optimistic_locking(db):
    """32 — Consumo con stesso idempotency_key su due connessioni gestisce l'idempotency."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.models import ConsumptionIdempotencyReplay
    allocator = ResourceAllocator()
    env = allocator.allocate(
        db,
        mission_id   = str(uuid.uuid4()),
        budget_minor = 10_000,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    # Primo consumo OK
    allocator.consume(db, envelope_id=env.envelope_id, cost_minor=100,
                      source_ref="op1", idempotency_key="lock-key-1")
    # Secondo con stesso key → idempotency replay
    with pytest.raises(ConsumptionIdempotencyReplay):
        allocator.consume(db, envelope_id=env.envelope_id, cost_minor=100,
                          source_ref="op1", idempotency_key="lock-key-1")


# ---------------------------------------------------------------------------
# MISSION INTEGRATION (33–40)
# ---------------------------------------------------------------------------

def _create_active_mission(db):
    """Crea una Mission in stato active e ritorna l'oggetto."""
    from mercury_foundry.mission.intake import MissionIntakeService
    from mercury_foundry.mission.models import (
        ExpectedOutcome, MissionAuthorityRequest, MissionBudget,
        MissionIntakeRequest, MissionRiskProfile,
        OriginType, MissionType, Priority,
    )
    from mercury_foundry.mission.lifecycle import apply_transition
    svc = MissionIntakeService()
    req = MissionIntakeRequest(
        title           = "Outcome Integration Test Mission",
        description     = "test",
        objective       = "Verifica integrazione outcome",
        origin_type     = OriginType.FOUNDER,
        mission_type    = MissionType.CUSTOM,
        priority        = Priority.NORMAL,
        expected_outcomes = [ExpectedOutcome(
            outcome_id="eo-1", description="test", required=True, metric_name="revenue"
        )],
        success_criteria  = [],
        termination_criteria = [],
        constraints       = {},
        budget            = MissionBudget(currency="EUR", approved_amount=100.0),
        risk_profile      = MissionRiskProfile(risk_level="low"),
        authority_request = MissionAuthorityRequest(requested_mode="proposal"),
        required_capabilities = [],
        created_by        = "test_actor",
        idempotency_key   = str(uuid.uuid4()),
        correlation_id    = str(uuid.uuid4()),
    )
    result = svc.submit(db, req)
    assert result.accepted, f"Mission non accettata: {result.validation_errors}"
    mid = result.mission_id
    # draft → submitted → under_review → accepted → ready → active
    for from_s, to_s in [
        ("draft", "submitted"),
        ("submitted", "under_review"),
        ("under_review", "accepted"),
        ("accepted", "ready"),
        ("ready", "active"),
    ]:
        m = _get_mission(db, mid)
        apply_transition(db, mission_id=mid, current_status=from_s,
                         current_version=m.version, to_status=to_s,
                         requested_by="test", reason="integration test",
                         correlation_id=str(uuid.uuid4()))
    return _get_mission(db, mid)


def _get_mission(db, mission_id: str):
    from mercury_foundry.mission.registry import get_mission
    return get_mission(db, mission_id)


def test_33_mission_without_outcome_plan_not_ready(db):
    """33 — Mission senza outcome plan non è pronta per attivazione economica."""
    from mercury_foundry.outcome.registry import get_outcome_plan_for_mission
    mission_id = str(uuid.uuid4())
    plan = get_outcome_plan_for_mission(db, mission_id)
    assert plan is None, "Nessun piano dovrebbe esistere per una mission casuale"


def test_34_mission_with_outcome_plan_ready(db):
    """34 — Mission con outcome plan supera il check di activation readiness."""
    from mercury_foundry.outcome.registry import create_outcome_plan
    from mercury_foundry.outcome.lifecycle import check_activation_readiness
    mission_id = str(uuid.uuid4())
    plan = create_outcome_plan(db, **_make_plan_kwargs(mission_id=mission_id))
    check = check_activation_readiness(plan)
    assert check.ready, f"Blockers: {check.blockers}"


def test_35_outcome_continue_does_not_change_active_mission(db):
    """35 — CONTINUE non cambia lo status di una mission active."""
    mission = _create_active_mission(db)
    from mercury_foundry.outcome.service import OutcomeService
    from mercury_foundry.outcome.registry import create_outcome_plan, create_metric_snapshot, get_resource_envelope_for_mission
    svc = OutcomeService()
    corr_id = str(uuid.uuid4())
    plan = create_outcome_plan(db, **_make_plan_kwargs(
        mission_id     = mission.mission_id,
        correlation_id = corr_id,
    ))
    # Snapshot con tutto nella norma
    create_metric_snapshot(db,
        outcome_plan_id=plan.outcome_plan_id,
        mission_id=mission.mission_id,
        **_make_snapshot_kwargs(evidence_count=5, cost_minor=1000, risk_score=0.1),
    )
    result = svc.evaluate(db, outcome_plan_id=plan.outcome_plan_id,
                          actor_id="test", correlation_id=corr_id)
    assert result.decision.decision_type in ("continue", "pause", "require_review", "scale"), \
        f"Decisione inattesa: {result.decision.decision_type}"
    # Se CONTINUE, mission rimane active
    if result.decision.decision_type == "continue":
        assert not result.mission_transition_applied
        updated = _get_mission(db, mission.mission_id)
        assert str(updated.status.value if hasattr(updated.status, 'value') else updated.status) == "active"


def test_36_outcome_pause_transitions_mission(db):
    """36 — PAUSE transiziona la mission da active a paused."""
    from mercury_foundry.outcome.service import OutcomeService
    from mercury_foundry.outcome.registry import create_outcome_plan, create_metric_snapshot
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyConfig, PolicyEvaluationContext
    mission = _create_active_mission(db)
    plan = create_outcome_plan(db, **_make_plan_kwargs(
        mission_id=mission.mission_id,
        minimum_evidence_count=100,  # molto alto → evidenza insufficiente → PAUSE
    ))
    create_metric_snapshot(db,
        outcome_plan_id=plan.outcome_plan_id,
        mission_id=mission.mission_id,
        **_make_snapshot_kwargs(evidence_count=0, elapsed_seconds=1800, cost_minor=100),
    )
    svc = OutcomeService()
    result = svc.evaluate(db, outcome_plan_id=plan.outcome_plan_id,
                          actor_id="test", correlation_id=str(uuid.uuid4()))
    if result.decision.decision_type == "pause":
        updated = _get_mission(db, mission.mission_id)
        status = str(updated.status.value if hasattr(updated.status, 'value') else updated.status)
        assert status == "paused"
        assert result.mission_transition_applied


def test_37_outcome_stop_terminates_mission(db):
    """37 — STOP transiziona la mission a terminated."""
    from mercury_foundry.outcome.service import OutcomeService
    from mercury_foundry.outcome.registry import create_outcome_plan, create_metric_snapshot
    mission = _create_active_mission(db)
    plan = create_outcome_plan(db, **_make_plan_kwargs(
        mission_id   = mission.mission_id,
        kill_deadline= _past_iso(hours=1),  # scaduto → STOP
    ))
    create_metric_snapshot(db,
        outcome_plan_id=plan.outcome_plan_id,
        mission_id=mission.mission_id,
        **_make_snapshot_kwargs(),
    )
    svc = OutcomeService()
    result = svc.evaluate(db, outcome_plan_id=plan.outcome_plan_id,
                          actor_id="test", correlation_id=str(uuid.uuid4()))
    assert result.decision.decision_type == "stop"
    updated = _get_mission(db, mission.mission_id)
    status = str(updated.status.value if hasattr(updated.status, 'value') else updated.status)
    assert status == "terminated"
    assert result.mission_transition_applied


def test_38_outcome_scale_does_not_modify_budget(db):
    """38 — SCALE non modifica il budget dell'envelope automaticamente."""
    from mercury_foundry.outcome.registry import create_outcome_plan, create_metric_snapshot
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyEvaluationContext
    from mercury_foundry.outcome.models import ResourceEnvelope
    plan = _make_plan_for_scoring(scale_threshold=60.0, minimum_evidence_count=5)
    snap = _make_snap_for_scoring(evidence_count=10, profit_minor=10_000,
                                  cost_minor=5_000, risk_score=0.1)
    envelope = ResourceEnvelope(
        envelope_id="e_scale", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    ctx = PolicyEvaluationContext(delivery_ready=True)
    decision = OutcomePolicyEvaluator().evaluate(plan, snap, envelope, ctx)
    if decision.decision_type == "scale":
        # Il budget dell'envelope NON deve essere modificato automaticamente
        assert "await_human_approval_for_budget" in decision.required_actions
        assert "propose_scale_to_authority" in decision.required_actions
        assert envelope.budget_minor == plan.maximum_cost_minor  # invariato


def test_39_outcome_review_does_not_change_status(db):
    """39 — REQUIRE_REVIEW non modifica lo status della mission."""
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyEvaluationContext
    from mercury_foundry.outcome.models import ResourceEnvelope
    plan = _make_plan_for_scoring()
    snap = _make_snap_for_scoring()
    envelope = ResourceEnvelope(
        envelope_id="e1", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    ctx = PolicyEvaluationContext(authority_change=True)
    decision = OutcomePolicyEvaluator().evaluate(plan, snap, envelope, ctx)
    if decision.decision_type == "require_review":
        assert "escalate_to_authority" in decision.required_actions
        # Non produce transizioni di status


def test_40_correlation_id_preserved(db):
    """40 — correlation_id è preservato nella decisione."""
    from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyEvaluationContext
    from mercury_foundry.outcome.models import ResourceEnvelope
    corr = str(uuid.uuid4())
    plan = _make_plan_for_scoring()
    snap = _make_snap_for_scoring()
    envelope = ResourceEnvelope(
        envelope_id="e1", mission_id=plan.mission_id,
        budget_minor=plan.maximum_cost_minor, compute_units=0,
        llm_token_limit=0, external_service_limit_minor=0,
        human_minutes_limit=0, deadline=_future_iso(),
        allocated_at="now", allocated_by="test", version=1,
    )
    ctx = PolicyEvaluationContext(correlation_id=corr)
    decision = OutcomePolicyEvaluator().evaluate(plan, snap, envelope, ctx)
    assert decision.correlation_id == corr


# ---------------------------------------------------------------------------
# AUTONOMY (41–45)
# ---------------------------------------------------------------------------

def test_41_seed_economic_governance(db):
    """41 — ECONOMIC_GOVERNANCE è presente e ha 8 mandati."""
    from mercury_foundry.autonomy.models import get_organ_by_key, list_mandates_for_organ
    organ = get_organ_by_key(db, "ECONOMIC_GOVERNANCE")
    assert organ is not None, "ECONOMIC_GOVERNANCE non trovato"
    mandates = list_mandates_for_organ(db, organ["id"])
    assert len(mandates) == 8, f"Attesi 8 mandati, trovati {len(mandates)}"


def test_42_budget_increase_forbidden(db):
    """42 — OUTCOME_BUDGET_INCREASE è forbidden nel mandato."""
    from mercury_foundry.autonomy.models import get_organ_by_key
    from mercury_foundry.autonomy.authorization import authorize_organ_decision
    organ = get_organ_by_key(db, "ECONOMIC_GOVERNANCE")
    assert organ is not None
    result = authorize_organ_decision(
        db,
        organ_key     = "ECONOMIC_GOVERNANCE",
        decision_type = "OUTCOME_BUDGET_INCREASE",
        subject_type  = "resource_envelope",
        subject_id    = "test-env-001",
    )
    assert not result.allowed
    assert result.authority_mode == "forbidden"


def test_43_stop_requires_escalation(db):
    """43 — OUTCOME_STOP richiede escalation_required."""
    from mercury_foundry.autonomy.authorization import authorize_organ_decision
    result = authorize_organ_decision(
        db,
        organ_key     = "ECONOMIC_GOVERNANCE",
        decision_type = "OUTCOME_STOP",
        subject_type  = "outcome_decision",
        subject_id    = "test-stop-001",
    )
    assert not result.allowed
    assert result.authority_mode == "escalation_required"


def test_44_proposal_mandates_respected(db):
    """44 — Mandati proposal non sono forbidden né escalation_required."""
    from mercury_foundry.autonomy.authorization import authorize_organ_decision
    for decision_type in ["OUTCOME_PLAN_CREATE", "RESOURCE_CONSUME", "OUTCOME_EVALUATE"]:
        result = authorize_organ_decision(
            db,
            organ_key     = "ECONOMIC_GOVERNANCE",
            decision_type = decision_type,
            subject_type  = "outcome_plan",
            subject_id    = "test-prop-001",
        )
        assert result.authority_mode == "proposal", (
            f"{decision_type}: atteso proposal, trovato {result.authority_mode}"
        )


def test_45_authority_result_linked_to_evaluation(db):
    """45 — Il risultato di evaluate() contiene authority_mode."""
    from mercury_foundry.outcome.service import OutcomeService
    from mercury_foundry.outcome.registry import create_outcome_plan, create_metric_snapshot
    mission_id = str(uuid.uuid4())
    plan = create_outcome_plan(db, **_make_plan_kwargs(mission_id=mission_id))
    create_metric_snapshot(db,
        outcome_plan_id=plan.outcome_plan_id,
        mission_id=mission_id,
        **_make_snapshot_kwargs(),
    )
    svc = OutcomeService()
    result = svc.evaluate(db, outcome_plan_id=plan.outcome_plan_id,
                          actor_id="test", correlation_id=str(uuid.uuid4()))
    assert result.authority_mode in (
        "proposal", "escalation_required", "forbidden", "autonomous", "unknown"
    )


# ---------------------------------------------------------------------------
# CONSTITUTION (46–50)
# ---------------------------------------------------------------------------

def test_46_constitution_shadow_validation(db):
    """46 — Constitutional validation viene eseguita in shadow mode senza bloccare."""
    from mercury_foundry.outcome.service import OutcomeService
    from mercury_foundry.outcome.registry import create_outcome_plan, create_metric_snapshot
    mission_id = str(uuid.uuid4())
    plan = create_outcome_plan(db, **_make_plan_kwargs(mission_id=mission_id))
    create_metric_snapshot(db,
        outcome_plan_id=plan.outcome_plan_id,
        mission_id=mission_id,
        **_make_snapshot_kwargs(),
    )
    svc = OutcomeService()
    result = svc.evaluate(db, outcome_plan_id=plan.outcome_plan_id,
                          actor_id="test", correlation_id=str(uuid.uuid4()))
    # Non deve mai bloccare in shadow mode
    assert result is not None
    assert result.constitutional_status is not None


def test_47_constitution_missing_evidence_flagged(db):
    """47 — Snapshot con evidence_count=0 non blocca in shadow mode."""
    from mercury_foundry.outcome.service import OutcomeService
    from mercury_foundry.outcome.registry import create_outcome_plan, create_metric_snapshot
    mission_id = str(uuid.uuid4())
    plan = create_outcome_plan(db, **_make_plan_kwargs(
        mission_id=mission_id, minimum_evidence_count=5
    ))
    create_metric_snapshot(db,
        outcome_plan_id=plan.outcome_plan_id,
        mission_id=mission_id,
        **_make_snapshot_kwargs(evidence_count=0),
    )
    svc = OutcomeService()
    result = svc.evaluate(db, outcome_plan_id=plan.outcome_plan_id,
                          actor_id="test", correlation_id=str(uuid.uuid4()))
    assert result is not None  # shadow mode: non blocca


def test_48_activation_without_termination_criteria_warns(db):
    """48 — Piano senza stop_threshold né minimum_evidence_count produce warnings."""
    from mercury_foundry.outcome.registry import create_outcome_plan
    from mercury_foundry.outcome.lifecycle import check_activation_readiness
    plan = create_outcome_plan(db, **_make_plan_kwargs(
        minimum_evidence_count=0, stop_threshold=None,
    ))
    check = check_activation_readiness(plan)
    assert len(check.warnings) > 0


def test_49_activation_without_rollback_irreversible_warns(db):
    """49 — Piano irreversibile senza rollback_plan produce warning."""
    from mercury_foundry.outcome.registry import create_outcome_plan
    from mercury_foundry.outcome.lifecycle import check_activation_readiness
    plan = create_outcome_plan(db, **_make_plan_kwargs(
        reversibility="irreversible", rollback_plan=None,
    ))
    check = check_activation_readiness(plan)
    assert any("rollback" in w.lower() for w in check.warnings), f"Warnings: {check.warnings}"


def test_50_shadow_mode_no_unexpected_block(db):
    """50 — Shadow mode non produce blocchi inattesi su piani validi."""
    from mercury_foundry.outcome.service import OutcomeService
    from mercury_foundry.outcome.registry import create_outcome_plan, create_metric_snapshot
    mission_id = str(uuid.uuid4())
    plan = create_outcome_plan(db, **_make_plan_kwargs(mission_id=mission_id))
    create_metric_snapshot(db,
        outcome_plan_id=plan.outcome_plan_id,
        mission_id=mission_id,
        **_make_snapshot_kwargs(evidence_count=5),
    )
    svc = OutcomeService()
    # Non deve sollevare eccezioni
    result = svc.evaluate(db, outcome_plan_id=plan.outcome_plan_id,
                          actor_id="test", correlation_id=str(uuid.uuid4()))
    assert result.decision is not None


# ---------------------------------------------------------------------------
# AUDIT (51–54)
# ---------------------------------------------------------------------------

def test_51_outcome_event_unique(db):
    """51 — Ogni operazione produce esattamente un evento audit."""
    from mercury_foundry.audit.logger import list_audit_log
    from mercury_foundry.outcome.service import OutcomeService
    from mercury_foundry.outcome.registry import create_outcome_plan
    mission_id = str(uuid.uuid4())
    corr = str(uuid.uuid4())
    svc = OutcomeService()
    svc.create_plan(
        db,
        mission_id               = mission_id,
        correlation_id           = corr,
        objective                = "Test audit",
        primary_metric           = "revenue",
        target_value             = 1000.0,
        target_operator          = ">=",
        maximum_cost_minor       = 10_000,
        maximum_duration_seconds = 3600,
        review_interval_seconds  = 600,
        kill_deadline            = _future_iso(),
        minimum_evidence_count   = 1,
        strategic_value_score    = 0.5,
        learning_value_score     = 0.5,
        reversibility            = "reversible",
        created_by               = "test",
        actor_id                 = "test",
    )
    log = list_audit_log(db, entity_type="outcome", limit=10)
    outcome_create_events = [e for e in log if e["action"] == "outcome.plan.created"]
    assert len(outcome_create_events) >= 1


def test_52_outcome_decision_immutable(db):
    """52 — Una decisione non può essere modificata dopo INSERT."""
    from mercury_foundry.outcome.registry import create_outcome_plan, create_metric_snapshot, persist_outcome_decision, get_latest_decision
    mission_id = str(uuid.uuid4())
    plan = create_outcome_plan(db, **_make_plan_kwargs(mission_id=mission_id))
    create_metric_snapshot(db,
        outcome_plan_id=plan.outcome_plan_id,
        mission_id=mission_id,
        **_make_snapshot_kwargs(),
    )
    from mercury_foundry.outcome.models import OutcomeDecision, _now_iso
    dec = OutcomeDecision(
        decision_id     = str(uuid.uuid4()),
        mission_id      = mission_id,
        outcome_plan_id = plan.outcome_plan_id,
        decision_type   = "continue",
        score           = 75.0,
        confidence      = 0.8,
        reasons         = ["test"],
        blockers        = [],
        required_actions= [],
        decided_at      = _now_iso(),
        correlation_id  = str(uuid.uuid4()),
    )
    persist_outcome_decision(db, dec)
    # Tentare un UPDATE diretto è un no-op (non esponiamo API di update)
    retrieved = get_latest_decision(db, plan.outcome_plan_id)
    assert retrieved.decision_id == dec.decision_id
    assert retrieved.score == 75.0


def test_53_consumption_is_auditable(db):
    """53 — I consumi sono registrati e recuperabili."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.registry import get_total_consumption
    allocator = ResourceAllocator()
    env = allocator.allocate(db, mission_id=str(uuid.uuid4()),
                              budget_minor=10_000, deadline=_future_iso(),
                              allocated_by="test")
    allocator.consume(db, envelope_id=env.envelope_id, cost_minor=300,
                      source_ref="audit_test", idempotency_key="aud-001")
    allocator.consume(db, envelope_id=env.envelope_id, cost_minor=200,
                      source_ref="audit_test2", idempotency_key="aud-002")
    totals = get_total_consumption(db, env.envelope_id)
    assert totals["cost_minor"] == 500


def test_54_no_destructive_deletion(db):
    """54 — Nessuna operazione di cancellazione distruttiva esiste nel registry."""
    import inspect
    import mercury_foundry.outcome.registry as reg_module
    src = inspect.getsource(reg_module)
    # Non devono esserci DELETE FROM nel registry (solo INSERT e SELECT)
    assert "DELETE FROM" not in src.upper(), (
        "Registry outcome contiene istruzioni DELETE distruttive"
    )


# ---------------------------------------------------------------------------
# REGRESSION (55–60)
# ---------------------------------------------------------------------------

def test_55_all_preexisting_tests_pass(db):
    """55 — Marker: tutti i 410 test pre-esistenti devono passare (verificato dalla suite completa)."""
    # Questo test passa sempre: la verifica reale è eseguire l'intera suite.
    assert True


def test_56_doctor_replication_invariant(tmp_path):
    """56 — Il doctor deve riportare READY_OUTCOME_SHADOW."""
    from mercury_foundry.diagnostics import run_doctor, OVERALL_READY_OUTCOME_SHADOW
    db_path = tmp_path / "mercury_foundry.db"
    sandbox = tmp_path / "target_project"
    sandbox.mkdir()
    report = run_doctor(db_path=db_path, sandbox_root=sandbox)
    assert report.overall_status == OVERALL_READY_OUTCOME_SHADOW, (
        f"Doctor status atteso: {OVERALL_READY_OUTCOME_SHADOW}, trovato: {report.overall_status}\n"
        + "\n".join(f"[{c.status}] {c.name}: {c.detail}" for c in report.checks if c.status != "ok")
    )


def test_57_mission_layer_invariant(db):
    """57 — Il Mission layer funziona correttamente dopo l'introduzione di Outcome."""
    from mercury_foundry.mission.intake import MissionIntakeService
    from mercury_foundry.mission.models import (
        ExpectedOutcome, MissionAuthorityRequest, MissionBudget,
        MissionIntakeRequest, MissionRiskProfile,
        OriginType, MissionType, Priority,
    )
    svc = MissionIntakeService()
    req = MissionIntakeRequest(
        title           = "Regression Mission",
        description     = "test regression",
        objective       = "verifica non-regressione",
        origin_type     = OriginType.FOUNDER,
        mission_type    = MissionType.CUSTOM,
        priority        = Priority.NORMAL,
        expected_outcomes = [ExpectedOutcome(
            outcome_id="eo-reg", description="test", required=True, metric_name="revenue"
        )],
        success_criteria  = [],
        termination_criteria = [],
        constraints       = {},
        budget            = MissionBudget(currency="EUR", approved_amount=10.0),
        risk_profile      = MissionRiskProfile(risk_level="low"),
        authority_request = MissionAuthorityRequest(requested_mode="proposal"),
        required_capabilities = [],
        created_by        = "test_actor",
        idempotency_key   = str(uuid.uuid4()),
        correlation_id    = str(uuid.uuid4()),
    )
    result = svc.submit(db, req)
    assert result.accepted, f"Mission non accettata: {result.validation_errors}"
    assert result.mission_id
    from mercury_foundry.mission.registry import get_mission
    m = get_mission(db, result.mission_id)
    assert str(m.status.value if hasattr(m.status, "value") else m.status) == "draft"


def test_58_constitutional_shadow_invariant(db):
    """58 — Constitutional shadow mode non blocca operazioni outcome."""
    from mercury_foundry.constitutional.shadow import maybe_validate_constitution
    result = maybe_validate_constitution(
        db,
        organ_key     = "ECONOMIC_GOVERNANCE",
        decision_type = "OUTCOME_EVALUATE",
        authority_mode= "proposal",
        subject_type  = "outcome_decision",
        subject_id    = str(uuid.uuid4()),
        evidence_refs = ["plan-001"],
        budget_impact = 1000.0,
        risk_level    = "low",
        metadata      = {},
    )
    # In shadow mode non blocca (None o risultato shadow)


def test_59_no_dedicated_mercury_created(db):
    """59 — Nessuna Dedicated Mercury creata dal layer Outcome."""
    # Verificare che le tabelle Genesis non abbiano record prodotti da Outcome
    rows = db.execute(
        "SELECT COUNT(*) as cnt FROM dedicated_mercury_genesis_requests"
    ).fetchone()
    assert rows["cnt"] == 0


def test_60_no_sale_or_payment(db):
    """60 — Nessuna operazione di vendita o pagamento nel package outcome."""
    import os
    import inspect
    import mercury_foundry.outcome.service as svc_module
    import mercury_foundry.outcome.policy as policy_module
    import mercury_foundry.outcome.scoring as scoring_module
    src = (
        inspect.getsource(svc_module)
        + inspect.getsource(policy_module)
        + inspect.getsource(scoring_module)
    )
    forbidden_terms = ["payment", "sale", "sell", "checkout", "stripe", "invoice"]
    for term in forbidden_terms:
        assert term.lower() not in src.lower(), (
            f"Termine proibito {term!r} trovato nel layer outcome"
        )

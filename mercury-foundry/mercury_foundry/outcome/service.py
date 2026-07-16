"""OutcomeService — orchestrazione del flusso Outcome Governance V0.

Flusso:
  Mission
  → Outcome Plan         (create_plan)
  → Resource Envelope    (allocate_resources, via ResourceAllocator)
  → Metric Snapshot      (record_snapshot)
  → Score                (OutcomeScorer)
  → Policy Evaluation    (OutcomePolicyEvaluator)
  → Authority            (authorize_organ_decision / ECONOMIC_GOVERNANCE)
  → Constitution         (maybe_validate_constitution / shadow)
  → Outcome Decision     (persist + emit event)
  → Mission Transition   (apply_decision_to_mission)

Mapping DecisionType → Mission action:
  CONTINUE       → nessun cambio se mission è active
  PAUSE          → active → paused
  STOP           → active/paused/blocked → terminated
  SCALE          → registra proposta, NON aumenta budget automaticamente
  REQUIRE_REVIEW → registra escalation, NON modifica status mission

Non implementa vendita, pagamento, delivery, scale automatico.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field

from mercury_foundry.outcome.allocator import ResourceAllocator
from mercury_foundry.outcome.events import emit_outcome_event
from mercury_foundry.outcome.lifecycle import (
    apply_outcome_transition,
    check_activation_readiness,
)
from mercury_foundry.outcome.models import (
    DecisionType,
    EconomicOutcomePlan,
    OutcomeActivationCheck,
    OutcomeDecision,
    OutcomeMetricSnapshot,
    OutcomePlanNotFoundError,
    OutcomePlanValidationError,
    OutcomeStatus,
    ResourceEnvelope,
    _new_id,
    _now_iso,
)
from mercury_foundry.outcome.policy import OutcomePolicyEvaluator, PolicyConfig, PolicyEvaluationContext
from mercury_foundry.outcome.registry import (
    create_metric_snapshot,
    create_outcome_plan,
    get_latest_decision,
    get_latest_snapshot,
    get_outcome_plan,
    get_outcome_plan_for_mission,
    persist_outcome_decision,
)
from mercury_foundry.outcome.scoring import OutcomeScorer


# ---------------------------------------------------------------------------
# OutcomeProposalResult
# ---------------------------------------------------------------------------

@dataclass
class OutcomeProposalResult:
    """Risultato di OutcomeService.create_plan()."""
    plan:       EconomicOutcomePlan
    check:      OutcomeActivationCheck
    event_id:   int | None = None


# ---------------------------------------------------------------------------
# OutcomeEvaluationResult
# ---------------------------------------------------------------------------

@dataclass
class OutcomeEvaluationResult:
    """Risultato di OutcomeService.evaluate()."""
    decision:                     OutcomeDecision
    authority_allowed:            bool
    authority_mode:               str
    constitutional_status:        str
    mission_transition_applied:   bool
    mission_new_status:           str | None = None
    correlation_id:               str = field(default_factory=lambda: str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# OutcomeService
# ---------------------------------------------------------------------------

class OutcomeService:
    """Entry point principale per il layer di Outcome Governance.

    Iniettabile con scorer, evaluator e allocator per i test.
    """

    def __init__(
        self,
        scorer:    OutcomeScorer    | None = None,
        evaluator: OutcomePolicyEvaluator | None = None,
        allocator: ResourceAllocator      | None = None,
        policy_config: PolicyConfig       | None = None,
    ) -> None:
        self._scorer    = scorer    or OutcomeScorer()
        self._evaluator = evaluator or OutcomePolicyEvaluator(config=policy_config)
        self._allocator = allocator or ResourceAllocator()

    # ----------------------------------------------------------------
    # create_plan
    # ----------------------------------------------------------------

    def create_plan(
        self,
        conn: sqlite3.Connection,
        *,
        mission_id: str,
        correlation_id: str,
        objective: str,
        primary_metric: str,
        target_value: float,
        target_operator: str,
        maximum_cost_minor: int,
        maximum_duration_seconds: int,
        review_interval_seconds: int,
        kill_deadline: str,
        minimum_evidence_count: int,
        strategic_value_score: float,
        learning_value_score: float,
        reversibility: str,
        created_by: str,
        actor_id: str,
        # Opzionali
        currency: str | None = None,
        expected_revenue_minor: int | None = None,
        expected_profit_minor: int | None = None,
        scale_threshold: float | None = None,
        stop_threshold: float | None = None,
        rollback_plan: str | None = None,
        priority_class: str = "normal",
        metadata: dict | None = None,
    ) -> OutcomeProposalResult:
        """Crea un EconomicOutcomePlan, lo valida e produce l'evento."""

        # 1. Autenticazione autonomy (OUTCOME_PLAN_CREATE → proposal)
        from mercury_foundry.autonomy.authorization import authorize_organ_decision
        auth = authorize_organ_decision(
            conn,
            organ_key    = "ECONOMIC_GOVERNANCE",
            decision_type= "OUTCOME_PLAN_CREATE",
            subject_type = "outcome_plan",
            subject_id   = mission_id,
            evidence     = {"mission_id": mission_id, "actor_id": actor_id},
        )

        # 2. Crea il piano
        plan = create_outcome_plan(
            conn,
            mission_id               = mission_id,
            correlation_id           = correlation_id,
            objective                = objective,
            primary_metric           = primary_metric,
            target_value             = target_value,
            target_operator          = target_operator,
            maximum_cost_minor       = maximum_cost_minor,
            maximum_duration_seconds = maximum_duration_seconds,
            review_interval_seconds  = review_interval_seconds,
            kill_deadline            = kill_deadline,
            minimum_evidence_count   = minimum_evidence_count,
            strategic_value_score    = strategic_value_score,
            learning_value_score     = learning_value_score,
            reversibility            = reversibility,
            created_by               = created_by,
            currency                 = currency,
            expected_revenue_minor   = expected_revenue_minor,
            expected_profit_minor    = expected_profit_minor,
            scale_threshold          = scale_threshold,
            stop_threshold           = stop_threshold,
            rollback_plan            = rollback_plan,
            priority_class           = priority_class,
            metadata                 = metadata,
        )

        # 3. Validazione strutturale
        errors = plan.validate()
        if errors:
            emit_outcome_event(
                conn,
                action          = "outcome.plan.invalid",
                entity_id       = 0,
                mission_id      = mission_id,
                actor_id        = actor_id,
                correlation_id  = correlation_id,
                outcome_plan_id = plan.outcome_plan_id,
                metadata        = {"errors": errors},
            )
            raise OutcomePlanValidationError(
                f"OutcomePlan non valido per mission {mission_id}: {errors}"
            )

        # 4. Activation readiness check
        check = check_activation_readiness(plan)

        # 5. Emetti evento
        event_id = emit_outcome_event(
            conn,
            action          = "outcome.plan.created",
            entity_id       = 0,
            mission_id      = mission_id,
            actor_id        = actor_id,
            correlation_id  = correlation_id,
            outcome_plan_id = plan.outcome_plan_id,
            authority_decision_id = str(auth.decision_record_id) if auth.decision_record_id else None,
        )

        return OutcomeProposalResult(
            plan     = plan,
            check    = check,
            event_id = event_id,
        )

    # ----------------------------------------------------------------
    # record_snapshot
    # ----------------------------------------------------------------

    def record_snapshot(
        self,
        conn: sqlite3.Connection,
        *,
        outcome_plan_id: str,
        mission_id: str,
        actor_id: str,
        correlation_id: str,
        revenue_minor: int,
        cost_minor: int,
        profit_minor: int,
        elapsed_seconds: int,
        evidence_count: int,
        customer_count: int,
        knowledge_gain_score: float,
        risk_score: float,
        conversion_rate: float | None = None,
        delivery_success_rate: float | None = None,
        metadata: dict | None = None,
    ) -> OutcomeMetricSnapshot:
        """Registra uno snapshot di metriche e produce l'evento."""
        snapshot = create_metric_snapshot(
            conn,
            outcome_plan_id       = outcome_plan_id,
            mission_id            = mission_id,
            revenue_minor         = revenue_minor,
            cost_minor            = cost_minor,
            profit_minor          = profit_minor,
            elapsed_seconds       = elapsed_seconds,
            evidence_count        = evidence_count,
            customer_count        = customer_count,
            knowledge_gain_score  = knowledge_gain_score,
            risk_score            = risk_score,
            conversion_rate       = conversion_rate,
            delivery_success_rate = delivery_success_rate,
            metadata              = metadata,
        )
        emit_outcome_event(
            conn,
            action          = "outcome.metric.recorded",
            entity_id       = 0,
            mission_id      = mission_id,
            actor_id        = actor_id,
            correlation_id  = correlation_id,
            outcome_plan_id = outcome_plan_id,
            snapshot_id     = snapshot.snapshot_id,
        )
        return snapshot

    # ----------------------------------------------------------------
    # evaluate
    # ----------------------------------------------------------------

    def evaluate(
        self,
        conn: sqlite3.Connection,
        *,
        outcome_plan_id: str,
        actor_id: str,
        correlation_id: str,
        context: PolicyEvaluationContext | None = None,
        now_iso: str | None = None,
    ) -> OutcomeEvaluationResult:
        """Valuta l'outcome plan, applica authority e constitution, persiste la decisione."""

        # 1. Carica piano e snapshot
        plan = get_outcome_plan(conn, outcome_plan_id)
        snapshot = get_latest_snapshot(conn, outcome_plan_id)
        if snapshot is None:
            # Snapshot fittizio con tutti zeri per valutazione (evidenza zero)
            from mercury_foundry.outcome.models import OutcomeMetricSnapshot
            snapshot = OutcomeMetricSnapshot(
                snapshot_id          = "null",
                outcome_plan_id      = outcome_plan_id,
                mission_id           = plan.mission_id,
                measured_at          = _now_iso(),
                revenue_minor        = 0,
                cost_minor           = 0,
                profit_minor         = 0,
                elapsed_seconds      = 0,
                evidence_count       = 0,
                customer_count       = 0,
                knowledge_gain_score = 0.0,
                risk_score           = 0.0,
            )

        # Recupera envelope (può essere None in V0 se non allocato)
        from mercury_foundry.outcome.registry import get_resource_envelope_for_mission
        envelope = get_resource_envelope_for_mission(conn, plan.mission_id)
        if envelope is None:
            # Envelope fittizio (illimitato) per la policy evaluation
            from mercury_foundry.outcome.models import ResourceEnvelope
            envelope = ResourceEnvelope(
                envelope_id                  = "null",
                mission_id                   = plan.mission_id,
                budget_minor                 = plan.maximum_cost_minor or 0,
                compute_units                = 0,
                llm_token_limit              = 0,
                external_service_limit_minor = 0,
                human_minutes_limit          = 0,
                deadline                     = plan.kill_deadline,
                allocated_at                 = _now_iso(),
                allocated_by                 = "system",
                version                      = 1,
            )

        # 2. Valutazione policy
        ctx = context or PolicyEvaluationContext(correlation_id=correlation_id)
        decision = self._evaluator.evaluate(plan, snapshot, envelope, ctx, now_iso=now_iso)
        decision.correlation_id = correlation_id

        # 3. Authority (OUTCOME_EVALUATE → proposal)
        from mercury_foundry.autonomy.authorization import authorize_organ_decision
        auth = authorize_organ_decision(
            conn,
            organ_key     = "ECONOMIC_GOVERNANCE",
            decision_type = "OUTCOME_EVALUATE",
            subject_type  = "outcome_decision",
            subject_id    = decision.decision_id,
            evidence      = {
                "decision_type": decision.decision_type,
                "score":         decision.score,
                "mission_id":    plan.mission_id,
            },
        )
        if auth.decision_record_id:
            decision.authority_decision_id = str(auth.decision_record_id)

        # 4. Constitutional (shadow)
        from mercury_foundry.constitutional.shadow import maybe_validate_constitution
        const_result = maybe_validate_constitution(
            conn,
            organ_key     = "ECONOMIC_GOVERNANCE",
            decision_type = "OUTCOME_EVALUATE",
            authority_mode= auth.authority_mode if auth else "proposal",
            subject_type  = "outcome_decision",
            subject_id    = decision.decision_id,
            evidence_refs = [plan.outcome_plan_id],
            budget_impact = float(snapshot.cost_minor) if snapshot.cost_minor else None,
            risk_level    = (
                "high" if snapshot.risk_score >= 0.7
                else "medium" if snapshot.risk_score >= 0.4
                else "low"
            ),
            correlation_id= correlation_id,
            metadata      = {"decision_type": decision.decision_type, "actor_id": actor_id},
        )
        if const_result is not None and const_result.validation_id:
            decision.constitutional_validation_id = const_result.validation_id

        # 5. Persisti la decisione (immutabile)
        persist_outcome_decision(conn, decision)

        # 6. Emetti evento
        event_action = {
            DecisionType.CONTINUE.value:       "outcome.decision.continue",
            DecisionType.PAUSE.value:          "outcome.decision.pause",
            DecisionType.STOP.value:           "outcome.decision.stop",
            DecisionType.SCALE.value:          "outcome.decision.scale_proposed",
            DecisionType.REQUIRE_REVIEW.value: "outcome.decision.review_required",
        }.get(decision.decision_type, "outcome.evaluation.completed")

        emit_outcome_event(
            conn,
            action          = event_action,
            entity_id       = 0,
            mission_id      = plan.mission_id,
            actor_id        = actor_id,
            correlation_id  = correlation_id,
            outcome_plan_id = outcome_plan_id,
            decision_type   = decision.decision_type,
            decision_id     = decision.decision_id,
            authority_decision_id = decision.authority_decision_id,
            constitutional_validation_id = decision.constitutional_validation_id,
        )

        # 7. Applica la decisione alla Mission
        transition_applied, new_status = self._apply_decision_to_mission(
            conn, decision=decision, plan=plan, actor_id=actor_id
        )

        const_status = (
            const_result.status if const_result is not None else "shadow_not_run"
        )

        return OutcomeEvaluationResult(
            decision                   = decision,
            authority_allowed          = auth.allowed,
            authority_mode             = auth.authority_mode,
            constitutional_status      = str(const_status),
            mission_transition_applied = transition_applied,
            mission_new_status         = new_status,
            correlation_id             = correlation_id,
        )

    # ----------------------------------------------------------------
    # _apply_decision_to_mission
    # ----------------------------------------------------------------

    def _apply_decision_to_mission(
        self,
        conn: sqlite3.Connection,
        *,
        decision: OutcomeDecision,
        plan: EconomicOutcomePlan,
        actor_id: str,
    ) -> tuple[bool, str | None]:
        """Applica la decisione di outcome al lifecycle della Mission.

        Mapping:
          CONTINUE       → nessun cambio (se mission active)
          PAUSE          → active → paused
          STOP           → active/paused/blocked → terminated
          SCALE          → registra proposta, NON aumenta budget automaticamente
          REQUIRE_REVIEW → registra escalation, NON modifica status mission
        """
        from mercury_foundry.mission.registry import get_mission
        try:
            mission = get_mission(conn, plan.mission_id)
        except Exception:
            return False, None

        dt = decision.decision_type
        current_status = mission.status.value if hasattr(mission.status, "value") else str(mission.status)

        if dt == DecisionType.CONTINUE.value:
            # Nessuna transizione — la mission rimane active
            return False, current_status

        elif dt == DecisionType.PAUSE.value:
            if current_status == "active":
                try:
                    from mercury_foundry.mission.lifecycle import apply_transition
                    apply_transition(
                        conn,
                        mission_id         = plan.mission_id,
                        current_status     = current_status,
                        current_version    = mission.version,
                        to_status          = "paused",
                        requested_by       = actor_id,
                        reason             = f"outcome_decision:{decision.decision_id} → pause",
                        correlation_id     = decision.correlation_id,
                        authority_decision_id = decision.authority_decision_id,
                    )
                    return True, "paused"
                except Exception:
                    return False, current_status
            return False, current_status

        elif dt == DecisionType.STOP.value:
            if current_status in ("active", "paused", "blocked"):
                try:
                    from mercury_foundry.mission.lifecycle import apply_transition
                    apply_transition(
                        conn,
                        mission_id         = plan.mission_id,
                        current_status     = current_status,
                        current_version    = mission.version,
                        to_status          = "terminated",
                        requested_by       = actor_id,
                        reason             = f"outcome_decision:{decision.decision_id} → stop",
                        correlation_id     = decision.correlation_id,
                        authority_decision_id = decision.authority_decision_id,
                    )
                    return True, "terminated"
                except Exception:
                    return False, current_status
            return False, current_status

        elif dt == DecisionType.SCALE.value:
            # SCALE: registra la proposta, NON modifica risorse automaticamente
            # NON aumenta budget. NON fa nulla di reale.
            return False, current_status

        elif dt == DecisionType.REQUIRE_REVIEW.value:
            # REQUIRE_REVIEW: registra escalation, NON modifica status
            return False, current_status

        return False, current_status

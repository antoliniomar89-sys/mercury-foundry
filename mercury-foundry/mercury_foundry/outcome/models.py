"""Modelli tipizzati per il layer di Outcome Governance — MF-OUTCOME-001.

Invarianti:
- Tutti gli importi monetari sono in integer minor units (es. centesimi EUR).
- Nessuna cancellazione distruttiva: stati terminali sostituiscono delete.
- Tutti i timestamp in UTC ISO 8601.
- Optimistic locking via campo `version`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DecisionType(str, Enum):
    CONTINUE       = "continue"
    PAUSE          = "pause"
    STOP           = "stop"
    SCALE          = "scale"
    REQUIRE_REVIEW = "require_review"


class OutcomeStatus(str, Enum):
    PLANNED      = "planned"
    ACTIVE       = "active"
    UNDER_REVIEW = "under_review"
    SUCCEEDED    = "succeeded"
    FAILED       = "failed"
    STOPPED      = "stopped"
    SCALED       = "scaled"
    ARCHIVED     = "archived"


class PriorityClass(str, Enum):
    CRITICAL = "critical"
    HIGH     = "high"
    NORMAL   = "normal"
    LOW      = "low"
    BACKLOG  = "backlog"


class TargetOperator(str, Enum):
    GTE = ">="
    LTE = "<="
    EQ  = "=="
    GT  = ">"
    LT  = "<"


class Reversibility(str, Enum):
    REVERSIBLE           = "reversible"
    PARTIALLY_REVERSIBLE = "partially_reversible"
    IRREVERSIBLE         = "irreversible"


# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------

class OutcomePlanNotFoundError(KeyError):
    """EconomicOutcomePlan non trovato per il mission_id / outcome_plan_id dato."""


class OutcomePlanValidationError(ValueError):
    """EconomicOutcomePlan non valido."""


class ResourceExhaustedError(ValueError):
    """Consumo supera l'envelope allocato."""


class ResourceAllocationError(ValueError):
    """Allocazione non valida (es. budget negativo)."""


class OutcomeVersionConflict(RuntimeError):
    """Versione non corrisponde (optimistic locking)."""


class ConsumptionIdempotencyReplay(Exception):
    """Consumo già registrato con la stessa idempotency_key."""
    def __init__(self, existing_consumption_id: str):
        super().__init__(f"Consumo già registrato: {existing_consumption_id}")
        self.existing_consumption_id = existing_consumption_id


class OutcomeDecisionImmutableError(RuntimeError):
    """Tentativo di modificare una decisione immutabile."""


# ---------------------------------------------------------------------------
# EconomicOutcomePlan
# ---------------------------------------------------------------------------

@dataclass
class EconomicOutcomePlan:
    """Piano di outcome economico associato a una Mission.

    Invarianti:
    - maximum_cost_minor >= 0
    - maximum_duration_seconds > 0
    - minimum_evidence_count >= 0
    - target_value è il valore soglia per primary_metric
    - kill_deadline è un ISO datetime UTC
    - strategic_value_score e learning_value_score ∈ [0.0, 1.0]
    """
    outcome_plan_id:          str
    mission_id:               str
    correlation_id:           str
    objective:                str
    primary_metric:           str
    target_value:             float
    target_operator:          str           # TargetOperator.value
    maximum_cost_minor:       int           # integer minor units (es. centesimi)
    maximum_duration_seconds: int
    review_interval_seconds:  int
    kill_deadline:            str           # ISO UTC datetime
    minimum_evidence_count:   int
    strategic_value_score:    float         # 0.0–1.0
    learning_value_score:     float         # 0.0–1.0
    reversibility:            str           # Reversibility.value
    created_by:               str
    created_at:               str
    updated_at:               str
    version:                  int
    # Opzionali
    currency:                 str | None = None
    expected_revenue_minor:   int | None = None
    expected_profit_minor:    int | None = None
    scale_threshold:          float | None = None
    stop_threshold:           float | None = None
    rollback_plan:            str | None = None
    metadata:                 dict = field(default_factory=dict)
    status:                   str = OutcomeStatus.PLANNED.value
    priority_class:           str = PriorityClass.NORMAL.value

    def validate(self) -> list[str]:
        """Ritorna lista di errori di validazione. Lista vuota = valido."""
        errors: list[str] = []
        if not self.objective.strip():
            errors.append("objective non può essere vuoto")
        if not self.primary_metric.strip():
            errors.append("primary_metric non può essere vuoto")
        if self.maximum_cost_minor < 0:
            errors.append(f"maximum_cost_minor deve essere >= 0, ricevuto: {self.maximum_cost_minor}")
        if self.maximum_duration_seconds <= 0:
            errors.append(f"maximum_duration_seconds deve essere > 0, ricevuto: {self.maximum_duration_seconds}")
        if self.minimum_evidence_count < 0:
            errors.append(f"minimum_evidence_count deve essere >= 0, ricevuto: {self.minimum_evidence_count}")
        if not (0.0 <= self.strategic_value_score <= 1.0):
            errors.append(f"strategic_value_score deve essere in [0,1], ricevuto: {self.strategic_value_score}")
        if not (0.0 <= self.learning_value_score <= 1.0):
            errors.append(f"learning_value_score deve essere in [0,1], ricevuto: {self.learning_value_score}")
        if self.reversibility not in {r.value for r in Reversibility}:
            errors.append(f"reversibility non valida: {self.reversibility!r}")
        if self.target_operator not in {op.value for op in TargetOperator}:
            errors.append(f"target_operator non valido: {self.target_operator!r}")
        # kill_deadline parse check
        try:
            kd = datetime.fromisoformat(self.kill_deadline)
            created = datetime.fromisoformat(self.created_at)
            if kd <= created:
                errors.append("kill_deadline deve essere successiva a created_at")
        except (ValueError, TypeError):
            errors.append(f"kill_deadline non parsabile come ISO datetime: {self.kill_deadline!r}")
        # Opzionali — se presenti devono essere >= 0
        if self.expected_revenue_minor is not None and self.expected_revenue_minor < 0:
            errors.append(f"expected_revenue_minor deve essere >= 0, ricevuto: {self.expected_revenue_minor}")
        if self.expected_profit_minor is not None and self.expected_profit_minor < 0:
            errors.append(f"expected_profit_minor deve essere >= 0, ricevuto: {self.expected_profit_minor}")
        return errors

    def to_dict(self) -> dict:
        return {
            "outcome_plan_id":          self.outcome_plan_id,
            "mission_id":               self.mission_id,
            "correlation_id":           self.correlation_id,
            "objective":                self.objective,
            "primary_metric":           self.primary_metric,
            "target_value":             self.target_value,
            "target_operator":          self.target_operator,
            "maximum_cost_minor":       self.maximum_cost_minor,
            "maximum_duration_seconds": self.maximum_duration_seconds,
            "review_interval_seconds":  self.review_interval_seconds,
            "kill_deadline":            self.kill_deadline,
            "minimum_evidence_count":   self.minimum_evidence_count,
            "strategic_value_score":    self.strategic_value_score,
            "learning_value_score":     self.learning_value_score,
            "reversibility":            self.reversibility,
            "created_by":               self.created_by,
            "created_at":               self.created_at,
            "updated_at":               self.updated_at,
            "version":                  self.version,
            "currency":                 self.currency,
            "expected_revenue_minor":   self.expected_revenue_minor,
            "expected_profit_minor":    self.expected_profit_minor,
            "scale_threshold":          self.scale_threshold,
            "stop_threshold":           self.stop_threshold,
            "rollback_plan":            self.rollback_plan,
            "metadata":                 self.metadata,
            "status":                   self.status,
            "priority_class":           self.priority_class,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EconomicOutcomePlan":
        return cls(
            outcome_plan_id          = d["outcome_plan_id"],
            mission_id               = d["mission_id"],
            correlation_id           = d["correlation_id"],
            objective                = d["objective"],
            primary_metric           = d["primary_metric"],
            target_value             = float(d["target_value"]),
            target_operator          = d["target_operator"],
            maximum_cost_minor       = int(d["maximum_cost_minor"]),
            maximum_duration_seconds = int(d["maximum_duration_seconds"]),
            review_interval_seconds  = int(d["review_interval_seconds"]),
            kill_deadline            = d["kill_deadline"],
            minimum_evidence_count   = int(d["minimum_evidence_count"]),
            strategic_value_score    = float(d["strategic_value_score"]),
            learning_value_score     = float(d["learning_value_score"]),
            reversibility            = d["reversibility"],
            created_by               = d["created_by"],
            created_at               = d["created_at"],
            updated_at               = d["updated_at"],
            version                  = int(d["version"]),
            currency                 = d.get("currency"),
            expected_revenue_minor   = d.get("expected_revenue_minor"),
            expected_profit_minor    = d.get("expected_profit_minor"),
            scale_threshold          = d.get("scale_threshold"),
            stop_threshold           = d.get("stop_threshold"),
            rollback_plan            = d.get("rollback_plan"),
            metadata                 = d.get("metadata", {}),
            status                   = d.get("status", OutcomeStatus.PLANNED.value),
            priority_class           = d.get("priority_class", PriorityClass.NORMAL.value),
        )


# ---------------------------------------------------------------------------
# OutcomeMetricSnapshot
# ---------------------------------------------------------------------------

@dataclass
class OutcomeMetricSnapshot:
    """Snapshot di metriche economiche per una Mission in un dato istante."""
    snapshot_id:           str
    outcome_plan_id:       str
    mission_id:            str
    measured_at:           str
    revenue_minor:         int
    cost_minor:            int
    profit_minor:          int
    elapsed_seconds:       int
    evidence_count:        int
    customer_count:        int
    knowledge_gain_score:  float  # 0.0–1.0
    risk_score:            float  # 0.0–1.0
    # Opzionali
    conversion_rate:         float | None = None   # 0.0–1.0
    delivery_success_rate:   float | None = None   # 0.0–1.0
    metadata:                dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "snapshot_id":           self.snapshot_id,
            "outcome_plan_id":       self.outcome_plan_id,
            "mission_id":            self.mission_id,
            "measured_at":           self.measured_at,
            "revenue_minor":         self.revenue_minor,
            "cost_minor":            self.cost_minor,
            "profit_minor":          self.profit_minor,
            "elapsed_seconds":       self.elapsed_seconds,
            "evidence_count":        self.evidence_count,
            "customer_count":        self.customer_count,
            "knowledge_gain_score":  self.knowledge_gain_score,
            "risk_score":            self.risk_score,
            "conversion_rate":       self.conversion_rate,
            "delivery_success_rate": self.delivery_success_rate,
            "metadata":              self.metadata,
        }


# ---------------------------------------------------------------------------
# ResourceEnvelope
# ---------------------------------------------------------------------------

@dataclass
class ResourceEnvelope:
    """Envelope di risorse allocate a una Mission."""
    envelope_id:                  str
    mission_id:                   str
    budget_minor:                 int
    compute_units:                int
    llm_token_limit:              int
    external_service_limit_minor: int
    human_minutes_limit:          int
    deadline:                     str
    allocated_at:                 str
    allocated_by:                 str
    version:                      int
    metadata:                     dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "envelope_id":                  self.envelope_id,
            "mission_id":                   self.mission_id,
            "budget_minor":                 self.budget_minor,
            "compute_units":                self.compute_units,
            "llm_token_limit":              self.llm_token_limit,
            "external_service_limit_minor": self.external_service_limit_minor,
            "human_minutes_limit":          self.human_minutes_limit,
            "deadline":                     self.deadline,
            "allocated_at":                 self.allocated_at,
            "allocated_by":                 self.allocated_by,
            "version":                      self.version,
            "metadata":                     self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResourceEnvelope":
        return cls(
            envelope_id                  = d["envelope_id"],
            mission_id                   = d["mission_id"],
            budget_minor                 = int(d["budget_minor"]),
            compute_units                = int(d["compute_units"]),
            llm_token_limit              = int(d["llm_token_limit"]),
            external_service_limit_minor = int(d["external_service_limit_minor"]),
            human_minutes_limit          = int(d["human_minutes_limit"]),
            deadline                     = d["deadline"],
            allocated_at                 = d["allocated_at"],
            allocated_by                 = d["allocated_by"],
            version                      = int(d["version"]),
            metadata                     = d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# ResourceConsumption
# ---------------------------------------------------------------------------

@dataclass
class ResourceConsumption:
    """Singola registrazione di consumo di risorse — immutabile dopo insert."""
    consumption_id:              str
    envelope_id:                 str
    mission_id:                  str
    cost_minor:                  int
    compute_units:               int
    llm_tokens:                  int
    external_service_cost_minor: int
    human_minutes:               int
    recorded_at:                 str
    source_ref:                  str
    idempotency_key:             str
    metadata:                    dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "consumption_id":              self.consumption_id,
            "envelope_id":                 self.envelope_id,
            "mission_id":                  self.mission_id,
            "cost_minor":                  self.cost_minor,
            "compute_units":               self.compute_units,
            "llm_tokens":                  self.llm_tokens,
            "external_service_cost_minor": self.external_service_cost_minor,
            "human_minutes":               self.human_minutes,
            "recorded_at":                 self.recorded_at,
            "source_ref":                  self.source_ref,
            "idempotency_key":             self.idempotency_key,
            "metadata":                    self.metadata,
        }


# ---------------------------------------------------------------------------
# OutcomeDecision
# ---------------------------------------------------------------------------

@dataclass
class OutcomeDecision:
    """Decisione economica prodotta dal PolicyEvaluator — immutabile dopo insert."""
    decision_id:                    str
    mission_id:                     str
    outcome_plan_id:                str
    decision_type:                  str   # DecisionType.value
    score:                          float
    confidence:                     float
    reasons:                        list[str]
    blockers:                       list[str]
    required_actions:               list[str]
    decided_at:                     str
    correlation_id:                 str
    authority_decision_id:          str | None = None
    constitutional_validation_id:   str | None = None
    metadata:                       dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "decision_id":                  self.decision_id,
            "mission_id":                   self.mission_id,
            "outcome_plan_id":              self.outcome_plan_id,
            "decision_type":                self.decision_type,
            "score":                        self.score,
            "confidence":                   self.confidence,
            "reasons":                      self.reasons,
            "blockers":                     self.blockers,
            "required_actions":             self.required_actions,
            "decided_at":                   self.decided_at,
            "correlation_id":               self.correlation_id,
            "authority_decision_id":        self.authority_decision_id,
            "constitutional_validation_id": self.constitutional_validation_id,
            "metadata":                     self.metadata,
        }


# ---------------------------------------------------------------------------
# OutcomeTransitionRecord
# ---------------------------------------------------------------------------

@dataclass
class OutcomeTransitionRecord:
    """Record immutabile di una transizione di stato di un OutcomePlan."""
    transition_id:    str
    outcome_plan_id:  str
    mission_id:       str
    from_status:      str
    to_status:        str
    requested_by:     str
    requested_at:     str
    reason:           str
    correlation_id:   str
    decision_id:      str | None = None
    metadata:         dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Activation readiness
# ---------------------------------------------------------------------------

@dataclass
class OutcomeActivationCheck:
    """Risultato del controllo di readiness per l'attivazione economica."""
    ready:    bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

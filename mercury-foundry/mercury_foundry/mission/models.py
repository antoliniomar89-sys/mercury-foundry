"""Modelli tipizzati del Mission Layer — MF-MISSION-001.

Tutti i modelli usano enum + dataclass per validazione forte alla costruzione.
Nessun LLM, nessuna euristica: solo strutture dati e costanti.

Budget: REAL per consistenza con `max_budget` in decision_mandates.
Debito tecnico: migrare a INTEGER minor units se il dominio economico lo richiede.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerazioni
# ---------------------------------------------------------------------------

class OriginType(str, Enum):
    FOUNDER                = "founder"
    AUTONOMOUS_DISCOVERY   = "autonomous_discovery"
    CUSTOMER               = "customer"
    BUSINESS_CELL          = "business_cell"
    INTERNAL_ORGAN         = "internal_organ"
    LABORATORY             = "laboratory"
    PORTFOLIO_ORCHESTRATOR = "portfolio_orchestrator"
    EXTERNAL_SYSTEM        = "external_system"


class MissionType(str, Enum):
    RESEARCH              = "research"
    PRODUCT_DISCOVERY     = "product_discovery"
    PRODUCT_DEVELOPMENT   = "product_development"
    VALIDATION            = "validation"
    DELIVERY              = "delivery"
    MARKET_ENTRY          = "market_entry"
    OPTIMIZATION          = "optimization"
    CAPABILITY_DEVELOPMENT = "capability_development"
    BUSINESS_GENESIS      = "business_genesis"
    CUSTOM                = "custom"


class MissionStatus(str, Enum):
    DRAFT                    = "draft"
    SUBMITTED                = "submitted"
    UNDER_REVIEW             = "under_review"
    ACCEPTED                 = "accepted"
    REJECTED                 = "rejected"
    READY                    = "ready"
    ACTIVE                   = "active"
    PAUSED                   = "paused"
    BLOCKED                  = "blocked"
    COMPLETED                = "completed"
    FAILED                   = "failed"
    TERMINATED               = "terminated"
    ARCHIVED                 = "archived"
    PROMOTED_TO_BUSINESS_CELL = "promoted_to_business_cell"


class Priority(str, Enum):
    LOW      = "low"
    NORMAL   = "normal"
    HIGH     = "high"
    CRITICAL = "critical"


class KnowledgeScope(str, Enum):
    ISOLATED       = "isolated"
    MISSION_LOCAL  = "mission_local"
    SHAREABLE      = "shareable"
    STRATEGIC      = "strategic"


class BusinessScope(str, Enum):
    EXPLORATION        = "exploration"
    INCUBATION         = "incubation"
    EXISTING_BUSINESS_CELL = "existing_business_cell"
    INFRASTRUCTURE     = "infrastructure"
    PORTFOLIO          = "portfolio"


class CriterionType(str, Enum):
    QUANTITATIVE = "quantitative"
    QUALITATIVE  = "qualitative"
    BINARY       = "binary"
    MILESTONE    = "milestone"


class VerificationMethod(str, Enum):
    AUTOMATED = "automated"
    HUMAN     = "human"
    MIXED     = "mixed"


class ReasonCode(str, Enum):
    OBJECTIVE_MET           = "objective_met"
    BUDGET_EXHAUSTED        = "budget_exhausted"
    DEADLINE_EXCEEDED       = "deadline_exceeded"
    RISK_THRESHOLD_EXCEEDED = "risk_threshold_exceeded"
    STRATEGIC_PIVOT         = "strategic_pivot"
    EXTERNAL_BLOCKER        = "external_blocker"
    GOVERNANCE_DECISION     = "governance_decision"
    DUPLICATE_DETECTED      = "duplicate_detected"


class CapabilityLevel(str, Enum):
    BASIC    = "basic"
    STANDARD = "standard"
    ADVANCED = "advanced"
    EXPERT   = "expert"


# ---------------------------------------------------------------------------
# Sub-modelli (serializzabili come JSON)
# ---------------------------------------------------------------------------

@dataclass
class ExpectedOutcome:
    outcome_id: str
    description: str
    required: bool
    evidence_requirements: list[str] = field(default_factory=list)
    verification_method: VerificationMethod = VerificationMethod.HUMAN
    metric_name: str | None = None
    target_value: float | None = None
    target_operator: str | None = None   # ">=" | "<=" | "==" | ">" | "<"
    unit: str | None = None

    def to_dict(self) -> dict:
        return {
            "outcome_id": self.outcome_id,
            "description": self.description,
            "required": self.required,
            "evidence_requirements": self.evidence_requirements,
            "verification_method": self.verification_method.value,
            "metric_name": self.metric_name,
            "target_value": self.target_value,
            "target_operator": self.target_operator,
            "unit": self.unit,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExpectedOutcome":
        return cls(
            outcome_id=d["outcome_id"],
            description=d["description"],
            required=d["required"],
            evidence_requirements=d.get("evidence_requirements", []),
            verification_method=VerificationMethod(d.get("verification_method", "human")),
            metric_name=d.get("metric_name"),
            target_value=d.get("target_value"),
            target_operator=d.get("target_operator"),
            unit=d.get("unit"),
        )


@dataclass
class SuccessCriterion:
    criterion_id: str
    description: str
    criterion_type: CriterionType
    required: bool
    required_evidence: list[str] = field(default_factory=list)
    threshold: float | None = None

    def to_dict(self) -> dict:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "criterion_type": self.criterion_type.value,
            "required": self.required,
            "required_evidence": self.required_evidence,
            "threshold": self.threshold,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SuccessCriterion":
        return cls(
            criterion_id=d["criterion_id"],
            description=d["description"],
            criterion_type=CriterionType(d["criterion_type"]),
            required=d["required"],
            required_evidence=d.get("required_evidence", []),
            threshold=d.get("threshold"),
        )


@dataclass
class TerminationCriterion:
    criterion_id: str
    description: str
    reason_code: ReasonCode
    automatic: bool
    escalation_required: bool
    threshold: float | None = None

    def to_dict(self) -> dict:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "reason_code": self.reason_code.value,
            "automatic": self.automatic,
            "escalation_required": self.escalation_required,
            "threshold": self.threshold,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TerminationCriterion":
        return cls(
            criterion_id=d["criterion_id"],
            description=d["description"],
            reason_code=ReasonCode(d["reason_code"]),
            automatic=d["automatic"],
            escalation_required=d["escalation_required"],
            threshold=d.get("threshold"),
        )


@dataclass
class MissionBudget:
    """Budget economico della Mission. Importi in REAL (euro o valuta indicata).

    Debito tecnico: migrare a INTEGER minor units (centesimi) in un futuro
    task se il dominio economico richiede precisione decimale garantita.
    """
    currency: str = "EUR"
    approved_amount: float = 0.0
    committed_amount: float = 0.0
    spent_amount: float = 0.0
    compute_limit: float | None = None
    external_service_limit: float | None = None
    marketing_limit: float | None = None
    human_service_limit: float | None = None

    def validate(self) -> list[str]:
        errors = []
        if self.approved_amount < 0:
            errors.append("approved_amount deve essere >= 0")
        if self.committed_amount < 0:
            errors.append("committed_amount deve essere >= 0")
        if self.spent_amount < 0:
            errors.append("spent_amount deve essere >= 0")
        if self.committed_amount > self.approved_amount:
            errors.append("committed_amount non può superare approved_amount")
        if self.spent_amount > self.approved_amount:
            errors.append("spent_amount non può superare approved_amount")
        return errors

    def to_dict(self) -> dict:
        return {
            "currency": self.currency,
            "approved_amount": self.approved_amount,
            "committed_amount": self.committed_amount,
            "spent_amount": self.spent_amount,
            "compute_limit": self.compute_limit,
            "external_service_limit": self.external_service_limit,
            "marketing_limit": self.marketing_limit,
            "human_service_limit": self.human_service_limit,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MissionBudget":
        return cls(
            currency=d.get("currency", "EUR"),
            approved_amount=d.get("approved_amount", 0.0),
            committed_amount=d.get("committed_amount", 0.0),
            spent_amount=d.get("spent_amount", 0.0),
            compute_limit=d.get("compute_limit"),
            external_service_limit=d.get("external_service_limit"),
            marketing_limit=d.get("marketing_limit"),
            human_service_limit=d.get("human_service_limit"),
        )


@dataclass
class MissionRiskProfile:
    risk_level: str = "low"   # low | medium | high | critical
    reversible: bool = True
    rollback_plan_required: bool = False
    rollback_plan: str | None = None
    max_single_action_impact: float | None = None
    prohibited_actions: list[str] = field(default_factory=list)
    escalation_triggers: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors = []
        if self.risk_level not in ("low", "medium", "high", "critical"):
            errors.append(f"risk_level non valido: {self.risk_level!r}")
        if self.rollback_plan_required and not self.rollback_plan:
            errors.append("rollback_plan obbligatorio ma assente (risk_profile lo richiede)")
        return errors

    def to_dict(self) -> dict:
        return {
            "risk_level": self.risk_level,
            "reversible": self.reversible,
            "rollback_plan_required": self.rollback_plan_required,
            "rollback_plan": self.rollback_plan,
            "max_single_action_impact": self.max_single_action_impact,
            "prohibited_actions": self.prohibited_actions,
            "escalation_triggers": self.escalation_triggers,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MissionRiskProfile":
        return cls(
            risk_level=d.get("risk_level", "low"),
            reversible=d.get("reversible", True),
            rollback_plan_required=d.get("rollback_plan_required", False),
            rollback_plan=d.get("rollback_plan"),
            max_single_action_impact=d.get("max_single_action_impact"),
            prohibited_actions=d.get("prohibited_actions", []),
            escalation_triggers=d.get("escalation_triggers", []),
        )


@dataclass
class MissionAuthorityRequest:
    requested_mode: str    # autonomous | proposal | escalation_required
    requested_actions: list[str] = field(default_factory=list)
    maximum_authority_scope: str = "proposal"
    human_approval_threshold: float | None = None

    def to_dict(self) -> dict:
        return {
            "requested_mode": self.requested_mode,
            "requested_actions": self.requested_actions,
            "maximum_authority_scope": self.maximum_authority_scope,
            "human_approval_threshold": self.human_approval_threshold,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MissionAuthorityRequest":
        return cls(
            requested_mode=d.get("requested_mode", "proposal"),
            requested_actions=d.get("requested_actions", []),
            maximum_authority_scope=d.get("maximum_authority_scope", "proposal"),
            human_approval_threshold=d.get("human_approval_threshold"),
        )


@dataclass
class RequiredCapability:
    capability_id: str
    required_level: CapabilityLevel
    mandatory: bool
    capability_version: str | None = None
    specialization: str | None = None

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "required_level": self.required_level.value,
            "mandatory": self.mandatory,
            "capability_version": self.capability_version,
            "specialization": self.specialization,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RequiredCapability":
        return cls(
            capability_id=d["capability_id"],
            required_level=CapabilityLevel(d["required_level"]),
            mandatory=d["mandatory"],
            capability_version=d.get("capability_version"),
            specialization=d.get("specialization"),
        )


# ---------------------------------------------------------------------------
# Mission (record aggregato — caricato dal DB)
# ---------------------------------------------------------------------------

@dataclass
class Mission:
    """Rappresentazione in-memory di una Mission caricata dal DB.

    Non viene mai costruita direttamente dal codice applicativo: viene
    prodotta da `registry.get_mission()` o `registry.create_mission()`.
    Non contiene logica di business: è un DTO strutturato.
    """
    id: int                          # rowid SQLite (per audit_log)
    mission_id: str                  # UUID esterno
    idempotency_key: str
    correlation_id: str
    title: str
    description: str
    origin_type: OriginType
    origin_ref: str | None
    mission_type: MissionType
    status: MissionStatus
    priority: Priority
    objective: str
    expected_outcomes: list[ExpectedOutcome]
    success_criteria: list[SuccessCriterion]
    termination_criteria: list[TerminationCriterion]
    constraints: dict
    budget: MissionBudget
    risk_profile: MissionRiskProfile
    authority_request: MissionAuthorityRequest
    required_capabilities: list[RequiredCapability]
    knowledge_scope: KnowledgeScope
    business_scope: BusinessScope
    deadline: str | None
    parent_mission_id: str | None
    candidate_business_cell_id: str | None
    constitutional_version: str
    created_by: str
    assigned_organ_id: int | None
    created_at: str
    updated_at: str
    accepted_at: str | None
    activated_at: str | None
    completed_at: str | None
    terminated_at: str | None
    version: int
    metadata: dict

    @classmethod
    def from_row(cls, row: Any) -> "Mission":
        """Costruisce una Mission da una sqlite3.Row."""
        return cls(
            id=row["id"],
            mission_id=row["mission_id"],
            idempotency_key=row["idempotency_key"],
            correlation_id=row["correlation_id"],
            title=row["title"],
            description=row["description"],
            origin_type=OriginType(row["origin_type"]),
            origin_ref=row["origin_ref"],
            mission_type=MissionType(row["mission_type"]),
            status=MissionStatus(row["status"]),
            priority=Priority(row["priority"]),
            objective=row["objective"],
            expected_outcomes=[
                ExpectedOutcome.from_dict(o)
                for o in json.loads(row["expected_outcomes_json"] or "[]")
            ],
            success_criteria=[
                SuccessCriterion.from_dict(c)
                for c in json.loads(row["success_criteria_json"] or "[]")
            ],
            termination_criteria=[
                TerminationCriterion.from_dict(c)
                for c in json.loads(row["termination_criteria_json"] or "[]")
            ],
            constraints=json.loads(row["constraints_json"] or "{}"),
            budget=MissionBudget.from_dict(json.loads(row["budget_json"] or "{}")),
            risk_profile=MissionRiskProfile.from_dict(
                json.loads(row["risk_profile_json"] or "{}")
            ),
            authority_request=MissionAuthorityRequest.from_dict(
                json.loads(row["authority_request_json"] or "{}")
            ),
            required_capabilities=[
                RequiredCapability.from_dict(c)
                for c in json.loads(row["required_capabilities_json"] or "[]")
            ],
            knowledge_scope=KnowledgeScope(row["knowledge_scope"]),
            business_scope=BusinessScope(row["business_scope"]),
            deadline=row["deadline"],
            parent_mission_id=row["parent_mission_id"],
            candidate_business_cell_id=row["candidate_business_cell_id"],
            constitutional_version=row["constitutional_version"],
            created_by=row["created_by"],
            assigned_organ_id=row["assigned_organ_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            accepted_at=row["accepted_at"],
            activated_at=row["activated_at"],
            completed_at=row["completed_at"],
            terminated_at=row["terminated_at"],
            version=row["version"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )


# ---------------------------------------------------------------------------
# MissionTransitionRecord
# ---------------------------------------------------------------------------

@dataclass
class MissionTransitionRecord:
    transition_id: str
    mission_id: str
    from_status: MissionStatus
    to_status: MissionStatus
    requested_by: str
    requested_at: str
    reason: str
    correlation_id: str
    evidence_refs: list[str] = field(default_factory=list)
    authorized_by: str | None = None
    authority_decision_id: str | None = None
    constitutional_validation_id: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "transition_id": self.transition_id,
            "mission_id": self.mission_id,
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
            "reason": self.reason,
            "correlation_id": self.correlation_id,
            "evidence_refs": self.evidence_refs,
            "authorized_by": self.authorized_by,
            "authority_decision_id": self.authority_decision_id,
            "constitutional_validation_id": self.constitutional_validation_id,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# MissionIntakeRequest / MissionIntakeResult
# ---------------------------------------------------------------------------

@dataclass
class MissionIntakeRequest:
    """Input strutturato per l'intake di una Mission.

    V0 deterministico: nessun LLM per interpretare testo libero.
    Tutti i campi devono essere forniti dal chiamante in forma strutturata.
    """
    title: str
    description: str
    origin_type: OriginType
    mission_type: MissionType
    objective: str
    idempotency_key: str
    created_by: str

    # Campi strutturati
    expected_outcomes: list[ExpectedOutcome] = field(default_factory=list)
    success_criteria: list[SuccessCriterion] = field(default_factory=list)
    termination_criteria: list[TerminationCriterion] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    budget: MissionBudget = field(default_factory=MissionBudget)
    risk_profile: MissionRiskProfile = field(default_factory=MissionRiskProfile)
    authority_request: MissionAuthorityRequest = field(
        default_factory=lambda: MissionAuthorityRequest(requested_mode="proposal")
    )
    required_capabilities: list[RequiredCapability] = field(default_factory=list)
    knowledge_scope: KnowledgeScope = KnowledgeScope.MISSION_LOCAL
    business_scope: BusinessScope = BusinessScope.EXPLORATION
    priority: Priority = Priority.NORMAL

    # Opzionali
    origin_ref: str | None = None
    deadline: str | None = None
    parent_mission_id: str | None = None
    correlation_id: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class MissionIntakeResult:
    """Risultato dell'intake di una Mission."""
    intake_id: str
    status: str                      # accepted | rejected | duplicate
    accepted: bool
    created_at: str
    explanation: str
    mission_id: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    constitutional_validation_id: str | None = None
    authority_decision_id: str | None = None
    duplicate_of: str | None = None

    def to_dict(self) -> dict:
        return {
            "intake_id": self.intake_id,
            "status": self.status,
            "accepted": self.accepted,
            "created_at": self.created_at,
            "explanation": self.explanation,
            "mission_id": self.mission_id,
            "validation_errors": self.validation_errors,
            "warnings": self.warnings,
            "missing_fields": self.missing_fields,
            "constitutional_validation_id": self.constitutional_validation_id,
            "authority_decision_id": self.authority_decision_id,
            "duplicate_of": self.duplicate_of,
        }


# ---------------------------------------------------------------------------
# Eccezioni
# ---------------------------------------------------------------------------

class MissionNotFoundError(LookupError):
    """Mission non trovata nel registry."""


class MissionTransitionError(ValueError):
    """Transizione di stato non consentita dalla state machine."""


class MissionVersionConflict(RuntimeError):
    """Conflitto di versione (optimistic locking): la Mission è stata modificata
    concorrentemente. Il chiamante deve ricaricarla e riprovare."""


class MissionIdempotencyReplay(Exception):
    """L'idempotency_key è già stata usata: ritorna la Mission originale."""
    def __init__(self, mission_id: str) -> None:
        self.mission_id = mission_id
        super().__init__(f"idempotency_key già utilizzata per mission_id={mission_id!r}")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_mission_id() -> str:
    return str(uuid.uuid4())


def new_transition_id() -> str:
    return str(uuid.uuid4())


def new_intake_id() -> str:
    return str(uuid.uuid4())

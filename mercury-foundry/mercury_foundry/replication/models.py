"""Modelli del Replication Layer — MF-REPL-001.

Fasi coperte:
  2 — Genesis Domain Model
  3 — Genetic Package
  4 — Independence Contract
  5 — Product Family Assessment
  6 — Replication Gate
 11 — Federation Contract V0
 12 — Portable Capability Placeholder

Tutti i modelli usano enum + dataclass. Nessun LLM, nessuna euristica.
Budget come REAL (coerente con max_budget REAL in decision_mandates).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Fase 2 — Genesis Domain Model enumerazioni
# ---------------------------------------------------------------------------

class GenesisReason(str, Enum):
    VALIDATED_PRODUCT        = "validated_product"
    VALIDATED_PRODUCT_FAMILY = "validated_product_family"
    STRATEGIC_SPINOUT        = "strategic_spinout"
    CUSTOMER_DEDICATED       = "customer_dedicated_instance"
    REGIONAL_SPECIALIZATION  = "regional_specialization"
    REGULATORY_ISOLATION     = "regulatory_isolation"
    CAPACITY_ISOLATION       = "capacity_isolation"
    EXPERIMENTAL_REPLICA     = "experimental_replica"
    CUSTOM                   = "custom"


class GenesisStatus(str, Enum):
    DRAFT                   = "draft"
    PROPOSED                = "proposed"
    UNDER_REVIEW            = "under_review"
    APPROVED                = "approved"
    REJECTED                = "rejected"
    PACKAGING               = "packaging"
    READY_FOR_PROVISIONING  = "ready_for_provisioning"
    PROVISIONING            = "provisioning"   # V0: non raggiungibile automaticamente
    ACTIVATED               = "activated"      # V0: non raggiungibile automaticamente
    SUSPENDED               = "suspended"
    FAILED                  = "failed"
    ABORTED                 = "aborted"
    ARCHIVED                = "archived"

    # Stati non raggiungibili automaticamente in V0
    V0_BLOCKED_STATUSES: frozenset = frozenset({"provisioning", "activated"})


class InstanceType(str, Enum):
    PRODUCT_DEDICATED  = "product_dedicated"
    PRODUCT_FAMILY     = "product_family"
    CUSTOMER_DEDICATED = "customer_dedicated"
    REGIONAL           = "regional"
    INFRASTRUCTURE     = "infrastructure"
    RESEARCH           = "research"
    STRATEGIC          = "strategic"


class IndependenceStatus(str, Enum):
    NOT_ASSESSED           = "not_assessed"
    INSUFFICIENT           = "insufficient"
    CONDITIONALLY_READY    = "conditionally_ready"
    READY_FOR_PROVISIONING = "ready_for_provisioning"
    INDEPENDENT            = "independent"
    DEGRADED               = "degraded"
    SUSPENDED              = "suspended"


class GateStatus(str, Enum):
    PASS                 = "pass"
    PASS_WITH_CONDITIONS = "pass_with_conditions"
    FAIL                 = "fail"
    REQUIRE_REVIEW       = "require_review"


class FamilyRecommendation(str, Enum):
    SINGLE_DEDICATED_MERCURY = "single_dedicated_mercury"
    SEPARATE_INSTANCES       = "separate_instances"
    MANUAL_REVIEW            = "manual_review"
    INSUFFICIENT_EVIDENCE    = "insufficient_evidence"


class PortabilityStatus(str, Enum):
    UNKNOWN           = "unknown"
    NOT_PORTABLE      = "not_portable"
    PARTIALLY_PORTABLE= "partially_portable"
    PORTABLE          = "portable"
    VERIFIED          = "verified"


# ---------------------------------------------------------------------------
# Fase 12 — Portable Capability Placeholder
# ---------------------------------------------------------------------------

@dataclass
class PortableCapabilityBundleRef:
    bundle_id: str
    version: str
    capability_ids: list[str] = field(default_factory=list)
    dependency_manifest: dict = field(default_factory=dict)
    integrity_checksum: str | None = None
    portability_status: PortabilityStatus = PortabilityStatus.UNKNOWN
    source_instance_id: str | None = None
    target_compatibility: list[str] = field(default_factory=list)
    required_runtime: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "version": self.version,
            "capability_ids": self.capability_ids,
            "dependency_manifest": self.dependency_manifest,
            "integrity_checksum": self.integrity_checksum,
            "portability_status": self.portability_status.value,
            "source_instance_id": self.source_instance_id,
            "target_compatibility": self.target_compatibility,
            "required_runtime": self.required_runtime,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PortableCapabilityBundleRef":
        return cls(
            bundle_id=d["bundle_id"],
            version=d["version"],
            capability_ids=d.get("capability_ids", []),
            dependency_manifest=d.get("dependency_manifest", {}),
            integrity_checksum=d.get("integrity_checksum"),
            portability_status=PortabilityStatus(d.get("portability_status", "unknown")),
            source_instance_id=d.get("source_instance_id"),
            target_compatibility=d.get("target_compatibility", []),
            required_runtime=d.get("required_runtime"),
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Fase 2 — DedicatedMercuryGenesisRequest
# ---------------------------------------------------------------------------

@dataclass
class DedicatedMercuryGenesisRequest:
    """Richiesta di genesis di una Dedicated Mercury.

    Modello di intake strutturato. Non avvia nessun provisioning.
    In V0 non è possibile raggiungere i stati provisioning o activated.
    """
    genesis_request_id: str
    idempotency_key: str
    correlation_id: str
    source_mission_id: str
    validated_product_ids: list[str]
    proposed_instance_name: str
    proposed_instance_slug: str
    genesis_reason: GenesisReason
    validation_evidence_refs: list[str]
    target_market: str
    target_customer: str
    business_model: str
    constitutional_version: str
    kernel_version: str
    requested_by: str
    requested_at: str

    # Opzionali
    source_expedition_id: str | None = None
    product_family_key: str | None = None
    product_validation_score: float | None = None
    pmf_confidence: float | None = None
    requested_genesis_profile: str = "standard"
    requested_capability_bundle_ids: list[str] = field(default_factory=list)
    requested_knowledge_package_ids: list[str] = field(default_factory=list)
    requested_budget_envelope: float = 0.0
    requested_authority_profile: dict = field(default_factory=dict)
    requested_isolation_profile: dict = field(default_factory=dict)
    requested_federation_profile: dict = field(default_factory=dict)
    requested_reporting_profile: dict = field(default_factory=dict)
    requested_parent_relationship: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.source_mission_id.strip():
            errors.append("source_mission_id è obbligatorio")
        if not self.validated_product_ids:
            errors.append("validated_product_ids non può essere vuoto")
        if not self.proposed_instance_name.strip():
            errors.append("proposed_instance_name è obbligatorio")
        if not self.proposed_instance_slug.strip():
            errors.append("proposed_instance_slug è obbligatorio")
        if not self.validation_evidence_refs:
            errors.append("validation_evidence_refs non può essere vuoto (CONST-001)")
        if not self.target_market.strip():
            errors.append("target_market è obbligatorio")
        if not self.target_customer.strip():
            errors.append("target_customer è obbligatorio")
        if not self.business_model.strip():
            errors.append("business_model è obbligatorio")
        if self.requested_budget_envelope < 0:
            errors.append("requested_budget_envelope deve essere >= 0")
        if self.product_validation_score is not None:
            if not (0.0 <= self.product_validation_score <= 1.0):
                errors.append("product_validation_score deve essere in [0.0, 1.0]")
        if self.pmf_confidence is not None:
            if not (0.0 <= self.pmf_confidence <= 1.0):
                errors.append("pmf_confidence deve essere in [0.0, 1.0]")
        return errors

    def to_dict(self) -> dict:
        return {
            "genesis_request_id": self.genesis_request_id,
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "source_mission_id": self.source_mission_id,
            "source_expedition_id": self.source_expedition_id,
            "validated_product_ids": self.validated_product_ids,
            "product_family_key": self.product_family_key,
            "proposed_instance_name": self.proposed_instance_name,
            "proposed_instance_slug": self.proposed_instance_slug,
            "genesis_reason": self.genesis_reason.value,
            "validation_evidence_refs": self.validation_evidence_refs,
            "product_validation_score": self.product_validation_score,
            "pmf_confidence": self.pmf_confidence,
            "target_market": self.target_market,
            "target_customer": self.target_customer,
            "business_model": self.business_model,
            "constitutional_version": self.constitutional_version,
            "kernel_version": self.kernel_version,
            "requested_genesis_profile": self.requested_genesis_profile,
            "requested_capability_bundle_ids": self.requested_capability_bundle_ids,
            "requested_knowledge_package_ids": self.requested_knowledge_package_ids,
            "requested_budget_envelope": self.requested_budget_envelope,
            "requested_authority_profile": self.requested_authority_profile,
            "requested_isolation_profile": self.requested_isolation_profile,
            "requested_federation_profile": self.requested_federation_profile,
            "requested_reporting_profile": self.requested_reporting_profile,
            "requested_parent_relationship": self.requested_parent_relationship,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Fase 2 — ProductScope + DedicatedMercuryIdentity
# ---------------------------------------------------------------------------

@dataclass
class ProductScope:
    primary_product_ids: list[str]
    allowed_related_product_categories: list[str] = field(default_factory=list)
    prohibited_product_categories: list[str] = field(default_factory=list)
    product_family_key: str | None = None
    expansion_requires_review: bool = True

    def to_dict(self) -> dict:
        return {
            "primary_product_ids": self.primary_product_ids,
            "allowed_related_product_categories": self.allowed_related_product_categories,
            "prohibited_product_categories": self.prohibited_product_categories,
            "product_family_key": self.product_family_key,
            "expansion_requires_review": self.expansion_requires_review,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProductScope":
        return cls(
            primary_product_ids=d.get("primary_product_ids", []),
            allowed_related_product_categories=d.get("allowed_related_product_categories", []),
            prohibited_product_categories=d.get("prohibited_product_categories", []),
            product_family_key=d.get("product_family_key"),
            expansion_requires_review=d.get("expansion_requires_review", True),
        )


@dataclass
class DedicatedMercuryIdentity:
    instance_id: str
    instance_name: str
    instance_slug: str
    instance_type: InstanceType
    product_scope: ProductScope
    market_scope: str
    business_scope: str
    constitutional_version: str
    kernel_version: str
    created_from_genesis_request_id: str
    created_from_mission_id: str
    status: GenesisStatus
    created_at: str

    # Opzionali (lineage)
    parent_instance_id: str | None = None
    lineage_root_id: str | None = None
    generation_number: int = 1
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "parent_instance_id": self.parent_instance_id,
            "lineage_root_id": self.lineage_root_id,
            "generation_number": self.generation_number,
            "instance_name": self.instance_name,
            "instance_slug": self.instance_slug,
            "instance_type": self.instance_type.value,
            "product_scope": self.product_scope.to_dict(),
            "market_scope": self.market_scope,
            "business_scope": self.business_scope,
            "created_from_genesis_request_id": self.created_from_genesis_request_id,
            "created_from_mission_id": self.created_from_mission_id,
            "constitutional_version": self.constitutional_version,
            "kernel_version": self.kernel_version,
            "status": self.status.value,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DedicatedMercuryIdentity":
        return cls(
            instance_id=d["instance_id"],
            parent_instance_id=d.get("parent_instance_id"),
            lineage_root_id=d.get("lineage_root_id"),
            generation_number=d.get("generation_number", 1),
            instance_name=d["instance_name"],
            instance_slug=d["instance_slug"],
            instance_type=InstanceType(d["instance_type"]),
            product_scope=ProductScope.from_dict(d.get("product_scope", {})),
            market_scope=d.get("market_scope", ""),
            business_scope=d.get("business_scope", ""),
            created_from_genesis_request_id=d["created_from_genesis_request_id"],
            created_from_mission_id=d["created_from_mission_id"],
            constitutional_version=d["constitutional_version"],
            kernel_version=d["kernel_version"],
            status=GenesisStatus(d["status"]),
            created_at=d["created_at"],
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Fase 3 — Genetic Package sub-models
# ---------------------------------------------------------------------------

@dataclass
class ConstitutionalPackage:
    constitution_version: str
    principle_ids: list[str]
    immutable_principles: list[str]
    inherited_enforcement_modes: dict   # principle_id → mode
    local_amendment_policy: str = "require_review"
    upstream_update_policy: str = "accept_proposals_only"

    def to_dict(self) -> dict:
        return {
            "constitution_version": self.constitution_version,
            "principle_ids": self.principle_ids,
            "immutable_principles": self.immutable_principles,
            "inherited_enforcement_modes": self.inherited_enforcement_modes,
            "local_amendment_policy": self.local_amendment_policy,
            "upstream_update_policy": self.upstream_update_policy,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConstitutionalPackage":
        return cls(
            constitution_version=d["constitution_version"],
            principle_ids=d.get("principle_ids", []),
            immutable_principles=d.get("immutable_principles", []),
            inherited_enforcement_modes=d.get("inherited_enforcement_modes", {}),
            local_amendment_policy=d.get("local_amendment_policy", "require_review"),
            upstream_update_policy=d.get("upstream_update_policy", "accept_proposals_only"),
        )


@dataclass
class KernelManifest:
    kernel_version: str
    required_modules: list[str]
    optional_modules: list[str] = field(default_factory=list)
    compatibility_constraints: dict = field(default_factory=dict)
    migration_requirements: list[str] = field(default_factory=list)
    runtime_requirements: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kernel_version": self.kernel_version,
            "required_modules": self.required_modules,
            "optional_modules": self.optional_modules,
            "compatibility_constraints": self.compatibility_constraints,
            "migration_requirements": self.migration_requirements,
            "runtime_requirements": self.runtime_requirements,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KernelManifest":
        return cls(
            kernel_version=d["kernel_version"],
            required_modules=d.get("required_modules", []),
            optional_modules=d.get("optional_modules", []),
            compatibility_constraints=d.get("compatibility_constraints", {}),
            migration_requirements=d.get("migration_requirements", []),
            runtime_requirements=d.get("runtime_requirements", {}),
        )


@dataclass
class GovernancePackage:
    organ_definitions: list[dict]
    decision_mandates: list[dict]
    authority_modes: dict
    budget_limits: dict
    risk_limits: dict
    escalation_rules: list[str] = field(default_factory=list)
    prohibited_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "organ_definitions": self.organ_definitions,
            "decision_mandates": self.decision_mandates,
            "authority_modes": self.authority_modes,
            "budget_limits": self.budget_limits,
            "risk_limits": self.risk_limits,
            "escalation_rules": self.escalation_rules,
            "prohibited_actions": self.prohibited_actions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GovernancePackage":
        return cls(
            organ_definitions=d.get("organ_definitions", []),
            decision_mandates=d.get("decision_mandates", []),
            authority_modes=d.get("authority_modes", {}),
            budget_limits=d.get("budget_limits", {}),
            risk_limits=d.get("risk_limits", {}),
            escalation_rules=d.get("escalation_rules", []),
            prohibited_actions=d.get("prohibited_actions", []),
        )


@dataclass
class CapabilityBundleManifest:
    bundle_ids: list[str]
    capability_ids: list[str]
    versions: dict   # capability_id → version
    dependency_graph: dict = field(default_factory=dict)
    required_tools: list[str] = field(default_factory=list)
    portability_status: dict = field(default_factory=dict)  # bundle_id → PortabilityStatus

    def to_dict(self) -> dict:
        return {
            "bundle_ids": self.bundle_ids,
            "capability_ids": self.capability_ids,
            "versions": self.versions,
            "dependency_graph": self.dependency_graph,
            "required_tools": self.required_tools,
            "portability_status": self.portability_status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityBundleManifest":
        return cls(
            bundle_ids=d.get("bundle_ids", []),
            capability_ids=d.get("capability_ids", []),
            versions=d.get("versions", {}),
            dependency_graph=d.get("dependency_graph", {}),
            required_tools=d.get("required_tools", []),
            portability_status=d.get("portability_status", {}),
        )


@dataclass
class KnowledgePackageManifest:
    package_ids: list[str]
    knowledge_scope: str
    source_refs: list[str]
    classifications: list[str] = field(default_factory=list)
    allowed_propagation: list[str] = field(default_factory=list)
    local_only_refs: list[str] = field(default_factory=list)
    strategic_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "package_ids": self.package_ids,
            "knowledge_scope": self.knowledge_scope,
            "source_refs": self.source_refs,
            "classifications": self.classifications,
            "allowed_propagation": self.allowed_propagation,
            "local_only_refs": self.local_only_refs,
            "strategic_refs": self.strategic_refs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgePackageManifest":
        return cls(
            package_ids=d.get("package_ids", []),
            knowledge_scope=d.get("knowledge_scope", "mission_local"),
            source_refs=d.get("source_refs", []),
            classifications=d.get("classifications", []),
            allowed_propagation=d.get("allowed_propagation", []),
            local_only_refs=d.get("local_only_refs", []),
            strategic_refs=d.get("strategic_refs", []),
        )


@dataclass
class MissionGenealogy:
    source_mission_id: str
    parent_mission_ids: list[str]
    evidence_refs: list[str]
    assumptions: list[str]
    validation_history: list[dict]
    genesis_rationale: str
    source_expedition_id: str | None = None
    decision_records: list[str] = field(default_factory=list)
    termination_criteria: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_mission_id": self.source_mission_id,
            "parent_mission_ids": self.parent_mission_ids,
            "source_expedition_id": self.source_expedition_id,
            "decision_records": self.decision_records,
            "evidence_refs": self.evidence_refs,
            "assumptions": self.assumptions,
            "validation_history": self.validation_history,
            "termination_criteria": self.termination_criteria,
            "genesis_rationale": self.genesis_rationale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MissionGenealogy":
        return cls(
            source_mission_id=d["source_mission_id"],
            parent_mission_ids=d.get("parent_mission_ids", []),
            source_expedition_id=d.get("source_expedition_id"),
            decision_records=d.get("decision_records", []),
            evidence_refs=d.get("evidence_refs", []),
            assumptions=d.get("assumptions", []),
            validation_history=d.get("validation_history", []),
            termination_criteria=d.get("termination_criteria", []),
            genesis_rationale=d.get("genesis_rationale", ""),
        )


@dataclass
class ProductEvidencePackage:
    product_ids: list[str]
    demand_evidence: list[str]
    value_evidence: list[str]
    feasibility_evidence: list[str]
    business_viability_evidence: list[str]
    customer_evidence: list[str]
    confidence_score: float
    unresolved_assumptions: list[str] = field(default_factory=list)
    known_risks: list[str] = field(default_factory=list)
    revenue_evidence: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors = []
        if not (0.0 <= self.confidence_score <= 1.0):
            errors.append("confidence_score deve essere in [0.0, 1.0]")
        if not self.demand_evidence:
            errors.append("demand_evidence è obbligatorio")
        if not self.value_evidence:
            errors.append("value_evidence è obbligatorio")
        return errors

    def to_dict(self) -> dict:
        return {
            "product_ids": self.product_ids,
            "demand_evidence": self.demand_evidence,
            "value_evidence": self.value_evidence,
            "feasibility_evidence": self.feasibility_evidence,
            "business_viability_evidence": self.business_viability_evidence,
            "customer_evidence": self.customer_evidence,
            "revenue_evidence": self.revenue_evidence,
            "confidence_score": self.confidence_score,
            "unresolved_assumptions": self.unresolved_assumptions,
            "known_risks": self.known_risks,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProductEvidencePackage":
        return cls(
            product_ids=d.get("product_ids", []),
            demand_evidence=d.get("demand_evidence", []),
            value_evidence=d.get("value_evidence", []),
            feasibility_evidence=d.get("feasibility_evidence", []),
            business_viability_evidence=d.get("business_viability_evidence", []),
            customer_evidence=d.get("customer_evidence", []),
            revenue_evidence=d.get("revenue_evidence", []),
            confidence_score=d.get("confidence_score", 0.0),
            unresolved_assumptions=d.get("unresolved_assumptions", []),
            known_risks=d.get("known_risks", []),
        )


@dataclass
class IntegrityManifest:
    package_checksum: str
    component_checksums: dict   # component_name → sha256
    schema_versions: dict       # component_name → version string
    generated_at: str
    validation_status: str = "pending"  # pending | valid | invalid

    def to_dict(self) -> dict:
        return {
            "package_checksum": self.package_checksum,
            "component_checksums": self.component_checksums,
            "schema_versions": self.schema_versions,
            "generated_at": self.generated_at,
            "validation_status": self.validation_status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IntegrityManifest":
        return cls(
            package_checksum=d["package_checksum"],
            component_checksums=d.get("component_checksums", {}),
            schema_versions=d.get("schema_versions", {}),
            generated_at=d.get("generated_at", ""),
            validation_status=d.get("validation_status", "pending"),
        )


@dataclass
class MercuryGeneticPackage:
    """Pacchetto genetico che una futura Dedicated Mercury riceve alla nascita.

    Immutabile dopo approvazione (status=sealed). Versionato, serializzabile,
    verificabile via checksum SHA-256. Indipendente da path locali non portabili.

    NON include:
    - path assoluti del filesystem del Main
    - credenziali o segreti
    - riferimenti a socket o processi locali
    - dati operativi clienti del Main
    """
    package_id: str
    package_version: str
    source_instance_id: str
    constitutional_package: ConstitutionalPackage
    kernel_manifest: KernelManifest
    governance_package: GovernancePackage
    mission_genealogy: MissionGenealogy
    product_evidence_package: ProductEvidencePackage
    integrity_manifest: IntegrityManifest
    generated_at: str
    generated_by: str
    checksum: str
    status: str = "draft"   # draft | sealed | invalid

    # Opzionali
    target_instance_id: str | None = None
    capability_bundle_manifest: CapabilityBundleManifest | None = None
    knowledge_package_manifest: KnowledgePackageManifest | None = None
    operational_policy_package: dict = field(default_factory=dict)
    audit_seed: dict = field(default_factory=dict)
    federation_contract: dict = field(default_factory=dict)
    reporting_contract: dict = field(default_factory=dict)
    economic_envelope: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "package_id": self.package_id,
            "package_version": self.package_version,
            "source_instance_id": self.source_instance_id,
            "target_instance_id": self.target_instance_id,
            "constitutional_package": self.constitutional_package.to_dict(),
            "kernel_manifest": self.kernel_manifest.to_dict(),
            "governance_package": self.governance_package.to_dict(),
            "capability_bundle_manifest": (
                self.capability_bundle_manifest.to_dict()
                if self.capability_bundle_manifest else None
            ),
            "knowledge_package_manifest": (
                self.knowledge_package_manifest.to_dict()
                if self.knowledge_package_manifest else None
            ),
            "mission_genealogy": self.mission_genealogy.to_dict(),
            "product_evidence_package": self.product_evidence_package.to_dict(),
            "operational_policy_package": self.operational_policy_package,
            "audit_seed": self.audit_seed,
            "federation_contract": self.federation_contract,
            "reporting_contract": self.reporting_contract,
            "economic_envelope": self.economic_envelope,
            "integrity_manifest": self.integrity_manifest.to_dict(),
            "generated_at": self.generated_at,
            "generated_by": self.generated_by,
            "checksum": self.checksum,
            "status": self.status,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MercuryGeneticPackage":
        cap = d.get("capability_bundle_manifest")
        kn = d.get("knowledge_package_manifest")
        return cls(
            package_id=d["package_id"],
            package_version=d["package_version"],
            source_instance_id=d["source_instance_id"],
            target_instance_id=d.get("target_instance_id"),
            constitutional_package=ConstitutionalPackage.from_dict(d["constitutional_package"]),
            kernel_manifest=KernelManifest.from_dict(d["kernel_manifest"]),
            governance_package=GovernancePackage.from_dict(d["governance_package"]),
            capability_bundle_manifest=(
                CapabilityBundleManifest.from_dict(cap) if cap else None
            ),
            knowledge_package_manifest=(
                KnowledgePackageManifest.from_dict(kn) if kn else None
            ),
            mission_genealogy=MissionGenealogy.from_dict(d["mission_genealogy"]),
            product_evidence_package=ProductEvidencePackage.from_dict(d["product_evidence_package"]),
            operational_policy_package=d.get("operational_policy_package", {}),
            audit_seed=d.get("audit_seed", {}),
            federation_contract=d.get("federation_contract", {}),
            reporting_contract=d.get("reporting_contract", {}),
            economic_envelope=d.get("economic_envelope", {}),
            integrity_manifest=IntegrityManifest.from_dict(d["integrity_manifest"]),
            generated_at=d["generated_at"],
            generated_by=d["generated_by"],
            checksum=d["checksum"],
            status=d.get("status", "draft"),
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Fase 4 — Independence Contract
# ---------------------------------------------------------------------------

# Dipendenze proibite dal Main in V0
PROHIBITED_MAIN_DEPENDENCIES: frozenset[str] = frozenset({
    "marketing_execution",
    "sales_execution",
    "product_delivery",
    "customer_support",
    "finance_operations",
    "daily_operational_decisions",
    "primary_operational_memory",
    "shared_customer_database",
    "permanent_main_agents",
})

# Dipendenze consentite dal Main (leggere, federazione)
ALLOWED_MAIN_DEPENDENCIES: frozenset[str] = frozenset({
    "constitutional_update_proposals",
    "security_advisories",
    "optional_capability_update_packages",
    "portfolio_level_reporting",
    "emergency_escalation",
    "initial_bootstrap_artifacts",
})


@dataclass
class DedicatedMercuryIndependenceContract:
    contract_id: str
    genesis_request_id: str
    required_local_capabilities: list[str]
    required_local_knowledge: list[str]
    required_local_organs: list[str]
    required_local_storage: list[str]
    required_local_audit: list[str]
    required_local_governance: list[str]
    required_local_budget_control: list[str]
    required_local_product_os_components: list[str]
    allowed_main_dependencies: list[str]
    prohibited_main_dependencies: list[str]
    temporary_bootstrap_dependencies: list[str]
    bootstrap_expiry_conditions: list[str]
    emergency_support_policy: str
    reporting_obligations: list[str]
    knowledge_return_policy: str
    capital_return_policy: str
    update_subscription_policy: str
    autonomy_target: str
    evaluated_at: str
    status: IndependenceStatus
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    instance_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "instance_id": self.instance_id,
            "genesis_request_id": self.genesis_request_id,
            "required_local_capabilities": self.required_local_capabilities,
            "required_local_knowledge": self.required_local_knowledge,
            "required_local_organs": self.required_local_organs,
            "required_local_storage": self.required_local_storage,
            "required_local_audit": self.required_local_audit,
            "required_local_governance": self.required_local_governance,
            "required_local_budget_control": self.required_local_budget_control,
            "required_local_product_os_components": self.required_local_product_os_components,
            "allowed_main_dependencies": self.allowed_main_dependencies,
            "prohibited_main_dependencies": self.prohibited_main_dependencies,
            "temporary_bootstrap_dependencies": self.temporary_bootstrap_dependencies,
            "bootstrap_expiry_conditions": self.bootstrap_expiry_conditions,
            "emergency_support_policy": self.emergency_support_policy,
            "reporting_obligations": self.reporting_obligations,
            "knowledge_return_policy": self.knowledge_return_policy,
            "capital_return_policy": self.capital_return_policy,
            "update_subscription_policy": self.update_subscription_policy,
            "autonomy_target": self.autonomy_target,
            "evaluated_at": self.evaluated_at,
            "status": self.status.value,
            "blockers": self.blockers,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DedicatedMercuryIndependenceContract":
        return cls(
            contract_id=d["contract_id"],
            instance_id=d.get("instance_id"),
            genesis_request_id=d["genesis_request_id"],
            required_local_capabilities=d.get("required_local_capabilities", []),
            required_local_knowledge=d.get("required_local_knowledge", []),
            required_local_organs=d.get("required_local_organs", []),
            required_local_storage=d.get("required_local_storage", []),
            required_local_audit=d.get("required_local_audit", []),
            required_local_governance=d.get("required_local_governance", []),
            required_local_budget_control=d.get("required_local_budget_control", []),
            required_local_product_os_components=d.get("required_local_product_os_components", []),
            allowed_main_dependencies=d.get("allowed_main_dependencies", []),
            prohibited_main_dependencies=d.get("prohibited_main_dependencies", []),
            temporary_bootstrap_dependencies=d.get("temporary_bootstrap_dependencies", []),
            bootstrap_expiry_conditions=d.get("bootstrap_expiry_conditions", []),
            emergency_support_policy=d.get("emergency_support_policy", "escalation_required"),
            reporting_obligations=d.get("reporting_obligations", []),
            knowledge_return_policy=d.get("knowledge_return_policy", "voluntary_shareable"),
            capital_return_policy=d.get("capital_return_policy", "portfolio_reporting_only"),
            update_subscription_policy=d.get("update_subscription_policy", "opt_in"),
            autonomy_target=d.get("autonomy_target", "full"),
            evaluated_at=d.get("evaluated_at", ""),
            status=IndependenceStatus(d.get("status", "not_assessed")),
            blockers=d.get("blockers", []),
            warnings=d.get("warnings", []),
        )


# ---------------------------------------------------------------------------
# Fase 5 — Product Family Assessment
# ---------------------------------------------------------------------------

@dataclass
class ProductFamilyAssessment:
    assessment_id: str
    product_ids: list[str]
    shared_customer: bool
    shared_market: bool
    shared_problem_space: bool
    shared_capabilities: bool
    shared_distribution: bool
    shared_business_model: bool
    shared_data_boundary: bool
    shared_regulatory_boundary: bool
    coherence_score: float          # 0.0 – 1.0
    conflicts: list[str]
    recommendation: FamilyRecommendation
    evaluated_at: str
    evidence_refs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "assessment_id": self.assessment_id,
            "product_ids": self.product_ids,
            "shared_customer": self.shared_customer,
            "shared_market": self.shared_market,
            "shared_problem_space": self.shared_problem_space,
            "shared_capabilities": self.shared_capabilities,
            "shared_distribution": self.shared_distribution,
            "shared_business_model": self.shared_business_model,
            "shared_data_boundary": self.shared_data_boundary,
            "shared_regulatory_boundary": self.shared_regulatory_boundary,
            "coherence_score": self.coherence_score,
            "conflicts": self.conflicts,
            "recommendation": self.recommendation.value,
            "evaluated_at": self.evaluated_at,
            "evidence_refs": self.evidence_refs,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProductFamilyAssessment":
        return cls(
            assessment_id=d["assessment_id"],
            product_ids=d.get("product_ids", []),
            shared_customer=d.get("shared_customer", False),
            shared_market=d.get("shared_market", False),
            shared_problem_space=d.get("shared_problem_space", False),
            shared_capabilities=d.get("shared_capabilities", False),
            shared_distribution=d.get("shared_distribution", False),
            shared_business_model=d.get("shared_business_model", False),
            shared_data_boundary=d.get("shared_data_boundary", False),
            shared_regulatory_boundary=d.get("shared_regulatory_boundary", False),
            coherence_score=d.get("coherence_score", 0.0),
            conflicts=d.get("conflicts", []),
            recommendation=FamilyRecommendation(
                d.get("recommendation", "insufficient_evidence")
            ),
            evaluated_at=d.get("evaluated_at", ""),
            evidence_refs=d.get("evidence_refs", []),
            warnings=d.get("warnings", []),
        )


# ---------------------------------------------------------------------------
# Fase 6 — Replication Gate
# ---------------------------------------------------------------------------

@dataclass
class ReplicationGateRequest:
    request_id: str
    genesis_request_id: str
    source_mission_id: str
    product_ids: list[str]
    evidence_refs: list[str]
    validation_summary: str
    economic_summary: str
    independence_contract_id: str
    requested_at: str
    requested_by: str
    correlation_id: str
    source_expedition_id: str | None = None
    family_assessment_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "genesis_request_id": self.genesis_request_id,
            "source_mission_id": self.source_mission_id,
            "source_expedition_id": self.source_expedition_id,
            "product_ids": self.product_ids,
            "evidence_refs": self.evidence_refs,
            "validation_summary": self.validation_summary,
            "economic_summary": self.economic_summary,
            "independence_contract_id": self.independence_contract_id,
            "family_assessment_id": self.family_assessment_id,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "correlation_id": self.correlation_id,
        }


@dataclass
class ReplicationGateResult:
    gate_result_id: str
    approved: bool
    status: GateStatus
    validation_score: float
    independence_status: IndependenceStatus
    family_coherence_status: str
    constitutional_status: str
    authority_status: str
    economic_readiness: bool
    unresolved_assumptions: list[str]
    blockers: list[str]
    warnings: list[str]
    required_actions: list[str]
    evaluated_at: str
    explanation: str
    approved_genesis_profile: str | None = None
    constitutional_validation_id: str | None = None
    authority_decision_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "gate_result_id": self.gate_result_id,
            "approved": self.approved,
            "status": self.status.value,
            "validation_score": self.validation_score,
            "independence_status": self.independence_status.value,
            "family_coherence_status": self.family_coherence_status,
            "constitutional_status": self.constitutional_status,
            "authority_status": self.authority_status,
            "economic_readiness": self.economic_readiness,
            "unresolved_assumptions": self.unresolved_assumptions,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "required_actions": self.required_actions,
            "approved_genesis_profile": self.approved_genesis_profile,
            "evaluated_at": self.evaluated_at,
            "explanation": self.explanation,
            "constitutional_validation_id": self.constitutional_validation_id,
            "authority_decision_id": self.authority_decision_id,
        }


# ---------------------------------------------------------------------------
# Fase 7 — Genesis Transition Record
# ---------------------------------------------------------------------------

@dataclass
class DedicatedMercuryGenesisTransitionRecord:
    transition_id: str
    genesis_request_id: str
    from_status: GenesisStatus
    to_status: GenesisStatus
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
            "genesis_request_id": self.genesis_request_id,
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
# Fase 11 — Federation Contract V0
# ---------------------------------------------------------------------------

@dataclass
class MotherReplicaFederationContract:
    """Contratto federativo leggero tra Mercury Main e una Dedicated Mercury.

    Chiarisce:
    - la replica non richiede autorizzazione del Main per operazioni ordinarie entro mandato
    - il Main non accede automaticamente ai dati clienti
    - la replica restituisce solo dati consentiti dal reporting_obligations
    - aggiornamenti di capability e Kernel sono proposte versionate
    - la replica può rifiutare aggiornamenti incompatibili
    - emergenze e violazioni costituzionali possono generare escalation

    NON implementa networking o sincronizzazione.
    """
    federation_contract_id: str
    mother_instance_id: str
    constitutional_relationship: str
    reporting_frequency: str
    allowed_metrics: list[str]
    knowledge_return_policy: str
    strategic_incident_policy: str
    update_proposal_policy: str
    capability_update_policy: str
    capital_distribution_policy: str
    emergency_intervention_policy: str
    termination_policy: str
    data_isolation_policy: str
    communication_channels: list[str]
    status: str
    created_at: str
    child_instance_id: str | None = None

    # Principi federativi espliciti
    PRINCIPLES: list[str] = field(default_factory=lambda: [
        "replica_autonomous_within_mandate",
        "main_no_automatic_customer_data_access",
        "replica_returns_only_allowed_data",
        "capability_updates_are_versioned_proposals",
        "replica_may_reject_incompatible_updates",
        "constitutional_violations_trigger_escalation",
    ])

    def validate(self) -> list[str]:
        errors = []
        # Cerca pattern positivi di accesso (non la negazione "no_automatic_*")
        policy = self.data_isolation_policy.lower()
        positive_access_patterns = [
            "allow_automatic_customer_data",
            "enables_customer_data_sharing",
            "grants_main_customer_access",
        ]
        if any(p in policy for p in positive_access_patterns):
            errors.append(
                "data_isolation_policy non può consentire accesso automatico ai dati clienti"
            )
        return errors

    def to_dict(self) -> dict:
        return {
            "federation_contract_id": self.federation_contract_id,
            "mother_instance_id": self.mother_instance_id,
            "child_instance_id": self.child_instance_id,
            "constitutional_relationship": self.constitutional_relationship,
            "reporting_frequency": self.reporting_frequency,
            "allowed_metrics": self.allowed_metrics,
            "knowledge_return_policy": self.knowledge_return_policy,
            "strategic_incident_policy": self.strategic_incident_policy,
            "update_proposal_policy": self.update_proposal_policy,
            "capability_update_policy": self.capability_update_policy,
            "capital_distribution_policy": self.capital_distribution_policy,
            "emergency_intervention_policy": self.emergency_intervention_policy,
            "termination_policy": self.termination_policy,
            "data_isolation_policy": self.data_isolation_policy,
            "communication_channels": self.communication_channels,
            "status": self.status,
            "created_at": self.created_at,
            "principles": self.PRINCIPLES,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MotherReplicaFederationContract":
        return cls(
            federation_contract_id=d["federation_contract_id"],
            mother_instance_id=d["mother_instance_id"],
            child_instance_id=d.get("child_instance_id"),
            constitutional_relationship=d.get("constitutional_relationship", "inherited"),
            reporting_frequency=d.get("reporting_frequency", "monthly"),
            allowed_metrics=d.get("allowed_metrics", []),
            knowledge_return_policy=d.get("knowledge_return_policy", "voluntary_shareable"),
            strategic_incident_policy=d.get("strategic_incident_policy", "escalation_required"),
            update_proposal_policy=d.get("update_proposal_policy", "versioned_opt_in"),
            capability_update_policy=d.get("capability_update_policy", "versioned_proposal"),
            capital_distribution_policy=d.get("capital_distribution_policy", "portfolio_reporting"),
            emergency_intervention_policy=d.get("emergency_intervention_policy", "escalation_required"),
            termination_policy=d.get("termination_policy", "mutual_agreement_or_governance"),
            data_isolation_policy=d.get("data_isolation_policy", "full_isolation_default"),
            communication_channels=d.get("communication_channels", ["audit_events"]),
            status=d.get("status", "draft"),
            created_at=d.get("created_at", ""),
        )


# ---------------------------------------------------------------------------
# Eccezioni
# ---------------------------------------------------------------------------

class GenesisRequestNotFoundError(LookupError):
    """Genesis request non trovata nel registry."""


class GenesisTransitionError(ValueError):
    """Transizione di stato non consentita dalla state machine."""


class GenesisVersionConflict(RuntimeError):
    """Conflitto di versione (optimistic locking)."""


class GenesisIdempotencyReplay(Exception):
    def __init__(self, genesis_request_id: str) -> None:
        self.genesis_request_id = genesis_request_id
        super().__init__(
            f"idempotency_key già usata per genesis_request_id={genesis_request_id!r}"
        )


class GeneticPackageSealed(RuntimeError):
    """Il Genetic Package è immutabile (status=sealed)."""


class ActivationBlockedError(RuntimeError):
    """L'activation è forbidden in V0."""

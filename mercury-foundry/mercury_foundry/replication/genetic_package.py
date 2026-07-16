"""Builder del Genetic Package — MF-REPL-001.

Costruisce il MercuryGeneticPackage da una DedicatedMercuryGenesisRequest.
Calcola il checksum SHA-256 sulla serializzazione deterministica (sort_keys=True).
Il package è immutabile dopo seal_genetic_package().

NON include:
  - path assoluti del filesystem del Main
  - credenziali o segreti
  - riferimenti a socket o processi locali
  - dati operativi clienti del Main
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from mercury_foundry.replication.models import (
    CapabilityBundleManifest,
    ConstitutionalPackage,
    DedicatedMercuryGenesisRequest,
    GovernancePackage,
    IntegrityManifest,
    KernelManifest,
    KnowledgePackageManifest,
    MercuryGeneticPackage,
    MissionGenealogy,
    ProductEvidencePackage,
    _new_id,
    _now_iso,
)

# Versione dello schema del package (per IntegrityManifest)
PACKAGE_SCHEMA_VERSION = "1.0.0"


def compute_checksum(data: dict) -> str:
    """Calcola SHA-256 sulla serializzazione JSON deterministica (sort_keys=True)."""
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_genetic_package(
    request: DedicatedMercuryGenesisRequest,
    *,
    source_instance_id: str = "mercury_main",
    package_version: str = "1.0.0",
    governance_organ_keys: list[str] | None = None,
    governance_mandates: list[dict] | None = None,
) -> MercuryGeneticPackage:
    """Costruisce un MercuryGeneticPackage da una DedicatedMercuryGenesisRequest.

    Valori di default minimi: il chiamante (GenesisService) è responsabile di
    passare i dati reali di governance e constitutional.
    """
    package_id = _new_id()
    now = _now_iso()

    constitutional_package = ConstitutionalPackage(
        constitution_version=request.constitutional_version,
        principle_ids=[
            "CONST-001", "CONST-002", "CONST-003",
            "CONST-004", "CONST-007",
        ],
        immutable_principles=["CONST-001", "CONST-002", "CONST-007"],
        inherited_enforcement_modes={
            "CONST-001": "shadow",
            "CONST-002": "shadow",
            "CONST-003": "shadow",
            "CONST-004": "shadow",
            "CONST-007": "shadow",
        },
        local_amendment_policy="require_review",
        upstream_update_policy="accept_proposals_only",
    )

    kernel_manifest = KernelManifest(
        kernel_version=request.kernel_version,
        required_modules=[
            "constitutional_core",
            "autonomy_boundary",
            "audit_log",
            "mission_layer",
        ],
        optional_modules=["expedition_runtime", "capability_engine"],
        compatibility_constraints={"min_python": "3.10"},
        migration_requirements=[],
        runtime_requirements={"sqlite": ">=3.35"},
    )

    governance_package = GovernancePackage(
        organ_definitions=governance_organ_keys or [],
        decision_mandates=governance_mandates or [],
        authority_modes={
            "default": "proposal",
            "constitutional_change": "forbidden",
            "activate": "forbidden",
        },
        budget_limits={
            "max_budget_eur": request.requested_budget_envelope,
        },
        risk_limits={"max_risk_level": "high"},
        escalation_rules=["constitutional_violations", "budget_exceeded", "high_risk_autonomous"],
        prohibited_actions=["direct_production_db_mutation", "genesis_activate"],
    )

    cap_bundle: CapabilityBundleManifest | None = None
    if request.requested_capability_bundle_ids:
        cap_bundle = CapabilityBundleManifest(
            bundle_ids=request.requested_capability_bundle_ids,
            capability_ids=[],
            versions={},
            portability_status={
                bid: "unknown"
                for bid in request.requested_capability_bundle_ids
            },
        )

    kn_manifest: KnowledgePackageManifest | None = None
    if request.requested_knowledge_package_ids:
        kn_manifest = KnowledgePackageManifest(
            package_ids=request.requested_knowledge_package_ids,
            knowledge_scope="shareable",
            source_refs=request.validation_evidence_refs,
        )

    genealogy = MissionGenealogy(
        source_mission_id=request.source_mission_id,
        parent_mission_ids=[request.source_mission_id],
        source_expedition_id=request.source_expedition_id,
        evidence_refs=request.validation_evidence_refs,
        assumptions=[],
        validation_history=[{
            "event": "genesis_requested",
            "reason": request.genesis_reason.value,
            "requested_by": request.requested_by,
            "at": request.requested_at,
        }],
        genesis_rationale=(
            f"Genesis richiesta da {request.requested_by!r} con motivo "
            f"{request.genesis_reason.value!r} per i prodotti {request.validated_product_ids}."
        ),
    )

    evidence = ProductEvidencePackage(
        product_ids=request.validated_product_ids,
        demand_evidence=request.validation_evidence_refs[:2] if len(request.validation_evidence_refs) >= 2 else request.validation_evidence_refs,
        value_evidence=request.validation_evidence_refs,
        feasibility_evidence=[],
        business_viability_evidence=[],
        customer_evidence=[],
        confidence_score=request.product_validation_score or 0.5,
        unresolved_assumptions=[],
        known_risks=[],
    )

    # Calcola i checksum di ogni componente
    component_checksums = {
        "constitutional_package": compute_checksum(constitutional_package.to_dict()),
        "kernel_manifest": compute_checksum(kernel_manifest.to_dict()),
        "governance_package": compute_checksum(governance_package.to_dict()),
        "mission_genealogy": compute_checksum(genealogy.to_dict()),
        "product_evidence_package": compute_checksum(evidence.to_dict()),
    }
    if cap_bundle:
        component_checksums["capability_bundle_manifest"] = compute_checksum(cap_bundle.to_dict())
    if kn_manifest:
        component_checksums["knowledge_package_manifest"] = compute_checksum(kn_manifest.to_dict())

    integrity = IntegrityManifest(
        package_checksum="",  # calcolato dopo
        component_checksums=component_checksums,
        schema_versions={
            "constitutional_package": PACKAGE_SCHEMA_VERSION,
            "kernel_manifest": PACKAGE_SCHEMA_VERSION,
            "governance_package": PACKAGE_SCHEMA_VERSION,
            "mission_genealogy": PACKAGE_SCHEMA_VERSION,
            "product_evidence_package": PACKAGE_SCHEMA_VERSION,
        },
        generated_at=now,
        validation_status="pending",
    )

    # Assembla il package (senza checksum finale)
    pkg = MercuryGeneticPackage(
        package_id=package_id,
        package_version=package_version,
        source_instance_id=source_instance_id,
        target_instance_id=None,
        constitutional_package=constitutional_package,
        kernel_manifest=kernel_manifest,
        governance_package=governance_package,
        capability_bundle_manifest=cap_bundle,
        knowledge_package_manifest=kn_manifest,
        mission_genealogy=genealogy,
        product_evidence_package=evidence,
        operational_policy_package={
            "data_isolation": "full_isolation_default",
            "customer_data_sharing": "prohibited_by_default",
        },
        audit_seed={
            "genesis_request_id": request.genesis_request_id,
            "source_mission_id": request.source_mission_id,
            "requested_by": request.requested_by,
            "requested_at": request.requested_at,
        },
        federation_contract={
            "type": "mother_replica_v0",
            "mother_instance_id": source_instance_id,
            "data_isolation_policy": "full_isolation_default",
            "reporting_frequency": "monthly",
        },
        reporting_contract={
            "allowed_metrics": [
                "portfolio_level_kpis",
                "constitutional_status",
                "budget_envelope_utilization",
            ],
            "prohibited_metrics": ["customer_pii", "customer_behavioral_data"],
        },
        economic_envelope={
            "approved_amount": request.requested_budget_envelope,
            "currency": "EUR",
        },
        integrity_manifest=integrity,
        generated_at=now,
        generated_by=request.requested_by,
        checksum="",   # placeholder
        status="draft",
        metadata=request.metadata,
    )

    # Calcola il checksum finale sull'intero package (escluso il campo checksum stesso)
    pkg_dict = pkg.to_dict()
    pkg_dict.pop("checksum", None)
    pkg_dict.pop("integrity_manifest", None)   # evita ricorsività
    final_checksum = compute_checksum(pkg_dict)

    integrity.package_checksum = final_checksum
    integrity.validation_status = "valid"
    pkg.checksum = final_checksum
    pkg.integrity_manifest = integrity

    return pkg


def validate_package_integrity(pkg: MercuryGeneticPackage) -> list[str]:
    """Verifica l'integrità del package. Ritorna lista di errori (vuota = OK)."""
    errors = []
    if not pkg.checksum:
        errors.append("checksum mancante")
    if not pkg.integrity_manifest.package_checksum:
        errors.append("integrity_manifest.package_checksum mancante")
    if pkg.integrity_manifest.validation_status not in ("valid", "pending"):
        errors.append(
            f"integrity_manifest.validation_status non valido: "
            f"{pkg.integrity_manifest.validation_status!r}"
        )
    if pkg.status == "invalid":
        errors.append("package in stato invalid")
    # Verifica che i componenti obbligatori siano presenti
    for comp in ("constitutional_package", "kernel_manifest", "mission_genealogy", "product_evidence_package"):
        if comp not in pkg.integrity_manifest.component_checksums:
            errors.append(f"component checksum mancante: {comp!r}")
    return errors

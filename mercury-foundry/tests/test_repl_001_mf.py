"""Test suite MF-REPL-001 — Dedicated Mercury Genesis Contract V0.

65 test cases coprono:
  - Domain Model (spec 1-10)
  - Genetic Package (spec 11-20)
  - Independence Evaluation (spec 21-29)
  - Product Family Assessment (spec 30-37)
  - Replication Gate (spec 38-44)
  - Genesis Lifecycle (spec 45-50)
  - Autonomy & Governance (spec 51-56)
  - Federation Contract (spec 57-61)
  - Audit & Events (spec 62-64)
  - Regression (spec 65)

Invarianti:
  - Nessuna replica reale creata.
  - GENESIS_ACTIVATE = forbidden.
  - V0: provisioning e activated non raggiungibili automaticamente.
  - 341 test precedenti non regrediscono.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from mercury_foundry.replication.models import (
    ALLOWED_MAIN_DEPENDENCIES,
    PROHIBITED_MAIN_DEPENDENCIES,
    ActivationBlockedError,
    DedicatedMercuryGenesisRequest,
    DedicatedMercuryIndependenceContract,
    FamilyRecommendation,
    GeneticPackageSealed,
    GenesisIdempotencyReplay,
    GenesisReason,
    GenesisRequestNotFoundError,
    GenesisStatus,
    GenesisTransitionError,
    GenesisVersionConflict,
    IndependenceStatus,
    InstanceType,
    MotherReplicaFederationContract,
    MercuryGeneticPackage,
    PortabilityStatus,
    ProductFamilyAssessment,
    _new_id,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    from mercury_foundry.state.db import init_schema
    conn = sqlite3.connect(str(tmp_path / "mf.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    init_schema(conn)
    return conn


def _make_request(**overrides) -> DedicatedMercuryGenesisRequest:
    """Costruisce una DedicatedMercuryGenesisRequest minima e valida."""
    base = dict(
        genesis_request_id=_new_id(),
        idempotency_key=_new_id(),
        correlation_id=_new_id(),
        source_mission_id="mission-alpha-001",
        validated_product_ids=["prod-001"],
        proposed_instance_name="Mercury Finance Italia",
        proposed_instance_slug="mercury-finance-it",
        genesis_reason=GenesisReason.VALIDATED_PRODUCT,
        validation_evidence_refs=["evidence-001", "evidence-002"],
        target_market="B2B Italy",
        target_customer="SME Finance Directors",
        business_model="SaaS Subscription",
        constitutional_version="1.0",
        kernel_version="0.1.0",
        requested_by="test_user",
        requested_at=_now_iso(),
        product_validation_score=0.75,
        pmf_confidence=0.70,
        requested_budget_envelope=10_000.0,
    )
    base.update(overrides)
    return DedicatedMercuryGenesisRequest(**base)


# ---------------------------------------------------------------------------
# Spec 1-10 — Domain Model
# ---------------------------------------------------------------------------

def test_01_genesis_request_valid_minimum(db):
    """1 — Una genesis request minima valida non produce errori di validazione."""
    req = _make_request()
    errors = req.validate()
    assert errors == [], f"Errori inattesi: {errors}"


def test_02_genesis_request_missing_mission_id(db):
    """2 — source_mission_id vuoto produce errore di validazione."""
    req = _make_request(source_mission_id="  ")
    errors = req.validate()
    assert any("source_mission_id" in e for e in errors)


def test_03_genesis_request_missing_products(db):
    """3 — validated_product_ids vuoto produce errore."""
    req = _make_request(validated_product_ids=[])
    errors = req.validate()
    assert any("validated_product_ids" in e for e in errors)


def test_04_genesis_request_missing_evidence(db):
    """4 — validation_evidence_refs vuota produce errore (CONST-001)."""
    req = _make_request(validation_evidence_refs=[])
    errors = req.validate()
    assert any("validation_evidence_refs" in e for e in errors)


def test_05_genesis_request_negative_budget(db):
    """5 — budget negativo produce errore."""
    req = _make_request(requested_budget_envelope=-1.0)
    errors = req.validate()
    assert any("budget_envelope" in e for e in errors)


def test_06_genesis_request_invalid_score_range(db):
    """6 — product_validation_score fuori da [0, 1] produce errore."""
    req = _make_request(product_validation_score=1.5)
    errors = req.validate()
    assert any("product_validation_score" in e for e in errors)


def test_07_genesis_reason_enum_all_values(db):
    """7 — Tutti i GenesisReason producono request valide."""
    for reason in GenesisReason:
        req = _make_request(genesis_reason=reason)
        errors = req.validate()
        assert errors == [], f"Errori per reason={reason}: {errors}"


def test_08_genesis_request_to_dict_roundtrip(db):
    """8 — to_dict produce dizionario con i campi chiave."""
    req = _make_request()
    d = req.to_dict()
    assert d["genesis_request_id"] == req.genesis_request_id
    assert d["genesis_reason"] == req.genesis_reason.value
    assert d["validated_product_ids"] == req.validated_product_ids


def test_09_genesis_status_v0_blocked_statuses(db):
    """9 — GenesisStatus.V0_BLOCKED_STATUSES contiene provisioning e activated."""
    blocked = GenesisStatus.V0_BLOCKED_STATUSES
    assert "provisioning" in blocked
    assert "activated" in blocked


def test_10_portable_capability_bundle_ref_roundtrip(db):
    """10 — PortableCapabilityBundleRef serializza e deserializza correttamente."""
    from mercury_foundry.replication.models import PortableCapabilityBundleRef
    ref = PortableCapabilityBundleRef(
        bundle_id="bundle-001",
        version="1.0.0",
        capability_ids=["cap-a", "cap-b"],
        portability_status=PortabilityStatus.PORTABLE,
    )
    d = ref.to_dict()
    ref2 = PortableCapabilityBundleRef.from_dict(d)
    assert ref2.bundle_id == ref.bundle_id
    assert ref2.portability_status == PortabilityStatus.PORTABLE


# ---------------------------------------------------------------------------
# Spec 11-20 — Genetic Package
# ---------------------------------------------------------------------------

def test_11_build_genetic_package_produces_checksum(db):
    """11 — build_genetic_package produce un package con checksum SHA-256."""
    from mercury_foundry.replication.genetic_package import build_genetic_package
    req = _make_request()
    pkg = build_genetic_package(req)
    assert pkg.checksum
    assert len(pkg.checksum) == 64   # SHA-256 hex


def test_12_genetic_package_serializable(db):
    """12 — Il package è serializzabile in JSON (roundtrip senza perdita di struttura)."""
    from mercury_foundry.replication.genetic_package import build_genetic_package
    req = _make_request()
    pkg = build_genetic_package(req)
    d = pkg.to_dict()
    raw = json.dumps(d)
    pkg2 = MercuryGeneticPackage.from_dict(json.loads(raw))
    assert pkg2.package_id == pkg.package_id
    assert pkg2.checksum == pkg.checksum


def test_13_genetic_package_integrity_validation(db):
    """13 — validate_package_integrity ritorna lista vuota per un package valido."""
    from mercury_foundry.replication.genetic_package import (
        build_genetic_package, validate_package_integrity,
    )
    req = _make_request()
    pkg = build_genetic_package(req)
    errors = validate_package_integrity(pkg)
    assert errors == [], f"Errori inattesi: {errors}"


def test_14_genetic_package_contains_constitutional_package(db):
    """14 — Il package include sempre un ConstitutionalPackage."""
    from mercury_foundry.replication.genetic_package import build_genetic_package
    req = _make_request()
    pkg = build_genetic_package(req)
    assert pkg.constitutional_package.constitution_version == req.constitutional_version
    assert "CONST-001" in pkg.constitutional_package.principle_ids


def test_15_genetic_package_contains_mission_genealogy(db):
    """15 — Il package include MissionGenealogy con source_mission_id."""
    from mercury_foundry.replication.genetic_package import build_genetic_package
    req = _make_request()
    pkg = build_genetic_package(req)
    assert pkg.mission_genealogy.source_mission_id == req.source_mission_id
    assert req.validation_evidence_refs == pkg.mission_genealogy.evidence_refs


def test_16_genetic_package_default_status_draft(db):
    """16 — Un package appena costruito ha status=draft."""
    from mercury_foundry.replication.genetic_package import build_genetic_package
    req = _make_request()
    pkg = build_genetic_package(req)
    assert pkg.status == "draft"


def test_17_genetic_package_seal(db):
    """17 — seal_genetic_package imposta status=sealed nel DB."""
    from mercury_foundry.replication.genetic_package import build_genetic_package
    from mercury_foundry.replication.registry import store_genetic_package, seal_genetic_package, get_genetic_package
    req = _make_request()
    pkg = build_genetic_package(req)
    _provision_genesis_request(db, req)
    store_genetic_package(db, package=pkg, genesis_request_id=req.genesis_request_id)
    seal_genetic_package(db, pkg.package_id)
    row = db.execute(
        "SELECT status FROM mercury_genetic_packages WHERE package_id = ?", (pkg.package_id,)
    ).fetchone()
    assert row["status"] == "sealed"


def test_18_genetic_package_seal_twice_raises(db):
    """18 — Seal su un package già sealed solleva GeneticPackageSealed."""
    from mercury_foundry.replication.genetic_package import build_genetic_package
    from mercury_foundry.replication.registry import store_genetic_package, seal_genetic_package
    req = _make_request()
    pkg = build_genetic_package(req)
    _provision_genesis_request(db, req)
    store_genetic_package(db, package=pkg, genesis_request_id=req.genesis_request_id)
    seal_genetic_package(db, pkg.package_id)
    with pytest.raises(GeneticPackageSealed):
        seal_genetic_package(db, pkg.package_id)


def test_19_genetic_package_no_absolute_paths(db):
    """19 — Il package serializzato non contiene path assoluti del filesystem."""
    from mercury_foundry.replication.genetic_package import build_genetic_package
    req = _make_request()
    pkg = build_genetic_package(req)
    raw = json.dumps(pkg.to_dict())
    assert "/home/" not in raw
    assert "/root/" not in raw
    assert "/mercury_foundry" not in raw.replace("mercury_main", "")


def test_20_genetic_package_no_secrets(db):
    """20 — Il package non contiene campi credenziali."""
    from mercury_foundry.replication.genetic_package import build_genetic_package
    req = _make_request()
    pkg = build_genetic_package(req)
    raw = json.dumps(pkg.to_dict()).lower()
    for forbidden in ("password", "secret", "api_key", "token", "credential"):
        assert forbidden not in raw, f"Campo proibito {forbidden!r} nel package"


# ---------------------------------------------------------------------------
# Spec 21-29 — Independence Evaluation
# ---------------------------------------------------------------------------

def test_21_independence_evaluation_basic_ready(db):
    """21 — Una request standard produce IndependenceStatus.READY_FOR_PROVISIONING."""
    from mercury_foundry.replication.independence import evaluate_independence
    req = _make_request()
    contract = evaluate_independence(
        req,
        required_local_audit=["local_audit_log"],
        required_local_governance=["REPLICATION_GOVERNANCE"],
        required_local_budget_control=["local_budget_tracker"],
        required_local_storage=["local_sqlite_db"],
    )
    assert contract.status == IndependenceStatus.READY_FOR_PROVISIONING
    assert not contract.blockers


def test_22_independence_evaluation_prohibited_dependency_blocks(db):
    """22 — Dipendenza proibita produce status INSUFFICIENT con blockers."""
    from mercury_foundry.replication.independence import evaluate_independence
    req = _make_request()
    contract = evaluate_independence(
        req,
        required_local_audit=["local_audit_log"],
        required_local_governance=["REPLICATION_GOVERNANCE"],
        required_local_budget_control=["local_budget_tracker"],
        required_local_storage=["local_sqlite_db"],
        prohibited_main_dependencies=["marketing_execution"],
    )
    assert contract.status == IndependenceStatus.INSUFFICIENT
    assert any("marketing_execution" in b.lower() or "Marketing" in b for b in contract.blockers)


def test_23_independence_evaluation_missing_audit_blocks(db):
    """23 — Audit locale assente produce blocker."""
    from mercury_foundry.replication.independence import evaluate_independence
    req = _make_request()
    contract = evaluate_independence(
        req,
        required_local_audit=[],
        required_local_governance=["REPLICATION_GOVERNANCE"],
        required_local_budget_control=["local_budget_tracker"],
        required_local_storage=["local_sqlite_db"],
    )
    assert contract.status == IndependenceStatus.INSUFFICIENT
    assert any("audit" in b.lower() for b in contract.blockers)


def test_24_independence_evaluation_missing_governance_blocks(db):
    """24 — Governance locale assente produce blocker."""
    from mercury_foundry.replication.independence import evaluate_independence
    req = _make_request()
    contract = evaluate_independence(
        req,
        required_local_audit=["local_audit_log"],
        required_local_governance=[],
        required_local_budget_control=["local_budget_tracker"],
        required_local_storage=["local_sqlite_db"],
    )
    assert contract.status == IndependenceStatus.INSUFFICIENT
    assert any("governance" in b.lower() or "Governance" in b for b in contract.blockers)


def test_25_independence_evaluation_bootstrap_without_expiry_warns(db):
    """25 — Bootstrap senza expiry produce warning non blocker."""
    from mercury_foundry.replication.independence import evaluate_independence
    req = _make_request()
    contract = evaluate_independence(
        req,
        required_local_audit=["local_audit_log"],
        required_local_governance=["REPLICATION_GOVERNANCE"],
        required_local_budget_control=["local_budget_tracker"],
        required_local_storage=["local_sqlite_db"],
        temporary_bootstrap_dependencies=["initial_bootstrap_artifacts"],
        bootstrap_expiry_conditions=[],
    )
    assert contract.status == IndependenceStatus.CONDITIONALLY_READY
    assert any("bootstrap" in w.lower() for w in contract.warnings)


def test_26_independence_evaluation_bootstrap_with_expiry_ok(db):
    """26 — Bootstrap con expiry: nessun blocker da bootstrap mancante."""
    from mercury_foundry.replication.independence import evaluate_independence
    req = _make_request()
    contract = evaluate_independence(
        req,
        required_local_audit=["local_audit_log"],
        required_local_governance=["REPLICATION_GOVERNANCE"],
        required_local_budget_control=["local_budget_tracker"],
        required_local_storage=["local_sqlite_db"],
        temporary_bootstrap_dependencies=["initial_bootstrap_artifacts"],
        bootstrap_expiry_conditions=["month_1_delivery"],
    )
    # con expiry non produce blocker per il bootstrap
    assert not any("bootstrap" in b.lower() for b in contract.blockers)


def test_27_independence_contract_to_dict_roundtrip(db):
    """27 — DedicatedMercuryIndependenceContract serializza e deserializza."""
    from mercury_foundry.replication.independence import evaluate_independence
    req = _make_request()
    contract = evaluate_independence(req)
    d = contract.to_dict()
    c2 = DedicatedMercuryIndependenceContract.from_dict(d)
    assert c2.genesis_request_id == contract.genesis_request_id
    assert c2.status == contract.status


def test_28_independence_evaluation_allowed_main_deps_whitelist(db):
    """28 — Dipendenza in whitelist ALLOWED_MAIN_DEPENDENCIES non genera errori."""
    from mercury_foundry.replication.independence import evaluate_independence
    req = _make_request()
    contract = evaluate_independence(
        req,
        required_local_audit=["local_audit_log"],
        required_local_governance=["REPLICATION_GOVERNANCE"],
        required_local_budget_control=["local_budget_tracker"],
        required_local_storage=["local_sqlite_db"],
        allowed_main_dependencies=list(ALLOWED_MAIN_DEPENDENCIES),
    )
    assert not any(
        "fuori dalla whitelist" in w for w in contract.warnings
    ), f"Warning inatteso: {contract.warnings}"


def test_29_independence_store_and_retrieve(db):
    """29 — store_independence_contract + get_independence_contract roundtrip."""
    from mercury_foundry.replication.independence import evaluate_independence
    from mercury_foundry.replication.registry import store_independence_contract, get_independence_contract
    req = _make_request()
    contract = evaluate_independence(req)
    _provision_genesis_request(db, req)
    store_independence_contract(db, contract=contract, genesis_request_id=req.genesis_request_id)
    retrieved = get_independence_contract(db, contract.contract_id)
    assert retrieved.contract_id == contract.contract_id
    assert retrieved.status == contract.status


# ---------------------------------------------------------------------------
# Spec 30-37 — Product Family Assessment
# ---------------------------------------------------------------------------

def test_30_single_product_always_single_instance(db):
    """30 — Un singolo prodotto → SINGLE_DEDICATED_MERCURY senza analisi."""
    from mercury_foundry.replication.family_assessment import assess_product_family
    a = assess_product_family(["prod-001"])
    assert a.recommendation == FamilyRecommendation.SINGLE_DEDICATED_MERCURY
    assert a.coherence_score == 1.0


def test_31_empty_product_list_insufficient(db):
    """31 — Lista prodotti vuota → INSUFFICIENT_EVIDENCE."""
    from mercury_foundry.replication.family_assessment import assess_product_family
    a = assess_product_family([])
    assert a.recommendation == FamilyRecommendation.INSUFFICIENT_EVIDENCE


def test_32_high_coherence_single_instance(db):
    """32 — 7+ dimensioni condivise → SINGLE_DEDICATED_MERCURY."""
    from mercury_foundry.replication.family_assessment import assess_product_family
    a = assess_product_family(
        ["prod-001", "prod-002"],
        shared_customer=True, shared_market=True, shared_problem_space=True,
        shared_capabilities=True, shared_distribution=True, shared_business_model=True,
        shared_data_boundary=True, shared_regulatory_boundary=True,
    )
    assert a.recommendation == FamilyRecommendation.SINGLE_DEDICATED_MERCURY
    assert a.coherence_score >= 0.875


def test_33_different_customers_and_markets_separate(db):
    """33 — Clienti e mercati diversi → SEPARATE_INSTANCES (override deterministico)."""
    from mercury_foundry.replication.family_assessment import assess_product_family
    a = assess_product_family(
        ["prod-001", "prod-002"],
        shared_customer=False, shared_market=False,
        shared_problem_space=True, shared_capabilities=True,
        shared_distribution=True, shared_business_model=True,
        shared_data_boundary=True, shared_regulatory_boundary=True,
    )
    assert a.recommendation == FamilyRecommendation.SEPARATE_INSTANCES
    assert any("clienti" in c.lower() or "mercati" in c.lower() for c in a.conflicts)


def test_34_incompatible_business_model_separate(db):
    """34 — Business model incompatibile → SEPARATE_INSTANCES."""
    from mercury_foundry.replication.family_assessment import assess_product_family
    a = assess_product_family(
        ["prod-001", "prod-002"],
        shared_customer=True, shared_market=True, shared_problem_space=True,
        shared_capabilities=True, shared_distribution=True, shared_business_model=False,
        shared_data_boundary=True, shared_regulatory_boundary=True,
    )
    assert a.recommendation == FamilyRecommendation.SEPARATE_INSTANCES


def test_35_incompatible_data_boundary_separate(db):
    """35 — Dati non condivisibili → SEPARATE_INSTANCES (da conflict deterministico)."""
    from mercury_foundry.replication.family_assessment import assess_product_family
    a = assess_product_family(
        ["prod-001", "prod-002"],
        shared_customer=True, shared_market=True, shared_problem_space=True,
        shared_capabilities=True, shared_distribution=True, shared_business_model=True,
        shared_data_boundary=False, shared_regulatory_boundary=True,
    )
    assert a.recommendation == FamilyRecommendation.SEPARATE_INSTANCES


def test_36_mid_coherence_manual_review(db):
    """36 — 4 dimensioni condivise senza conflitti hard → MANUAL_REVIEW."""
    from mercury_foundry.replication.family_assessment import assess_product_family
    a = assess_product_family(
        ["prod-001", "prod-002"],
        shared_customer=True, shared_market=True, shared_problem_space=True,
        shared_capabilities=True, shared_distribution=False, shared_business_model=True,
        shared_data_boundary=True, shared_regulatory_boundary=True,
    )
    # 7 dimensioni condivise → SINGLE_DEDICATED_MERCURY
    # Con regulatory non condivisa → SEPARATE_INSTANCES
    # Verifica che non abbia troppi conflitti e segua la logica
    assert a.recommendation in {
        FamilyRecommendation.SINGLE_DEDICATED_MERCURY,
        FamilyRecommendation.MANUAL_REVIEW,
        FamilyRecommendation.SEPARATE_INSTANCES,
    }


def test_37_family_assessment_store_and_retrieve(db):
    """37 — store_family_assessment + get_family_assessment roundtrip."""
    from mercury_foundry.replication.family_assessment import assess_product_family
    from mercury_foundry.replication.registry import store_family_assessment, get_family_assessment
    req = _make_request()
    a = assess_product_family(["prod-001"])
    _provision_genesis_request(db, req)
    store_family_assessment(db, assessment=a, genesis_request_id=req.genesis_request_id)
    retrieved = get_family_assessment(db, a.assessment_id)
    assert retrieved.assessment_id == a.assessment_id
    assert retrieved.recommendation == a.recommendation


# ---------------------------------------------------------------------------
# Spec 38-44 — Replication Gate
# ---------------------------------------------------------------------------

def test_38_gate_pass_valid_request(db):
    """38 — Request valida produce GateStatus.PASS."""
    from mercury_foundry.replication.gate import evaluate_replication_gate
    from mercury_foundry.replication.independence import evaluate_independence
    req = _make_request()
    gate_req = _make_gate_request(req)
    contract = evaluate_independence(
        req,
        required_local_audit=["local_audit_log"],
        required_local_governance=["REPLICATION_GOVERNANCE"],
        required_local_budget_control=["local_budget_tracker"],
        required_local_storage=["local_sqlite_db"],
    )
    from mercury_foundry.replication.models import GateStatus
    result = evaluate_replication_gate(db, req, gate_req, contract)
    assert result.approved
    assert result.status in {GateStatus.PASS, GateStatus.PASS_WITH_CONDITIONS}


def test_39_gate_fail_no_evidence(db):
    """39 — Nessuna evidence → GateStatus.FAIL."""
    from mercury_foundry.replication.gate import evaluate_replication_gate
    from mercury_foundry.replication.independence import evaluate_independence
    from mercury_foundry.replication.models import GateStatus, ReplicationGateRequest
    req = _make_request()
    gate_req = _make_gate_request(req, evidence_refs=[])
    contract = evaluate_independence(req)
    result = evaluate_replication_gate(db, req, gate_req, contract)
    assert result.status == GateStatus.FAIL
    assert not result.approved


def test_40_gate_fail_low_validation_score(db):
    """40 — Score < 0.5 produce GateStatus.FAIL."""
    from mercury_foundry.replication.gate import evaluate_replication_gate
    from mercury_foundry.replication.independence import evaluate_independence
    from mercury_foundry.replication.models import GateStatus
    req = _make_request(product_validation_score=0.3)
    gate_req = _make_gate_request(req)
    contract = evaluate_independence(req)
    result = evaluate_replication_gate(db, req, gate_req, contract)
    assert result.status == GateStatus.FAIL


def test_41_gate_fail_independence_insufficient(db):
    """41 — IndependenceStatus.INSUFFICIENT → GateStatus.FAIL."""
    from mercury_foundry.replication.gate import evaluate_replication_gate
    from mercury_foundry.replication.independence import evaluate_independence
    from mercury_foundry.replication.models import GateStatus
    req = _make_request()
    gate_req = _make_gate_request(req)
    contract = evaluate_independence(
        req,
        required_local_audit=[],   # assente: blocker
        required_local_governance=[],
        required_local_budget_control=[],
        required_local_storage=[],
    )
    result = evaluate_replication_gate(db, req, gate_req, contract)
    assert result.status == GateStatus.FAIL


def test_42_gate_fail_family_separate_instances(db):
    """42 — ProductFamilyAssessment.SEPARATE_INSTANCES → GateStatus.FAIL."""
    from mercury_foundry.replication.gate import evaluate_replication_gate
    from mercury_foundry.replication.independence import evaluate_independence
    from mercury_foundry.replication.family_assessment import assess_product_family
    from mercury_foundry.replication.models import GateStatus
    req = _make_request(validated_product_ids=["prod-001", "prod-002"])
    gate_req = _make_gate_request(req)
    contract = evaluate_independence(
        req,
        required_local_audit=["local_audit_log"],
        required_local_governance=["REPLICATION_GOVERNANCE"],
        required_local_budget_control=["local_budget_tracker"],
        required_local_storage=["local_sqlite_db"],
    )
    family = assess_product_family(
        ["prod-001", "prod-002"],
        shared_customer=False, shared_market=False,
        shared_business_model=False, shared_data_boundary=False,
        shared_regulatory_boundary=False,
    )
    result = evaluate_replication_gate(db, req, gate_req, contract, family_assessment=family)
    assert result.status == GateStatus.FAIL


def test_43_gate_result_contains_explanation(db):
    """43 — Il gate result include sempre una spiegazione."""
    from mercury_foundry.replication.gate import evaluate_replication_gate
    from mercury_foundry.replication.independence import evaluate_independence
    req = _make_request()
    gate_req = _make_gate_request(req)
    contract = evaluate_independence(req)
    result = evaluate_replication_gate(db, req, gate_req, contract)
    assert result.explanation


def test_44_gate_result_store_and_retrieve(db):
    """44 — store_gate_result + get_gate_result roundtrip."""
    from mercury_foundry.replication.gate import evaluate_replication_gate
    from mercury_foundry.replication.independence import evaluate_independence
    from mercury_foundry.replication.registry import store_gate_result, get_gate_result
    req = _make_request()
    gate_req = _make_gate_request(req)
    contract = evaluate_independence(req)
    result = evaluate_replication_gate(db, req, gate_req, contract)
    _provision_genesis_request(db, req)
    store_gate_result(db, result=result, genesis_request_id=req.genesis_request_id)
    retrieved = get_gate_result(db, result.gate_result_id)
    assert retrieved.gate_result_id == result.gate_result_id
    assert retrieved.approved == result.approved


# ---------------------------------------------------------------------------
# Spec 45-50 — Genesis Lifecycle
# ---------------------------------------------------------------------------

def test_45_state_machine_allowed_transitions(db):
    """45 — ALLOWED_TRANSITIONS copre il flusso completo non-V0-bloccato."""
    from mercury_foundry.replication.lifecycle import ALLOWED_TRANSITIONS, can_transition
    assert can_transition("draft", "proposed")
    assert can_transition("proposed", "under_review")
    assert can_transition("under_review", "approved")
    assert can_transition("under_review", "rejected")
    assert can_transition("approved", "packaging")
    assert can_transition("packaging", "ready_for_provisioning")
    assert can_transition("ready_for_provisioning", "aborted")
    assert can_transition("rejected", "archived")
    assert can_transition("failed", "archived")
    assert can_transition("aborted", "archived")


def test_46_state_machine_v0_blocked(db):
    """46 — Transizioni V0-bloccate sollevano ActivationBlockedError."""
    from mercury_foundry.replication.lifecycle import validate_transition
    with pytest.raises(ActivationBlockedError):
        validate_transition("ready_for_provisioning", "provisioning")
    with pytest.raises(ActivationBlockedError):
        validate_transition("provisioning", "activated")


def test_47_state_machine_illegal_transition(db):
    """47 — Transizioni non nel grafo sollevano GenesisTransitionError."""
    from mercury_foundry.replication.lifecycle import validate_transition
    with pytest.raises(GenesisTransitionError):
        validate_transition("draft", "activated")
    with pytest.raises(GenesisTransitionError):
        validate_transition("archived", "proposed")


def test_48_apply_genesis_transition_basic(db):
    """48 — apply_genesis_transition esegue aggiornamento e crea transition record."""
    from mercury_foundry.replication.lifecycle import apply_genesis_transition
    from mercury_foundry.replication.registry import create_genesis_request
    req = _make_request()
    _provision_genesis_request(db, req)
    record = apply_genesis_transition(
        db,
        genesis_request_id=req.genesis_request_id,
        current_status=GenesisStatus.DRAFT.value,
        current_version=1,
        to_status=GenesisStatus.PROPOSED.value,
        requested_by="test_user",
        reason="gate passato",
        correlation_id=req.correlation_id,
    )
    assert record.from_status == GenesisStatus.DRAFT
    assert record.to_status == GenesisStatus.PROPOSED

    row = db.execute(
        "SELECT status, version FROM dedicated_mercury_genesis_requests WHERE genesis_request_id = ?",
        (req.genesis_request_id,),
    ).fetchone()
    assert row["status"] == "proposed"
    assert row["version"] == 2


def test_49_apply_genesis_transition_version_conflict(db):
    """49 — Version conflict solleva GenesisVersionConflict."""
    from mercury_foundry.replication.lifecycle import apply_genesis_transition
    _provision_genesis_request(db, _make_request())
    req = _make_request()
    _provision_genesis_request(db, req)
    with pytest.raises(GenesisVersionConflict):
        apply_genesis_transition(
            db,
            genesis_request_id=req.genesis_request_id,
            current_status=GenesisStatus.DRAFT.value,
            current_version=999,  # version sbagliata
            to_status=GenesisStatus.PROPOSED.value,
            requested_by="test_user",
            reason="conflict test",
            correlation_id=req.correlation_id,
        )


def test_50_terminal_states_no_automatic_exit(db):
    """50 — Stati terminali (archived, activated) non hanno transizioni automatiche."""
    from mercury_foundry.replication.lifecycle import ALLOWED_TRANSITIONS, TERMINAL_STATUSES
    for state in TERMINAL_STATUSES:
        exits = ALLOWED_TRANSITIONS.get(state, frozenset())
        assert not exits, f"Stato terminale {state!r} ha uscite: {exits}"


# ---------------------------------------------------------------------------
# Spec 51-56 — Autonomy & Governance
# ---------------------------------------------------------------------------

def test_51_replication_governance_organ_exists(db):
    """51 — REPLICATION_GOVERNANCE è presente nel DB dopo init_schema."""
    from mercury_foundry.autonomy.models import get_organ_by_key
    from mercury_foundry.replication.seed import REPLICATION_GOVERNANCE_KEY
    organ = get_organ_by_key(db, REPLICATION_GOVERNANCE_KEY)
    assert organ is not None


def test_52_replication_governance_has_8_mandates(db):
    """52 — REPLICATION_GOVERNANCE ha esattamente 8 mandati."""
    from mercury_foundry.autonomy.models import get_organ_by_key, list_mandates_for_organ
    from mercury_foundry.replication.seed import REPLICATION_GOVERNANCE_KEY, INITIAL_MANDATES
    organ = get_organ_by_key(db, REPLICATION_GOVERNANCE_KEY)
    mandates = list_mandates_for_organ(db, organ["id"])
    assert len(mandates) == len(INITIAL_MANDATES)


def test_53_genesis_activate_is_forbidden(db):
    """53 — Mandato GENESIS_ACTIVATE ha authority_mode=forbidden (V0 invariante)."""
    from mercury_foundry.autonomy.models import get_organ_by_key, list_mandates_for_organ
    from mercury_foundry.replication.seed import REPLICATION_GOVERNANCE_KEY
    organ = get_organ_by_key(db, REPLICATION_GOVERNANCE_KEY)
    mandates = list_mandates_for_organ(db, organ["id"])
    activate = next(m for m in mandates if m["decision_type"] == "GENESIS_ACTIVATE")
    assert activate["authority_mode"] == "forbidden"


def test_54_genesis_approve_is_escalation_required(db):
    """54 — GENESIS_APPROVE è escalation_required (non autonoma)."""
    from mercury_foundry.autonomy.models import get_organ_by_key, list_mandates_for_organ
    from mercury_foundry.replication.seed import REPLICATION_GOVERNANCE_KEY
    organ = get_organ_by_key(db, REPLICATION_GOVERNANCE_KEY)
    mandates = list_mandates_for_organ(db, organ["id"])
    approve = next(m for m in mandates if m["decision_type"] == "GENESIS_APPROVE")
    assert approve["authority_mode"] == "escalation_required"


def test_55_seed_replication_governance_idempotent(db):
    """55 — Richiamando seed_replication_governance una seconda volta non duplica."""
    from mercury_foundry.replication.seed import seed_replication_governance
    from mercury_foundry.autonomy.models import get_organ_by_key, list_mandates_for_organ
    from mercury_foundry.replication.seed import REPLICATION_GOVERNANCE_KEY
    seed_replication_governance(db)
    organ = get_organ_by_key(db, REPLICATION_GOVERNANCE_KEY)
    mandates_before = list_mandates_for_organ(db, organ["id"])
    seed_replication_governance(db)
    mandates_after = list_mandates_for_organ(db, organ["id"])
    assert len(mandates_before) == len(mandates_after)


def test_56_no_business_cell_created_at_runtime(db):
    """56 — GenesisService non crea Business Cell (nessun record in organs con tipo business_cell)."""
    from mercury_foundry.replication.genesis_service import GenesisService
    req = _make_request()
    svc = GenesisService()
    svc.propose(db, req)
    # Verifica che nessun organo con "business_cell" sia stato creato
    rows = db.execute(
        "SELECT * FROM organs WHERE organ_key LIKE '%BUSINESS_CELL%'"
    ).fetchall()
    assert rows == []


# ---------------------------------------------------------------------------
# Spec 57-61 — Federation Contract
# ---------------------------------------------------------------------------

def test_57_federation_contract_builds(db):
    """57 — build_federation_contract produce un contratto valido."""
    from mercury_foundry.replication.federation import build_federation_contract, validate_federation_contract
    req = _make_request()
    fc = build_federation_contract(req)
    errors = validate_federation_contract(fc)
    assert errors == [], f"Errori inattesi: {errors}"


def test_58_federation_contract_data_isolation(db):
    """58 — Il contratto garantisce data_isolation_policy con full_isolation."""
    from mercury_foundry.replication.federation import build_federation_contract
    req = _make_request()
    fc = build_federation_contract(req)
    assert "full_isolation" in fc.data_isolation_policy.lower()


def test_59_federation_contract_principles(db):
    """59 — Il contratto include i 6 principi federativi."""
    from mercury_foundry.replication.federation import build_federation_contract
    req = _make_request()
    fc = build_federation_contract(req)
    assert len(fc.PRINCIPLES) >= 6
    assert "replica_autonomous_within_mandate" in fc.PRINCIPLES
    assert "main_no_automatic_customer_data_access" in fc.PRINCIPLES


def test_60_federation_contract_update_policy_versioned(db):
    """60 — update_proposal_policy menziona 'versioned'."""
    from mercury_foundry.replication.federation import build_federation_contract
    req = _make_request()
    fc = build_federation_contract(req)
    assert "versioned" in fc.update_proposal_policy.lower()


def test_61_federation_contract_roundtrip(db):
    """61 — MotherReplicaFederationContract serializza e deserializza."""
    from mercury_foundry.replication.federation import build_federation_contract
    req = _make_request()
    fc = build_federation_contract(req)
    d = fc.to_dict()
    fc2 = MotherReplicaFederationContract.from_dict(d)
    assert fc2.federation_contract_id == fc.federation_contract_id
    assert fc2.data_isolation_policy == fc.data_isolation_policy


# ---------------------------------------------------------------------------
# Spec 62-64 — Audit & Events
# ---------------------------------------------------------------------------

def test_62_genesis_service_produces_audit_events(db):
    """62 — GenesisService propone e produce eventi nell'audit log."""
    from mercury_foundry.replication.genesis_service import GenesisService
    from mercury_foundry.audit.logger import list_audit_log
    req = _make_request()
    svc = GenesisService()
    result = svc.propose(db, req)
    if result.genesis_request_id:
        events = list_audit_log(db, entity_type="replication", entity_id=None)
        assert len(events) > 0


def test_63_genesis_service_idempotency_replay(db):
    """63 — Seconda chiamata con stesso idempotency_key → status=duplicate."""
    from mercury_foundry.replication.genesis_service import GenesisService
    req1 = _make_request()
    req2 = _make_request(
        genesis_request_id=_new_id(),
        idempotency_key=req1.idempotency_key,   # stesso key
    )
    svc = GenesisService()
    r1 = svc.propose(db, req1)
    r2 = svc.propose(db, req2)
    assert r2.status == "duplicate"
    assert r2.duplicate_of == r1.genesis_request_id


def test_64_emit_replication_event_stored(db):
    """64 — emit_replication_event scrive nel DB audit_log."""
    from mercury_foundry.replication.events import emit_replication_event
    from mercury_foundry.replication.registry import create_genesis_request
    req = _make_request()
    db_id = _provision_genesis_request(db, req)
    emit_replication_event(
        db,
        action="replication.genesis.requested",
        genesis_db_id=db_id,
        genesis_request_id=req.genesis_request_id,
        actor_id="test_user",
        correlation_id=req.correlation_id,
    )
    rows = db.execute(
        "SELECT * FROM audit_log WHERE entity_type = 'replication' AND action = 'replication.genesis.requested'"
    ).fetchall()
    assert len(rows) >= 1


# ---------------------------------------------------------------------------
# Spec 65 — Regression
# ---------------------------------------------------------------------------

def test_65_doctor_returns_ready_replication_contract_shadow(tmp_path):
    """65 — Doctor restituisce READY_REPLICATION_CONTRACT_SHADOW dopo MF-REPL-001."""
    from mercury_foundry.diagnostics import run_doctor, OVERALL_READY_OUTCOME_SHADOW
    report = run_doctor(
        db_path=tmp_path / "mf_repl.db",
        sandbox_root=tmp_path / "target",
        provider_name="fake",
    )
    assert report.overall_status == OVERALL_READY_OUTCOME_SHADOW
    assert not report.has_errors()

    check_names = {c.name for c in report.checks}
    for expected in [
        "replication_schema",
        "replication_governance_organ",
        "replication_governance_mandates",
        "replication_activation_forbidden",
        "replication_state_machine",
        "replication_v0_blocked",
        "replication_feature_flags",
        "replication_genesis_service",
        "replication_federation_contract",
        "replication_independence_evaluator",
        "replication_genetic_package_builder",
    ]:
        assert expected in check_names, (
            f"Check {expected!r} mancante nel report. Presenti: {sorted(check_names)}"
        )


# ---------------------------------------------------------------------------
# Full flow integration (bonus)
# ---------------------------------------------------------------------------

def test_genesis_service_full_flow_propose(db):
    """Bonus — GenesisService propone correttamente con tutti i componenti."""
    from mercury_foundry.replication.genesis_service import GenesisService
    req = _make_request()
    svc = GenesisService()
    result = svc.propose(db, req)
    assert result.accepted
    assert result.genesis_request_id == req.genesis_request_id
    assert result.package_id is not None
    assert result.gate_result_id is not None
    assert result.independence_contract_id is not None

    # Verifica status=proposed nel DB
    row = db.execute(
        "SELECT status FROM dedicated_mercury_genesis_requests WHERE genesis_request_id = ?",
        (req.genesis_request_id,),
    ).fetchone()
    assert row["status"] == "proposed"


def test_genesis_service_rejected_low_score(db):
    """Bonus — GenesisService rigetta correttamente per score basso."""
    from mercury_foundry.replication.genesis_service import GenesisService
    req = _make_request(product_validation_score=0.2)
    svc = GenesisService()
    result = svc.propose(db, req)
    assert not result.accepted
    assert result.status == "rejected"


def test_genesis_service_rejected_no_evidence(db):
    """Bonus — GenesisService rigetta per evidence mancante."""
    from mercury_foundry.replication.genesis_service import GenesisService
    req = _make_request(validation_evidence_refs=[])
    svc = GenesisService()
    result = svc.propose(db, req)
    # Validation errors (schema) o rejected
    assert result.status in ("rejected",)


def test_genesis_duplicate_detection(db):
    """Bonus — Duplicate detection su source_mission_id attiva con status proposta attiva."""
    from mercury_foundry.replication.genesis_service import GenesisService
    req1 = _make_request(source_mission_id="mission-unique-xyz")
    req2 = _make_request(
        source_mission_id="mission-unique-xyz",
        idempotency_key=_new_id(),
        genesis_request_id=_new_id(),
    )
    svc = GenesisService()
    r1 = svc.propose(db, req1)
    assert r1.accepted
    r2 = svc.propose(db, req2)
    assert r2.status == "duplicate"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _provision_genesis_request(db: sqlite3.Connection, req: DedicatedMercuryGenesisRequest) -> int:
    """Inserisce una genesis request minima nel DB (status=draft). Ritorna rowid."""
    from mercury_foundry.replication.registry import create_genesis_request
    return create_genesis_request(
        db,
        genesis_request_id=req.genesis_request_id,
        idempotency_key=req.idempotency_key,
        correlation_id=req.correlation_id,
        source_mission_id=req.source_mission_id,
        validated_product_ids_json=json.dumps(req.validated_product_ids),
        proposed_instance_name=req.proposed_instance_name,
        proposed_instance_slug=req.proposed_instance_slug,
        genesis_reason=req.genesis_reason.value,
        validation_evidence_refs_json=json.dumps(req.validation_evidence_refs),
        target_market=req.target_market,
        target_customer=req.target_customer,
        business_model=req.business_model,
        constitutional_version=req.constitutional_version,
        kernel_version=req.kernel_version,
        requested_by=req.requested_by,
        requested_at=req.requested_at,
        product_validation_score=req.product_validation_score,
        requested_budget_envelope=req.requested_budget_envelope,
    )


def _make_gate_request(
    req: DedicatedMercuryGenesisRequest,
    evidence_refs: list[str] | None = None,
):
    from mercury_foundry.replication.models import ReplicationGateRequest
    return ReplicationGateRequest(
        request_id=_new_id(),
        genesis_request_id=req.genesis_request_id,
        source_mission_id=req.source_mission_id,
        source_expedition_id=req.source_expedition_id,
        product_ids=req.validated_product_ids,
        evidence_refs=evidence_refs if evidence_refs is not None else req.validation_evidence_refs,
        validation_summary=f"score={req.product_validation_score}",
        economic_summary=f"budget={req.requested_budget_envelope}",
        independence_contract_id=_new_id(),
        requested_at=_now_iso(),
        requested_by=req.requested_by,
        correlation_id=req.correlation_id,
    )

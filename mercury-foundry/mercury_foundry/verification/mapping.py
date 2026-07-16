"""Mappa dichiarativa source-pattern → test files / domain / livello minimo.

MF-VERIFY-001: questa mappa può essere aggiornata senza modificare il runner.
Aggiungere, rimuovere o modificare voci qui non richiede cambi al codice di selezione.

Struttura:
    SOURCE_MAPPINGS: list[SourceMapping]
        Ogni voce mappa un pattern (stringa parziale o glob-like) a:
        - domains: liste di domini del sistema
        - test_files: file di test associati
        - minimum_level: livello di verifica minimo
        - risk_class: classe di rischio
        - requires_full_at_milestone: se True, LEVEL 3 è obbligatorio prima del commit finale

    ALWAYS_RUN: list[str]
        Test sempre inclusi indipendentemente dal livello (smoke / invarianti critici).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from mercury_foundry.verification.models import RiskClass, VerificationLevel


# ---------------------------------------------------------------------------
# SourceMapping
# ---------------------------------------------------------------------------

@dataclass
class SourceMapping:
    """Associazione dichiarativa tra pattern di file sorgente e test."""
    pattern: str                          # sottostringa del path (case-insensitive)
    domains: list[str]                    # etichette di dominio
    test_files: list[str]                 # percorsi relativi dei file di test
    minimum_level: VerificationLevel      # livello minimo di verifica
    risk_class: RiskClass                 # classe di rischio
    requires_full_at_milestone: bool = False  # LEVEL 3 prima del commit finale


# ---------------------------------------------------------------------------
# Mappa dichiarativa
# ---------------------------------------------------------------------------

SOURCE_MAPPINGS: list[SourceMapping] = [

    # --- Documentazione e messaggi ---
    SourceMapping(
        pattern              = "docs/",
        domains              = ["documentation"],
        test_files           = [],
        minimum_level        = VerificationLevel.STATIC,
        risk_class           = RiskClass.LOW,
    ),
    SourceMapping(
        pattern              = ".md",
        domains              = ["documentation"],
        test_files           = [],
        minimum_level        = VerificationLevel.STATIC,
        risk_class           = RiskClass.LOW,
    ),
    SourceMapping(
        pattern              = "reports/",
        domains              = ["documentation"],
        test_files           = [],
        minimum_level        = VerificationLevel.STATIC,
        risk_class           = RiskClass.LOW,
    ),
    SourceMapping(
        pattern              = ".txt",
        domains              = ["documentation"],
        test_files           = [],
        minimum_level        = VerificationLevel.STATIC,
        risk_class           = RiskClass.LOW,
    ),

    # --- Verifica / MF-VERIFY-001 ---
    SourceMapping(
        pattern              = "mercury_foundry/verification/",
        domains              = ["verification"],
        test_files           = ["tests/test_verify_001_mf.py"],
        minimum_level        = VerificationLevel.TARGETED,
        risk_class           = RiskClass.MEDIUM,
    ),

    # --- Configurazione ---
    SourceMapping(
        pattern              = "mercury_foundry/config.py",
        domains              = ["config"],
        test_files           = [
            "tests/test_doctor.py",
            "tests/test_arch_008_autonomy.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.HIGH,
    ),

    # --- Schema DB e migrazioni ---
    SourceMapping(
        pattern              = "state/schema.sql",
        domains              = ["database", "migration", "persistence"],
        test_files           = [
            "tests/test_doctor.py",
            "tests/test_outcome_001_mf.py",
            "tests/test_eco_001_mf.py",
            "tests/test_mission_001_mf.py",
            "tests/test_repl_001_mf.py",
            "tests/test_arch_008_autonomy.py",
            "tests/test_const_001_constitutional_core.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.HIGH,
        requires_full_at_milestone = True,
    ),
    SourceMapping(
        pattern              = "state/db.py",
        domains              = ["database", "migration"],
        test_files           = [
            "tests/test_doctor.py",
            "tests/test_outcome_001_mf.py",
            "tests/test_eco_001_mf.py",
            "tests/test_mission_001_mf.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.HIGH,
        requires_full_at_milestone = True,
    ),

    # --- Outcome Governance ---
    SourceMapping(
        pattern              = "mercury_foundry/outcome/",
        domains              = ["outcome", "economic"],
        test_files           = [
            "tests/test_outcome_001_mf.py",
            "tests/test_eco_001_mf.py",
            "tests/test_mission_001_mf.py",
            "tests/test_arch_008_autonomy.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.HIGH,
    ),

    # --- Resource Allocator (specifico) ---
    SourceMapping(
        pattern              = "outcome/allocator",
        domains              = ["resource", "budget", "idempotency"],
        test_files           = [
            "tests/test_outcome_001_mf.py",
            "tests/test_eco_001_mf.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.HIGH,
    ),

    # --- Mission Layer ---
    SourceMapping(
        pattern              = "mercury_foundry/mission/",
        domains              = ["mission", "lifecycle"],
        test_files           = [
            "tests/test_mission_001_mf.py",
            "tests/test_outcome_001_mf.py",
            "tests/test_arch_008_autonomy.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.HIGH,
    ),

    # --- Replication Layer ---
    SourceMapping(
        pattern              = "mercury_foundry/replication/",
        domains              = ["replication", "genesis"],
        test_files           = [
            "tests/test_repl_001_mf.py",
            "tests/test_arch_008_autonomy.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.HIGH,
    ),

    # --- Constitutional Core ---
    SourceMapping(
        pattern              = "mercury_foundry/constitutional/",
        domains              = ["constitution", "authority"],
        test_files           = [
            "tests/test_const_001_constitutional_core.py",
            "tests/test_arch_008_autonomy.py",
            "tests/test_mission_001_mf.py",
            "tests/test_outcome_001_mf.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.CRITICAL,
        requires_full_at_milestone = True,
    ),

    # --- Autonomy Boundary ---
    SourceMapping(
        pattern              = "mercury_foundry/autonomy/",
        domains              = ["autonomy", "authority"],
        test_files           = [
            "tests/test_arch_008_autonomy.py",
            "tests/test_const_001_constitutional_core.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.CRITICAL,
        requires_full_at_milestone = True,
    ),

    # --- Approval Gate ---
    SourceMapping(
        pattern              = "approval",
        domains              = ["approval", "security"],
        test_files           = [
            "tests/test_gate_isolation.py",
            "tests/test_human_approval_protocol.py",
            "tests/test_candidate_integrity_and_coherent_approval.py",
            "tests/test_audit_and_approval.py",
            "tests/test_legacy_candidate_not_promotable.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.HIGH,
    ),

    # --- Staging / Sandbox ---
    SourceMapping(
        pattern              = "sandbox",
        domains              = ["sandbox", "isolation"],
        test_files           = [
            "tests/test_sandbox_workspace.py",
            "tests/test_staging_isolation.py",
            "tests/test_atomic_build.py",
        ],
        minimum_level        = VerificationLevel.TARGETED,
        risk_class           = RiskClass.MEDIUM,
    ),

    # --- AI Provider ---
    SourceMapping(
        pattern              = "mercury_foundry/ai/",
        domains              = ["ai", "provider"],
        test_files           = [
            "tests/test_real_provider.py",
            "tests/test_check_provider_structured_output.py",
            "tests/test_provider_safety.py",
            "tests/test_fake_model.py",
        ],
        minimum_level        = VerificationLevel.TARGETED,
        risk_class           = RiskClass.MEDIUM,
    ),

    # --- Audit Log ---
    SourceMapping(
        pattern              = "audit",
        domains              = ["audit", "security"],
        test_files           = [
            "tests/test_audit_log_immutability.py",
            "tests/test_audit_and_approval.py",
            "tests/test_goal_candidate_invariant.py",
        ],
        minimum_level        = VerificationLevel.TARGETED,
        risk_class           = RiskClass.MEDIUM,
    ),

    # --- Diagnostics / Doctor ---
    SourceMapping(
        pattern              = "mercury_foundry/diagnostics.py",
        domains              = ["diagnostics"],
        test_files           = ["tests/test_doctor.py"],
        minimum_level        = VerificationLevel.TARGETED,
        risk_class           = RiskClass.MEDIUM,
    ),

    # --- Testing / Runner interni ---
    SourceMapping(
        pattern              = "mercury_foundry/testing/",
        domains              = ["testing"],
        test_files           = [
            "tests/test_execution_loop_e2e_healthcheck.py",
            "tests/test_real_provider_e2e_workflow.py",
        ],
        minimum_level        = VerificationLevel.TARGETED,
        risk_class           = RiskClass.MEDIUM,
    ),

    # --- Execution Loop (MF-INTEGRATE-001) ---
    SourceMapping(
        pattern              = "execution/loop",
        domains              = ["execution"],
        test_files           = [
            "tests/test_execution_loop_e2e_healthcheck.py",
            "tests/test_atomic_build.py",
            "tests/test_staging_isolation.py",
            "tests/test_integrate_001_adaptive_verification.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.HIGH,
    ),

    # --- Wiring ---
    SourceMapping(
        pattern              = "wiring.py",
        domains              = ["wiring", "execution"],
        test_files           = [
            "tests/test_execution_loop_e2e_healthcheck.py",
            "tests/test_doctor.py",
            "tests/test_integrate_001_adaptive_verification.py",
        ],
        minimum_level        = VerificationLevel.IMPACTED,
        risk_class           = RiskClass.HIGH,
    ),

    # --- Literal constraints ---
    SourceMapping(
        pattern              = "literal",
        domains              = ["constraints"],
        test_files           = ["tests/test_literal_constraints.py"],
        minimum_level        = VerificationLevel.TARGETED,
        risk_class           = RiskClass.MEDIUM,
    ),

    # --- Minor Units / Conversione monetaria ---
    SourceMapping(
        pattern              = "minor_units",
        domains              = ["economic", "budget"],
        test_files           = ["tests/test_eco_001_mf.py"],
        minimum_level        = VerificationLevel.TARGETED,
        risk_class           = RiskClass.HIGH,
    ),
]


# ---------------------------------------------------------------------------
# Test sempre inclusi (smoke / invarianti critici)
# ---------------------------------------------------------------------------

ALWAYS_RUN: list[str] = [
    # Nessun test è sempre obbligatorio a STATIC (cost 0).
    # A TARGETED+: test_doctor è sempre incluso come smoke test rapido.
]

# Test critici inclusi a IMPACTED+ (aggiunti automaticamente dal selector)
CRITICAL_TESTS: list[str] = [
    "tests/test_const_001_constitutional_core.py",
    "tests/test_arch_008_autonomy.py",
]


# ---------------------------------------------------------------------------
# Regole livello minimo per classe di rischio
# ---------------------------------------------------------------------------

RISK_TO_MINIMUM_LEVEL: dict[RiskClass, VerificationLevel] = {
    RiskClass.LOW:      VerificationLevel.STATIC,
    RiskClass.MEDIUM:   VerificationLevel.TARGETED,
    RiskClass.HIGH:     VerificationLevel.IMPACTED,
    RiskClass.CRITICAL: VerificationLevel.IMPACTED,  # FULL solo a milestone
}

# Classi che richiedono sempre test costituzionali pertinenti
REQUIRES_CONSTITUTIONAL_TESTS: set[RiskClass] = {
    RiskClass.HIGH,
    RiskClass.CRITICAL,
}

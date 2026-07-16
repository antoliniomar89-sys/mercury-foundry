"""Federation Contract V0 — MF-REPL-001.

MotherReplicaFederationContract: definisce il rapporto federato e leggero
tra Mercury Main e una Dedicated Mercury.

NON implementa networking o sincronizzazione.
È un contratto strutturato che chiarisce i diritti e doveri di entrambe le parti.

Principi federativi V0 (invarianti):
  1. La replica non richiede autorizzazione del Main per operazioni ordinarie entro mandato.
  2. Il Main non accede automaticamente ai dati clienti.
  3. La replica restituisce solo dati consentiti (reporting_obligations).
  4. Aggiornamenti di capability e Kernel sono proposte versionate.
  5. La replica può rifiutare aggiornamenti incompatibili.
  6. Emergenze e violazioni costituzionali possono generare escalation.
"""

from __future__ import annotations

from mercury_foundry.replication.models import (
    DedicatedMercuryGenesisRequest,
    MotherReplicaFederationContract,
    _new_id,
    _now_iso,
)


def build_federation_contract(
    request: DedicatedMercuryGenesisRequest,
    *,
    mother_instance_id: str = "mercury_main",
    reporting_frequency: str = "monthly",
    allowed_metrics: list[str] | None = None,
) -> MotherReplicaFederationContract:
    """Costruisce un MotherReplicaFederationContract V0 a partire dalla genesis request."""
    now = _now_iso()

    default_metrics = [
        "portfolio_level_kpis",
        "constitutional_status",
        "budget_envelope_utilization",
        "mission_count",
    ]
    metrics = allowed_metrics or default_metrics

    contract = MotherReplicaFederationContract(
        federation_contract_id=_new_id(),
        mother_instance_id=mother_instance_id,
        child_instance_id=None,  # assegnato quando la replica viene creata realmente
        constitutional_relationship="inherited_versioned",
        reporting_frequency=reporting_frequency,
        allowed_metrics=metrics,
        knowledge_return_policy="voluntary_shareable_no_pii",
        strategic_incident_policy="escalation_required",
        update_proposal_policy="versioned_opt_in",
        capability_update_policy="versioned_proposal_replica_may_reject",
        capital_distribution_policy="portfolio_reporting_only",
        emergency_intervention_policy="escalation_required",
        termination_policy="mutual_agreement_or_governance_decision",
        data_isolation_policy="full_isolation_default_no_automatic_customer_data_access",
        communication_channels=["audit_events", "constitutional_proposals", "security_advisories"],
        status="draft",
        created_at=now,
    )

    errors = contract.validate()
    if errors:
        raise ValueError(f"Federation contract non valido: {errors}")

    return contract


def validate_federation_contract(
    contract: MotherReplicaFederationContract,
) -> list[str]:
    """Valida un federation contract. Ritorna lista di errori."""
    errors = contract.validate()

    # Verifica principi federativi
    if "full_isolation" not in contract.data_isolation_policy.lower():
        errors.append(
            "data_isolation_policy non garantisce isolamento completo di default."
        )
    positive_access_patterns = [
        "allow_automatic_customer_data",
        "enables_customer_data_sharing",
        "grants_main_customer_access",
    ]
    if any(p in contract.data_isolation_policy.lower() for p in positive_access_patterns):
        errors.append(
            "data_isolation_policy non può consentire accesso automatico ai dati clienti."
        )
    if "versioned" not in contract.update_proposal_policy.lower():
        errors.append(
            "update_proposal_policy deve specificare che gli aggiornamenti sono versionati."
        )

    return errors

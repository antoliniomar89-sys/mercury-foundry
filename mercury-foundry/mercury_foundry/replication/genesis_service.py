"""GenesisService — orchestrazione del flusso genesis V0 — MF-REPL-001.

Unico entry point pubblico per la proposizione di una DedicatedMercuryGenesisRequest.

Flusso:
  1. Idempotency check
  2. Validazione schema (deterministica)
  3. Duplicate detection
  4. Independence evaluation
  5. Product family assessment (se multi-prodotto)
  6. Replication gate
  7. Genetic package build
  8. Persistenza nel registry
  9. Transizione draft → proposed
  10. Audit event unico

NON crea repliche reali. NON avvia provisioning. L'activation è forbidden.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field

from mercury_foundry.replication.events import emit_replication_event
from mercury_foundry.replication.family_assessment import assess_product_family
from mercury_foundry.replication.federation import build_federation_contract
from mercury_foundry.replication.gate import evaluate_replication_gate
from mercury_foundry.replication.genetic_package import build_genetic_package
from mercury_foundry.replication.independence import evaluate_independence
from mercury_foundry.replication.lifecycle import apply_genesis_transition
from mercury_foundry.replication.models import (
    DedicatedMercuryGenesisRequest,
    GenesisIdempotencyReplay,
    GenesisReason,
    GenesisStatus,
    ReplicationGateRequest,
    _new_id,
    _now_iso,
)
from mercury_foundry.replication.registry import (
    create_genesis_request,
    get_genesis_by_idempotency_key,
    get_genesis_request,
    store_family_assessment,
    store_gate_result,
    store_genetic_package,
    store_independence_contract,
)


@dataclass
class GenesisProposalResult:
    """Risultato della proposizione di una Genesis Request."""
    proposal_id: str
    status: str          # proposed | rejected | duplicate
    accepted: bool
    created_at: str
    explanation: str
    genesis_request_id: str | None = None
    package_id: str | None = None
    gate_result_id: str | None = None
    independence_contract_id: str | None = None
    family_assessment_id: str | None = None
    constitutional_validation_id: str | None = None
    authority_decision_id: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicate_of: str | None = None

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "status": self.status,
            "accepted": self.accepted,
            "created_at": self.created_at,
            "explanation": self.explanation,
            "genesis_request_id": self.genesis_request_id,
            "package_id": self.package_id,
            "gate_result_id": self.gate_result_id,
            "independence_contract_id": self.independence_contract_id,
            "family_assessment_id": self.family_assessment_id,
            "constitutional_validation_id": self.constitutional_validation_id,
            "authority_decision_id": self.authority_decision_id,
            "validation_errors": self.validation_errors,
            "warnings": self.warnings,
            "duplicate_of": self.duplicate_of,
        }


class GenesisService:
    """Servizio di proposizione di una Dedicated Mercury. Deterministico, fail-closed."""

    def propose(
        self,
        conn: sqlite3.Connection,
        request: DedicatedMercuryGenesisRequest,
    ) -> GenesisProposalResult:
        """Esegue il flusso completo di proposizione genesis.

        Non solleva mai eccezioni per errori di validazione: li raccoglie nel risultato.
        Solleva solo per errori di sistema (DB, ecc.).
        """
        proposal_id = _new_id()
        now = _now_iso()

        # --- 1. Idempotency check ---
        existing = get_genesis_by_idempotency_key(conn, request.idempotency_key)
        if existing is not None:
            return GenesisProposalResult(
                proposal_id=proposal_id,
                status="duplicate",
                accepted=False,
                created_at=now,
                explanation=(
                    f"Idempotency replay: genesis_request_id={existing['genesis_request_id']!r} "
                    f"già creata con idempotency_key={request.idempotency_key!r}."
                ),
                genesis_request_id=existing["genesis_request_id"],
                duplicate_of=existing["genesis_request_id"],
            )

        # --- 2. Validazione schema ---
        validation_errors = request.validate()
        if validation_errors:
            self._audit_validation_failed(conn, request, proposal_id, validation_errors)
            return GenesisProposalResult(
                proposal_id=proposal_id,
                status="rejected",
                accepted=False,
                created_at=now,
                explanation=f"Validazione fallita: {'; '.join(validation_errors)}",
                validation_errors=validation_errors,
            )

        # --- 3. Duplicate detection ---
        dup_id = self._check_duplicate(conn, request)
        if dup_id:
            return GenesisProposalResult(
                proposal_id=proposal_id,
                status="duplicate",
                accepted=False,
                created_at=now,
                explanation=(
                    f"Genesis potenzialmente duplicata di {dup_id!r}: "
                    "stessa source_mission_id con status attivo."
                ),
                duplicate_of=dup_id,
            )

        warnings: list[str] = []

        # --- 4. Independence evaluation ---
        independence_contract = evaluate_independence(
            request,
            required_local_audit=["local_audit_log"],
            required_local_governance=["REPLICATION_GOVERNANCE"],
            required_local_budget_control=["local_budget_tracker"],
            required_local_storage=["local_sqlite_db"],
        )
        ind_contract_id = independence_contract.contract_id

        # --- 5. Product family assessment ---
        family_assessment = None
        family_assessment_id = None
        if len(request.validated_product_ids) > 1:
            family_assessment = assess_product_family(
                request.validated_product_ids,
                shared_customer=request.metadata.get("shared_customer", False),
                shared_market=request.metadata.get("shared_market", False),
                shared_problem_space=request.metadata.get("shared_problem_space", False),
                shared_capabilities=request.metadata.get("shared_capabilities", False),
                shared_distribution=request.metadata.get("shared_distribution", False),
                shared_business_model=request.metadata.get("shared_business_model", False),
                shared_data_boundary=request.metadata.get("shared_data_boundary", False),
                shared_regulatory_boundary=request.metadata.get("shared_regulatory_boundary", False),
                evidence_refs=request.validation_evidence_refs,
            )
            family_assessment_id = family_assessment.assessment_id

        # --- 6. Replication gate ---
        gate_request = ReplicationGateRequest(
            request_id=_new_id(),
            genesis_request_id=request.genesis_request_id,
            source_mission_id=request.source_mission_id,
            source_expedition_id=request.source_expedition_id,
            product_ids=request.validated_product_ids,
            evidence_refs=request.validation_evidence_refs,
            validation_summary=f"score={request.product_validation_score or 0.0:.2f}",
            economic_summary=f"budget={request.requested_budget_envelope:.2f}",
            independence_contract_id=ind_contract_id,
            family_assessment_id=family_assessment_id,
            requested_at=now,
            requested_by=request.requested_by,
            correlation_id=request.correlation_id,
        )
        gate_result = evaluate_replication_gate(
            conn, request, gate_request, independence_contract,
            family_assessment=family_assessment,
        )
        const_id = gate_result.constitutional_validation_id
        auth_id = gate_result.authority_decision_id
        warnings.extend(gate_result.warnings)

        if not gate_result.approved:
            # Fallita: persisti lo stato e ritorna
            db_id = create_genesis_request(
                conn,
                genesis_request_id=request.genesis_request_id,
                idempotency_key=request.idempotency_key,
                correlation_id=request.correlation_id,
                source_mission_id=request.source_mission_id,
                source_expedition_id=request.source_expedition_id,
                validated_product_ids_json=json.dumps(request.validated_product_ids),
                product_family_key=request.product_family_key,
                proposed_instance_name=request.proposed_instance_name,
                proposed_instance_slug=request.proposed_instance_slug,
                genesis_reason=request.genesis_reason.value,
                validation_evidence_refs_json=json.dumps(request.validation_evidence_refs),
                product_validation_score=request.product_validation_score,
                pmf_confidence=request.pmf_confidence,
                target_market=request.target_market,
                target_customer=request.target_customer,
                business_model=request.business_model,
                constitutional_version=request.constitutional_version,
                kernel_version=request.kernel_version,
                requested_genesis_profile=request.requested_genesis_profile,
                requested_capability_bundle_ids_json=json.dumps(request.requested_capability_bundle_ids),
                requested_knowledge_package_ids_json=json.dumps(request.requested_knowledge_package_ids),
                requested_budget_envelope=request.requested_budget_envelope,
                requested_by=request.requested_by,
                requested_at=request.requested_at,
                metadata_json=json.dumps(request.metadata),
            )
            store_independence_contract(conn, contract=independence_contract, genesis_request_id=request.genesis_request_id)
            if family_assessment:
                store_family_assessment(conn, assessment=family_assessment, genesis_request_id=request.genesis_request_id)
            store_gate_result(conn, result=gate_result, genesis_request_id=request.genesis_request_id)
            emit_replication_event(
                conn, action="replication.genesis.rejected",
                genesis_db_id=db_id, genesis_request_id=request.genesis_request_id,
                actor_id=request.requested_by, correlation_id=request.correlation_id,
                source_mission_id=request.source_mission_id,
                constitutional_validation_id=const_id, authority_decision_id=auth_id,
                result="rejected",
                evidence_refs=request.validation_evidence_refs,
                metadata={"blockers": gate_result.blockers[:3], "proposal_id": proposal_id},
            )
            return GenesisProposalResult(
                proposal_id=proposal_id,
                status="rejected",
                accepted=False,
                created_at=now,
                explanation=gate_result.explanation,
                genesis_request_id=request.genesis_request_id,
                gate_result_id=gate_result.gate_result_id,
                independence_contract_id=ind_contract_id,
                family_assessment_id=family_assessment_id,
                constitutional_validation_id=const_id,
                authority_decision_id=auth_id,
                validation_errors=gate_result.blockers,
                warnings=warnings,
            )

        # --- 7. Genetic Package ---
        genetic_package = build_genetic_package(request)
        package_id = genetic_package.package_id

        # --- 8. Federation contract (costruzione, non persistita separatamente) ---
        federation_contract = build_federation_contract(request)

        # --- 8. Persistenza ---
        db_id = create_genesis_request(
            conn,
            genesis_request_id=request.genesis_request_id,
            idempotency_key=request.idempotency_key,
            correlation_id=request.correlation_id,
            source_mission_id=request.source_mission_id,
            source_expedition_id=request.source_expedition_id,
            validated_product_ids_json=json.dumps(request.validated_product_ids),
            product_family_key=request.product_family_key,
            proposed_instance_name=request.proposed_instance_name,
            proposed_instance_slug=request.proposed_instance_slug,
            genesis_reason=request.genesis_reason.value,
            validation_evidence_refs_json=json.dumps(request.validation_evidence_refs),
            product_validation_score=request.product_validation_score,
            pmf_confidence=request.pmf_confidence,
            target_market=request.target_market,
            target_customer=request.target_customer,
            business_model=request.business_model,
            constitutional_version=request.constitutional_version,
            kernel_version=request.kernel_version,
            requested_genesis_profile=request.requested_genesis_profile,
            requested_capability_bundle_ids_json=json.dumps(request.requested_capability_bundle_ids),
            requested_knowledge_package_ids_json=json.dumps(request.requested_knowledge_package_ids),
            requested_budget_envelope=request.requested_budget_envelope,
            requested_federation_profile_json=json.dumps(federation_contract.to_dict()),
            requested_by=request.requested_by,
            requested_at=request.requested_at,
            metadata_json=json.dumps(request.metadata),
        )

        store_independence_contract(conn, contract=independence_contract, genesis_request_id=request.genesis_request_id)
        if family_assessment:
            store_family_assessment(conn, assessment=family_assessment, genesis_request_id=request.genesis_request_id)
        store_gate_result(conn, result=gate_result, genesis_request_id=request.genesis_request_id)
        store_genetic_package(conn, package=genetic_package, genesis_request_id=request.genesis_request_id)

        # --- 9. Transizione draft → proposed ---
        apply_genesis_transition(
            conn,
            genesis_request_id=request.genesis_request_id,
            current_status=GenesisStatus.DRAFT.value,
            current_version=1,
            to_status=GenesisStatus.PROPOSED.value,
            requested_by=request.requested_by,
            reason="Genesis request valida: gate passato, package creato.",
            correlation_id=request.correlation_id,
            authority_decision_id=auth_id,
            constitutional_validation_id=const_id,
            evidence_refs=request.validation_evidence_refs,
        )

        # --- 10. Audit event ---
        emit_replication_event(
            conn, action="replication.genesis.requested",
            genesis_db_id=db_id, genesis_request_id=request.genesis_request_id,
            actor_id=request.requested_by, correlation_id=request.correlation_id,
            source_mission_id=request.source_mission_id,
            source_expedition_id=request.source_expedition_id,
            constitutional_validation_id=const_id, authority_decision_id=auth_id,
            result="proposed",
            evidence_refs=request.validation_evidence_refs,
            metadata={
                "proposal_id": proposal_id,
                "package_id": package_id,
                "gate_result_id": gate_result.gate_result_id,
                "independence_status": independence_contract.status.value,
                "warnings_count": len(warnings),
            },
        )
        emit_replication_event(
            conn, action="replication.genesis.proposed",
            genesis_db_id=db_id, genesis_request_id=request.genesis_request_id,
            actor_id=request.requested_by, correlation_id=request.correlation_id,
            source_mission_id=request.source_mission_id,
            metadata={"proposal_id": proposal_id},
        )
        emit_replication_event(
            conn, action="replication.genetic_package.created",
            genesis_db_id=db_id, genesis_request_id=request.genesis_request_id,
            actor_id=request.requested_by, correlation_id=request.correlation_id,
            metadata={"package_id": package_id, "checksum": genetic_package.checksum[:16]},
        )

        return GenesisProposalResult(
            proposal_id=proposal_id,
            status="proposed",
            accepted=True,
            created_at=now,
            explanation=(
                f"Genesis request {request.genesis_request_id!r} proposta con successo "
                f"(status=proposed, gate={gate_result.status.value})."
            ),
            genesis_request_id=request.genesis_request_id,
            package_id=package_id,
            gate_result_id=gate_result.gate_result_id,
            independence_contract_id=ind_contract_id,
            family_assessment_id=family_assessment_id,
            constitutional_validation_id=const_id,
            authority_decision_id=auth_id,
            warnings=warnings,
        )

    def _check_duplicate(
        self, conn: sqlite3.Connection, request: DedicatedMercuryGenesisRequest
    ) -> str | None:
        row = conn.execute(
            """
            SELECT genesis_request_id FROM dedicated_mercury_genesis_requests
            WHERE source_mission_id = ?
              AND status IN ('proposed', 'under_review', 'approved', 'packaging',
                             'ready_for_provisioning')
            LIMIT 1
            """,
            (request.source_mission_id,),
        ).fetchone()
        return row["genesis_request_id"] if row else None

    def _audit_validation_failed(
        self, conn, request, proposal_id, errors
    ) -> None:
        try:
            from mercury_foundry.audit.logger import log_action
            log_action(
                conn,
                entity_type="replication",
                entity_id=0,
                action="replication.validation.failed",
                actor=request.requested_by or "unknown",
                payload={
                    "proposal_id": proposal_id,
                    "correlation_id": request.correlation_id,
                    "genesis_request_id": request.genesis_request_id,
                    "errors": errors,
                    "source_mission_id": request.source_mission_id,
                },
            )
        except Exception:
            pass

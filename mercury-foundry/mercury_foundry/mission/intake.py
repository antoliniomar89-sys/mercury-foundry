"""Mission Intake Service — MF-MISSION-001.

Flusso deterministico (nessun LLM):
  1. Validazione schema (campi obbligatori, enum, budget, deadline)
  2. Idempotency check (chiave già usata → replay sicuro)
  3. Duplicati evidenti (stesso titolo+origin+objective nello stato active/ready)
  4. Origine autorizzata
  5. Budget e risk profile validi
  6. Criteri minimi di successo
  7. Capability contracts (gap detection)
  8. Validazione costituzionale (MF-CONST-001, shadow mode)
  9. Autorizzazione Autonomy Boundary (MISSION_CREATE, shadow mode)
  10. Creazione Mission nel registry
  11. Audit event (una sola volta)

Produce MissionIntakeResult con esito strutturato.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from mercury_foundry.mission.models import (
    MissionBudget,
    MissionIdempotencyReplay,
    MissionIntakeRequest,
    MissionIntakeResult,
    MissionRiskProfile,
    MissionStatus,
    OriginType,
    Priority,
    _now_iso,
    new_intake_id,
    new_mission_id,
)
from mercury_foundry.mission.registry import create_mission, get_by_idempotency_key
from mercury_foundry.mission.events import emit_mission_event
from mercury_foundry.mission.capability_contracts import NullCapabilityProvider


# Origini autorizzate in V0 per intake diretto senza escalation aggiuntiva
_AUTHORIZED_ORIGINS = frozenset({
    OriginType.FOUNDER.value,
    OriginType.AUTONOMOUS_DISCOVERY.value,
    OriginType.CUSTOMER.value,
    OriginType.BUSINESS_CELL.value,
    OriginType.INTERNAL_ORGAN.value,
    OriginType.LABORATORY.value,
    OriginType.PORTFOLIO_ORCHESTRATOR.value,
    OriginType.EXTERNAL_SYSTEM.value,
})

# Origini che richiedono un evidence_ref obbligatorio (V0 advisory)
_EVIDENCE_RECOMMENDED_ORIGINS = frozenset({
    OriginType.EXTERNAL_SYSTEM.value,
    OriginType.PORTFOLIO_ORCHESTRATOR.value,
})


class MissionIntakeService:
    """Servizio di intake per Mission. Deterministico, fail-closed.

    Dependency injection:
      - `capability_provider`: provider di capability (default: NullCapabilityProvider)
    """

    def __init__(self, capability_provider=None) -> None:
        self._cap_provider = capability_provider or NullCapabilityProvider()

    def submit(
        self,
        conn: sqlite3.Connection,
        request: MissionIntakeRequest,
    ) -> MissionIntakeResult:
        """Esegue l'intake di una Mission e ritorna il risultato strutturato.

        Non solleva mai eccezioni per errori di validazione: li raccoglie nel
        MissionIntakeResult. Solleva solo su errori di sistema (DB, ecc.).
        """
        intake_id = new_intake_id()
        correlation_id = request.correlation_id or str(uuid.uuid4())
        now = _now_iso()

        # --- 2. Idempotency check ---
        existing = get_by_idempotency_key(conn, request.idempotency_key)
        if existing is not None:
            return MissionIntakeResult(
                intake_id=intake_id,
                status="duplicate",
                accepted=False,
                created_at=now,
                explanation=(
                    f"Idempotency replay: mission_id={existing.mission_id!r} già creata "
                    f"con idempotency_key={request.idempotency_key!r}."
                ),
                mission_id=existing.mission_id,
                duplicate_of=existing.mission_id,
            )

        # --- 1. Validazione schema ---
        validation_errors, warnings, missing_fields = self._validate(request)

        if validation_errors or missing_fields:
            self._audit_validation_failed(conn, request, intake_id, correlation_id,
                                          validation_errors + missing_fields)
            return MissionIntakeResult(
                intake_id=intake_id,
                status="rejected",
                accepted=False,
                created_at=now,
                explanation=f"Validazione fallita: {'; '.join(validation_errors + missing_fields)}",
                validation_errors=validation_errors,
                missing_fields=missing_fields,
                warnings=warnings,
            )

        # --- 3. Duplicati evidenti ---
        dup_id = self._check_duplicate(conn, request)
        if dup_id:
            return MissionIntakeResult(
                intake_id=intake_id,
                status="duplicate",
                accepted=False,
                created_at=now,
                explanation=(
                    f"Mission potenzialmente duplicata di {dup_id!r}: "
                    "stesso titolo, origin_type e objective con stato active/ready."
                ),
                duplicate_of=dup_id,
                warnings=warnings,
            )

        # --- 7. Capability gap detection ---
        cap_report = self._cap_provider.check_capability_availability(
            request.required_capabilities
        )
        mandatory_gaps = cap_report.mandatory_gaps(request.required_capabilities)
        optional_gaps = cap_report.optional_gaps(request.required_capabilities)

        if mandatory_gaps:
            gap_ids = [g.capability_id for g in mandatory_gaps]
            validation_errors.append(
                f"Capability obbligatorie non disponibili: {gap_ids}"
            )

        cap_warnings = [
            f"Capability opzionale non disponibile: {g.capability_id} "
            f"({g.gap_reason or 'N/A'})"
            for g in optional_gaps
        ]
        warnings.extend(cap_warnings)

        if mandatory_gaps:
            return MissionIntakeResult(
                intake_id=intake_id,
                status="rejected",
                accepted=False,
                created_at=now,
                explanation=f"Capability obbligatorie mancanti: {[g.capability_id for g in mandatory_gaps]}",
                validation_errors=validation_errors,
                warnings=warnings,
            )

        # --- 8. Validazione costituzionale ---
        const_validation_id: str | None = None
        const_result = self._run_constitutional_check(conn, request, correlation_id)
        if const_result is not None:
            const_validation_id = const_result.validation_id
            if const_result.warnings:
                warnings.extend(const_result.warnings[:3])

        # --- 9. Autonomy Boundary ---
        authority_decision_id: str | None = None
        auth_result = self._run_authority_check(conn, request, correlation_id)
        if auth_result is not None:
            authority_decision_id = (
                str(auth_result.decision_record_id)
                if auth_result.decision_record_id is not None
                else None
            )
            if not auth_result.allowed and auth_result.authority_mode == "forbidden":
                return MissionIntakeResult(
                    intake_id=intake_id,
                    status="rejected",
                    accepted=False,
                    created_at=now,
                    explanation=f"Autorizzazione negata (forbidden): {auth_result.reason}",
                    authority_decision_id=authority_decision_id,
                    constitutional_validation_id=const_validation_id,
                    warnings=warnings,
                )

        # --- 10. Creazione Mission ---
        mission_id = new_mission_id()

        try:
            from mercury_foundry.constitutional.registry import ConstitutionRegistry
            const_version = ConstitutionRegistry.load().version_string
        except Exception:
            const_version = "unknown"

        db_id = create_mission(
            conn,
            mission_id=mission_id,
            idempotency_key=request.idempotency_key,
            correlation_id=correlation_id,
            title=request.title,
            description=request.description,
            origin_type=request.origin_type.value,
            origin_ref=request.origin_ref,
            mission_type=request.mission_type.value,
            objective=request.objective,
            created_by=request.created_by,
            constitutional_version=const_version,
            expected_outcomes_json=json.dumps(
                [o.to_dict() for o in request.expected_outcomes], ensure_ascii=False
            ),
            success_criteria_json=json.dumps(
                [c.to_dict() for c in request.success_criteria], ensure_ascii=False
            ),
            termination_criteria_json=json.dumps(
                [c.to_dict() for c in request.termination_criteria], ensure_ascii=False
            ),
            constraints_json=json.dumps(request.constraints, ensure_ascii=False),
            budget_json=json.dumps(request.budget.to_dict(), ensure_ascii=False),
            risk_profile_json=json.dumps(request.risk_profile.to_dict(), ensure_ascii=False),
            authority_request_json=json.dumps(
                request.authority_request.to_dict(), ensure_ascii=False
            ),
            required_capabilities_json=json.dumps(
                [c.to_dict() for c in request.required_capabilities], ensure_ascii=False
            ),
            priority=request.priority.value,
            knowledge_scope=request.knowledge_scope.value,
            business_scope=request.business_scope.value,
            deadline=request.deadline,
            parent_mission_id=request.parent_mission_id,
            metadata_json=json.dumps(request.metadata, ensure_ascii=False),
        )

        # --- 11. Audit event (una sola volta) ---
        emit_mission_event(
            conn,
            action="mission.created",
            mission_db_id=db_id,
            mission_id=mission_id,
            actor_id=request.created_by,
            correlation_id=correlation_id,
            origin_type=request.origin_type.value,
            new_status=MissionStatus.DRAFT.value,
            authority_decision_id=authority_decision_id,
            constitutional_validation_id=const_validation_id,
            metadata={
                "intake_id": intake_id,
                "warnings_count": len(warnings),
            },
        )

        # Emetti anche mission.intake.received (evento separato — l'intake è
        # un'operazione distinta dalla creazione nel registry)
        emit_mission_event(
            conn,
            action="mission.intake.received",
            mission_db_id=db_id,
            mission_id=mission_id,
            actor_id=request.created_by,
            correlation_id=correlation_id,
            origin_type=request.origin_type.value,
            metadata={"intake_id": intake_id},
        )

        return MissionIntakeResult(
            intake_id=intake_id,
            status="accepted",
            accepted=True,
            created_at=now,
            explanation=(
                f"Mission {mission_id!r} creata con successo (status=draft)."
                + (f" Avvertimenti: {len(warnings)}." if warnings else "")
            ),
            mission_id=mission_id,
            warnings=warnings,
            constitutional_validation_id=const_validation_id,
            authority_decision_id=authority_decision_id,
        )

    # ---------------------------------------------------------------------------
    # Validazione
    # ---------------------------------------------------------------------------

    def _validate(
        self, request: MissionIntakeRequest
    ) -> tuple[list[str], list[str], list[str]]:
        """Ritorna (errors, warnings, missing_fields)."""
        errors: list[str] = []
        warnings: list[str] = []
        missing: list[str] = []

        # Campi obbligatori (già imposti dalla dataclass, ma controlliamo vuoto)
        if not request.title or not request.title.strip():
            missing.append("title")
        if not request.description or not request.description.strip():
            missing.append("description")
        if not request.objective or not request.objective.strip():
            missing.append("objective")
        if not request.idempotency_key or not request.idempotency_key.strip():
            missing.append("idempotency_key")
        if not request.created_by or not request.created_by.strip():
            missing.append("created_by")

        # Origine autorizzata
        if request.origin_type.value not in _AUTHORIZED_ORIGINS:
            errors.append(f"origin_type non autorizzato: {request.origin_type.value!r}")

        # Budget
        budget_errors = request.budget.validate()
        errors.extend(budget_errors)

        # Risk profile
        risk_errors = request.risk_profile.validate()
        errors.extend(risk_errors)

        # Success criteria minimi (almeno 1 required se mission_type ≠ custom)
        from mercury_foundry.mission.models import MissionType
        if request.mission_type != MissionType.CUSTOM:
            required_criteria = [c for c in request.success_criteria if c.required]
            if not required_criteria:
                errors.append(
                    "success_criteria: almeno 1 criterio required per mission_type "
                    f"{request.mission_type.value!r}. Usare mission_type=custom per esentarsi."
                )

        # Deadline (formato ISO 8601 base)
        if request.deadline is not None:
            try:
                datetime.fromisoformat(request.deadline.replace("Z", "+00:00"))
            except ValueError:
                errors.append(
                    f"deadline non è un timestamp ISO 8601 valido: {request.deadline!r}"
                )

        # Evidence raccomandati per certe origini
        if request.origin_type.value in _EVIDENCE_RECOMMENDED_ORIGINS:
            if not request.metadata.get("evidence_refs"):
                warnings.append(
                    f"origin_type={request.origin_type.value!r} raccomanda evidence_refs nel metadata."
                )

        # Budget investimento significativo → richiede success criteria con evidenza
        if request.budget.approved_amount > 0:
            has_evidence_criteria = any(
                c.required_evidence for c in request.success_criteria
            )
            if not has_evidence_criteria:
                warnings.append(
                    "Budget > 0 ma nessun success_criterion ha required_evidence: "
                    "CONST-001 potrebbe segnalare una violazione."
                )

        return errors, warnings, missing

    def _check_duplicate(
        self, conn: sqlite3.Connection, request: MissionIntakeRequest
    ) -> str | None:
        """Cerca Mission con stesso titolo+origin_type+objective in stato active/ready."""
        row = conn.execute(
            """
            SELECT mission_id FROM missions
            WHERE title = ? AND origin_type = ? AND objective = ?
              AND status IN ('active', 'ready', 'accepted', 'under_review', 'submitted')
            LIMIT 1
            """,
            (request.title, request.origin_type.value, request.objective),
        ).fetchone()
        return row["mission_id"] if row else None

    def _run_constitutional_check(self, conn, request, correlation_id):
        """Chiama maybe_validate_constitution in shadow mode."""
        try:
            from mercury_foundry.constitutional.shadow import maybe_validate_constitution
            return maybe_validate_constitution(
                conn,
                organ_key="MISSION_CONTROL",
                decision_type="MISSION_CREATE",
                authority_mode=request.authority_request.requested_mode,
                subject_type="mission",
                subject_id=request.idempotency_key,
                evidence_refs=list(request.metadata.get("evidence_refs", [])),
                budget_impact=request.budget.approved_amount,
                risk_level=request.risk_profile.risk_level,
                correlation_id=correlation_id,
                metadata={
                    "rollback_plan": request.risk_profile.rollback_plan or "",
                    "title": request.title,
                },
            )
        except Exception:
            return None

    def _run_authority_check(self, conn, request, correlation_id):
        """Chiama authorize_organ_decision per MISSION_CREATE."""
        try:
            from mercury_foundry.autonomy.authorization import authorize_organ_decision
            return authorize_organ_decision(
                conn,
                organ_key="MISSION_CONTROL",
                decision_type="MISSION_CREATE",
                subject_type="mission",
                subject_id=request.idempotency_key,
                estimated_budget=request.budget.approved_amount,
            )
        except Exception:
            return None

    def _audit_validation_failed(
        self, conn, request, intake_id, correlation_id, errors
    ) -> None:
        """Emette mission.validation.failed nell'audit log senza missione creata."""
        try:
            from mercury_foundry.audit.logger import log_action
            log_action(
                conn,
                entity_type="mission",
                entity_id=0,
                action="mission.validation.failed",
                actor=request.created_by or "unknown",
                payload={
                    "intake_id": intake_id,
                    "correlation_id": correlation_id,
                    "errors": errors,
                    "origin_type": request.origin_type.value,
                    "idempotency_key": request.idempotency_key,
                },
            )
        except Exception:
            pass

"""Servizio centrale di autorizzazione — MF-ARCH-008.

`authorize_organ_decision` è l'unico punto in cui un organo può richiedere
autorizzazione per un'azione. Il risultato dipende esclusivamente dal mandato
registrato; nessun organo può autoassegnarsi autorità.

Comportamento fail-closed:
  - organo assente        → rejected (ORGAN_NOT_FOUND)
  - mandato assente       → rejected (MANDATE_NOT_FOUND)
  - mandato disabilitato  → rejected (MANDATE_DISABLED)
  - evidence mancante     → rejected (EVIDENCE_REQUIRED)
  - risk_score > limite   → escalation_required (RISK_LIMIT_EXCEEDED)
  - budget > limite       → escalation_required (BUDGET_LIMIT_EXCEEDED)

authority_mode finale:
  - autonomous            → allowed=True,  status=authorized
  - proposal              → allowed=False, status=proposed,  requires_human_approval=True
  - escalation_required   → allowed=False, status=escalated, requires_human_approval=True
  - forbidden             → allowed=False, status=rejected

Ogni decisione viene:
  1. scritta in decision_records
  2. emessa come organ_event (DECISION_RECORDED)
  3. registrata nell'audit log esistente (append-only)
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from mercury_foundry.audit.logger import log_action
from mercury_foundry.autonomy import models as am
from mercury_foundry.constitutional.shadow import maybe_validate_constitution


class AutonomyBoundaryViolation(RuntimeError):
    """Sollevata in modalità enforced quando il mandato nega l'autorizzazione."""


@dataclass
class AuthorizationResult:
    allowed: bool
    authority_mode: str   # autonomous|proposal|escalation_required|forbidden|unknown
    resulting_status: str # authorized|proposed|escalated|rejected
    reason: str
    decision_record_id: int | None
    requires_human_approval: bool


def authorize_organ_decision(
    conn: sqlite3.Connection,
    *,
    organ_key: str,
    decision_type: str,
    subject_type: str,
    subject_id: str,
    evidence: dict | None = None,
    confidence: float | None = None,
    risk_score: float | None = None,
    estimated_budget: float | None = None,
) -> AuthorizationResult:
    """Autorizza (o nega) una decisione di un organo in base al suo mandato.

    La funzione è idempotente nella lettura e transazionale nella scrittura:
    ogni chiamata produce esattamente un decision_record e un organ_event.
    """
    correlation_id = str(uuid.uuid4())

    # --- 1. Lookup organo ---
    organ = am.get_organ_by_key(conn, organ_key)
    if organ is None:
        reason = f"ORGAN_NOT_FOUND: organ_key={organ_key!r} non registrato"
        log_action(
            conn,
            entity_type="autonomy",
            entity_id=0,
            action="AUTONOMY_DECISION_REJECTED",
            actor="system",
            payload={
                "organ_key": organ_key,
                "decision_type": decision_type,
                "reason": reason,
                "correlation_id": correlation_id,
            },
        )
        return AuthorizationResult(
            allowed=False,
            authority_mode="unknown",
            resulting_status="rejected",
            reason=reason,
            decision_record_id=None,
            requires_human_approval=False,
        )

    organ_id: int = organ["id"]

    # --- 2. Lookup mandato ---
    mandate = am.get_mandate(conn, organ_id, decision_type)
    if mandate is None:
        reason = f"MANDATE_NOT_FOUND: nessun mandato per ({organ_key!r}, {decision_type!r})"
        record_id = am.create_decision_record(
            conn,
            organ_id=organ_id,
            decision_type=decision_type,
            authority_mode="unknown",
            subject_type=subject_type,
            subject_id=subject_id,
            input_evidence=evidence,
            confidence=confidence,
            risk_score=risk_score,
            status="rejected",
            reason=reason,
        )
        _emit_event(conn, organ_id, correlation_id, record_id, "DECISION_REJECTED", reason)
        log_action(
            conn,
            entity_type="decision_record",
            entity_id=record_id,
            action="AUTONOMY_DECISION_REJECTED",
            actor="system",
            payload={"reason": reason, "organ_key": organ_key, "decision_type": decision_type,
                     "correlation_id": correlation_id},
        )
        return AuthorizationResult(
            allowed=False,
            authority_mode="unknown",
            resulting_status="rejected",
            reason=reason,
            decision_record_id=record_id,
            requires_human_approval=False,
        )

    if not mandate["enabled"]:
        reason = f"MANDATE_DISABLED: mandato ({organ_key!r}, {decision_type!r}) disabilitato"
        record_id = am.create_decision_record(
            conn,
            organ_id=organ_id,
            decision_type=decision_type,
            authority_mode=mandate["authority_mode"],
            subject_type=subject_type,
            subject_id=subject_id,
            input_evidence=evidence,
            confidence=confidence,
            risk_score=risk_score,
            status="rejected",
            reason=reason,
        )
        _emit_event(conn, organ_id, correlation_id, record_id, "DECISION_REJECTED", reason)
        log_action(
            conn,
            entity_type="decision_record",
            entity_id=record_id,
            action="AUTONOMY_DECISION_REJECTED",
            actor="system",
            payload={"reason": reason, "correlation_id": correlation_id},
        )
        return AuthorizationResult(
            allowed=False,
            authority_mode=mandate["authority_mode"],
            resulting_status="rejected",
            reason=reason,
            decision_record_id=record_id,
            requires_human_approval=False,
        )

    authority_mode: str = mandate["authority_mode"]

    # --- 3. Evidence obbligatoria ---
    if mandate["requires_evidence"] and not evidence:
        reason = f"EVIDENCE_REQUIRED: mandato ({organ_key!r}, {decision_type!r}) richiede evidence"
        record_id = am.create_decision_record(
            conn,
            organ_id=organ_id,
            decision_type=decision_type,
            authority_mode=authority_mode,
            subject_type=subject_type,
            subject_id=subject_id,
            input_evidence=evidence,
            confidence=confidence,
            risk_score=risk_score,
            status="rejected",
            reason=reason,
        )
        _emit_event(conn, organ_id, correlation_id, record_id, "DECISION_REJECTED", reason)
        log_action(
            conn,
            entity_type="decision_record",
            entity_id=record_id,
            action="AUTONOMY_DECISION_REJECTED",
            actor="system",
            payload={"reason": reason, "correlation_id": correlation_id},
        )
        return AuthorizationResult(
            allowed=False,
            authority_mode=authority_mode,
            resulting_status="rejected",
            reason=reason,
            decision_record_id=record_id,
            requires_human_approval=False,
        )

    # --- 4. Limite risk_score ---
    max_risk = mandate["max_risk_score"]
    if max_risk is not None and risk_score is not None and risk_score > max_risk:
        reason = (
            f"RISK_LIMIT_EXCEEDED: risk_score={risk_score} > max_risk_score={max_risk} "
            f"per ({organ_key!r}, {decision_type!r})"
        )
        record_id = am.create_decision_record(
            conn,
            organ_id=organ_id,
            decision_type=decision_type,
            authority_mode=authority_mode,
            subject_type=subject_type,
            subject_id=subject_id,
            input_evidence=evidence,
            confidence=confidence,
            risk_score=risk_score,
            status="escalated",
            reason=reason,
        )
        _emit_event(conn, organ_id, correlation_id, record_id, "DECISION_ESCALATED", reason)
        log_action(
            conn,
            entity_type="decision_record",
            entity_id=record_id,
            action="AUTONOMY_DECISION_ESCALATED",
            actor="system",
            payload={"reason": reason, "risk_score": risk_score, "max_risk": max_risk,
                     "correlation_id": correlation_id},
        )
        return AuthorizationResult(
            allowed=False,
            authority_mode=authority_mode,
            resulting_status="escalated",
            reason=reason,
            decision_record_id=record_id,
            requires_human_approval=True,
        )

    # --- 5. Limite budget ---
    max_budget = mandate["max_budget"]
    if max_budget is not None and estimated_budget is not None and estimated_budget > max_budget:
        reason = (
            f"BUDGET_LIMIT_EXCEEDED: estimated_budget={estimated_budget} > max_budget={max_budget} "
            f"per ({organ_key!r}, {decision_type!r})"
        )
        record_id = am.create_decision_record(
            conn,
            organ_id=organ_id,
            decision_type=decision_type,
            authority_mode=authority_mode,
            subject_type=subject_type,
            subject_id=subject_id,
            input_evidence=evidence,
            confidence=confidence,
            risk_score=risk_score,
            status="escalated",
            reason=reason,
        )
        _emit_event(conn, organ_id, correlation_id, record_id, "DECISION_ESCALATED", reason)
        log_action(
            conn,
            entity_type="decision_record",
            entity_id=record_id,
            action="AUTONOMY_DECISION_ESCALATED",
            actor="system",
            payload={"reason": reason, "estimated_budget": estimated_budget,
                     "max_budget": max_budget, "correlation_id": correlation_id},
        )
        return AuthorizationResult(
            allowed=False,
            authority_mode=authority_mode,
            resulting_status="escalated",
            reason=reason,
            decision_record_id=record_id,
            requires_human_approval=True,
        )

    # --- 6. Validazione costituzionale (MF-CONST-001) ---
    # Flusso: Mandate/Authority Validation → Constitutional Validation
    #         → Existing Decision Path → Audit Record.
    # In shadow mode: valida e registra, non blocca mai.
    # In enforce mode: può sollevare ConstitutionalViolationError (V0:
    #   nessun principio BLOCKING → non blocca in pratica).
    # In disabled mode: no-op.
    maybe_validate_constitution(
        conn,
        organ_key=organ_key,
        decision_type=decision_type,
        authority_mode=authority_mode,
        subject_type=subject_type,
        subject_id=subject_id,
        evidence_refs=list(evidence.keys()) if evidence else [],
        budget_impact=estimated_budget,
        risk_level=(
            "high" if risk_score is not None and risk_score > 0.7
            else "medium" if risk_score is not None and risk_score > 0.3
            else "low" if risk_score is not None
            else None
        ),
        correlation_id=correlation_id,
    )

    # --- 7. Applica authority_mode ---
    return _apply_authority_mode(
        conn, organ_id, organ_key, decision_type, authority_mode,
        subject_type, subject_id, evidence, confidence, risk_score, correlation_id,
    )


def _apply_authority_mode(
    conn: sqlite3.Connection,
    organ_id: int,
    organ_key: str,
    decision_type: str,
    authority_mode: str,
    subject_type: str,
    subject_id: str,
    evidence: dict | None,
    confidence: float | None,
    risk_score: float | None,
    correlation_id: str,
) -> AuthorizationResult:
    if authority_mode == "autonomous":
        status = "authorized"
        allowed = True
        requires_human = False
        audit_action = "AUTONOMY_DECISION_AUTHORIZED"
        reason = f"AUTONOMOUS: decisione autonoma per ({organ_key!r}, {decision_type!r})"
        event_type = "DECISION_AUTHORIZED"

    elif authority_mode == "proposal":
        status = "proposed"
        allowed = False
        requires_human = True
        audit_action = "AUTONOMY_DECISION_PROPOSED"
        reason = f"PROPOSAL: decisione proposta, richiede revisione umana per ({organ_key!r}, {decision_type!r})"
        event_type = "DECISION_PROPOSED"

    elif authority_mode == "escalation_required":
        status = "escalated"
        allowed = False
        requires_human = True
        audit_action = "AUTONOMY_DECISION_ESCALATED"
        reason = f"ESCALATION_REQUIRED: escalation obbligatoria per ({organ_key!r}, {decision_type!r})"
        event_type = "DECISION_ESCALATED"

    elif authority_mode == "forbidden":
        status = "rejected"
        allowed = False
        requires_human = False
        audit_action = "AUTONOMY_DECISION_REJECTED"
        reason = f"FORBIDDEN: decisione esplicitamente vietata per ({organ_key!r}, {decision_type!r})"
        event_type = "DECISION_REJECTED"

    else:
        # authority_mode sconosciuto → fail-closed
        status = "rejected"
        allowed = False
        requires_human = False
        audit_action = "AUTONOMY_DECISION_REJECTED"
        reason = f"UNKNOWN_AUTHORITY_MODE: {authority_mode!r} non riconosciuto"
        event_type = "DECISION_REJECTED"

    record_id = am.create_decision_record(
        conn,
        organ_id=organ_id,
        decision_type=decision_type,
        authority_mode=authority_mode,
        subject_type=subject_type,
        subject_id=subject_id,
        input_evidence=evidence,
        confidence=confidence,
        risk_score=risk_score,
        status=status,
        reason=reason,
    )
    _emit_event(conn, organ_id, correlation_id, record_id, event_type, reason)
    log_action(
        conn,
        entity_type="decision_record",
        entity_id=record_id,
        action=audit_action,
        actor="system",
        payload={
            "organ_key": organ_key,
            "decision_type": decision_type,
            "authority_mode": authority_mode,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "correlation_id": correlation_id,
            "reason": reason,
        },
    )

    return AuthorizationResult(
        allowed=allowed,
        authority_mode=authority_mode,
        resulting_status=status,
        reason=reason,
        decision_record_id=record_id,
        requires_human_approval=requires_human,
    )


def _emit_event(
    conn: sqlite3.Connection,
    organ_id: int,
    correlation_id: str,
    record_id: int,
    event_type: str,
    reason: str,
) -> None:
    am.create_organ_event(
        conn,
        source_organ_id=organ_id,
        event_type=event_type,
        payload={"decision_record_id": record_id, "reason": reason},
        correlation_id=correlation_id,
        status="pending",
    )

"""Integrazione shadow/enforced dell'Autonomy Boundary Layer — MF-ARCH-008.

`maybe_check_governance` è il punto di contatto tra i moduli operativi
esistenti (gate.py, orchestrator, ecc.) e il servizio di autorizzazione.

Modalità SHADOW (default):
  - Chiama `authorize_organ_decision` per ogni operazione critica.
  - Il risultato viene registrato nell'audit log.
  - QUALSIASI eccezione (tecnica o autorizzativa) è silenziata: il flusso
    operativo esistente non viene mai interrotto.
  - Una divergenza (not allowed) viene segnalata come audit AUTONOMY_SHADOW_DIVERGENCE.

Modalità ENFORCED:
  - Chiama `authorize_organ_decision` normalmente.
  - Se il risultato è `allowed=False`, solleva `AutonomyBoundaryViolation`.
  - Eccezioni tecniche interne propagano normalmente.
"""

from __future__ import annotations

import sqlite3

from mercury_foundry import config
from mercury_foundry.audit.logger import log_action
from mercury_foundry.autonomy.authorization import (
    AuthorizationResult,
    AutonomyBoundaryViolation,
    authorize_organ_decision,
)

_GOVERNANCE_ORGAN_KEY = "FOUNDRY_GOVERNANCE"


def maybe_check_governance(
    conn: sqlite3.Connection,
    *,
    decision_type: str,
    subject_type: str,
    subject_id: str,
    evidence: dict | None = None,
    confidence: float | None = None,
    risk_score: float | None = None,
    estimated_budget: float | None = None,
) -> AuthorizationResult | None:
    """Controlla il mandato FOUNDRY_GOVERNANCE in shadow o enforced mode.

    Ritorna AuthorizationResult in entrambe le modalità (utile per i test).
    Ritorna None se in shadow mode e una eccezione tecnica è stata silenziata.
    """
    if config.AUTONOMY_MODE == "shadow":
        return _shadow_check(
            conn, decision_type=decision_type, subject_type=subject_type,
            subject_id=subject_id, evidence=evidence, confidence=confidence,
            risk_score=risk_score, estimated_budget=estimated_budget,
        )
    else:
        return _enforced_check(
            conn, decision_type=decision_type, subject_type=subject_type,
            subject_id=subject_id, evidence=evidence, confidence=confidence,
            risk_score=risk_score, estimated_budget=estimated_budget,
        )


def _shadow_check(
    conn: sqlite3.Connection,
    *,
    decision_type: str,
    subject_type: str,
    subject_id: str,
    evidence: dict | None,
    confidence: float | None,
    risk_score: float | None,
    estimated_budget: float | None,
) -> AuthorizationResult | None:
    """Modalità shadow: chiama il servizio di autorizzazione senza mai bloccare.

    In caso di eccezione tecnica (tabelle mancanti, DB non migrato, ecc.)
    registra un audit AUTONOMY_SHADOW_ERROR e ritorna None.
    In caso di decisione non autorizzata registra AUTONOMY_SHADOW_DIVERGENCE.
    """
    try:
        result = authorize_organ_decision(
            conn,
            organ_key=_GOVERNANCE_ORGAN_KEY,
            decision_type=decision_type,
            subject_type=subject_type,
            subject_id=subject_id,
            evidence=evidence,
            confidence=confidence,
            risk_score=risk_score,
            estimated_budget=estimated_budget,
        )
    except Exception as exc:
        # Eccezione tecnica: non bloccare mai in shadow mode.
        try:
            log_action(
                conn,
                entity_type="autonomy",
                entity_id=0,
                action="AUTONOMY_SHADOW_ERROR",
                actor="system",
                payload={
                    "decision_type": decision_type,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
        except Exception:
            pass  # log_action stesso ha fallito — silenzio totale
        return None

    if not result.allowed:
        try:
            log_action(
                conn,
                entity_type="autonomy",
                entity_id=result.decision_record_id or 0,
                action="AUTONOMY_SHADOW_DIVERGENCE",
                actor="system",
                payload={
                    "decision_type": decision_type,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "resulting_status": result.resulting_status,
                    "reason": result.reason,
                    "authority_mode": result.authority_mode,
                    "requires_human_approval": result.requires_human_approval,
                    "note": "shadow: flusso non bloccato",
                },
            )
        except Exception:
            pass

    return result


def _enforced_check(
    conn: sqlite3.Connection,
    *,
    decision_type: str,
    subject_type: str,
    subject_id: str,
    evidence: dict | None,
    confidence: float | None,
    risk_score: float | None,
    estimated_budget: float | None,
) -> AuthorizationResult:
    """Modalità enforced: blocca se il mandato nega l'autorizzazione.

    Solleva AutonomyBoundaryViolation se `result.allowed == False`.
    """
    result = authorize_organ_decision(
        conn,
        organ_key=_GOVERNANCE_ORGAN_KEY,
        decision_type=decision_type,
        subject_type=subject_type,
        subject_id=subject_id,
        evidence=evidence,
        confidence=confidence,
        risk_score=risk_score,
        estimated_budget=estimated_budget,
    )
    if not result.allowed:
        raise AutonomyBoundaryViolation(
            f"AUTONOMY_BOUNDARY_ENFORCED: {decision_type!r} → {result.resulting_status} "
            f"({result.reason})"
        )
    return result

"""Integrazione shadow/enforce/disabled del Constitutional Core — MF-CONST-001.

`maybe_validate_constitution` è il punto di contatto tra l'Autonomy Boundary
Layer (`autonomy/authorization.py`) e il Constitutional Core.

Modalità DISABLED:
  - No-op immediato. Ritorna None. Zero overhead.

Modalità SHADOW (default):
  - Chiama il validator costituzionale.
  - Qualsiasi eccezione (file corrotto, schema invalido, errore tecnico) è
    silenziata: il flusso operativo non viene mai interrotto.
  - Registra `constitution.validation.completed` nell'audit log.
  - In caso di violazione, registra `constitution.violation.detected`.
  - In caso di errore tecnico, registra `constitution.configuration.invalid`.

Modalità ENFORCE:
  - Chiama il validator costituzionale normalmente.
  - Se enforcement_action = DENY, solleva `ConstitutionalViolationError`.
  - In V0 nessun principio ha enforcement BLOCKING, quindi ENFORCE è equivalente
    a SHADOW in termini di comportamento produttivo. La struttura è predisposta.

Nessuna duplicazione di audit: questo modulo produce eventi `constitution.*`,
distinti dagli eventi `AUTONOMY_DECISION_*` prodotti dall'authorization service.
"""

from __future__ import annotations

import sqlite3
import uuid

from mercury_foundry import config
from mercury_foundry.audit.logger import log_action
from mercury_foundry.constitutional.models import (
    ConstitutionalValidationRequest,
    ConstitutionalValidationResult,
    ConstitutionalViolationError,
    EnforcementAction,
    ValidationStatus,
)
from mercury_foundry.constitutional import registry as _registry_mod
from mercury_foundry.constitutional.registry import ConstitutionRegistry
from mercury_foundry.constitutional.validator import ConstitutionalValidator


def maybe_validate_constitution(
    conn: sqlite3.Connection,
    *,
    organ_key: str,
    decision_type: str,
    authority_mode: str,
    subject_type: str,
    subject_id: str,
    evidence_refs: list[str] | None = None,
    budget_impact: float | None = None,
    risk_level: str | None = None,
    correlation_id: str | None = None,
    metadata: dict | None = None,
) -> ConstitutionalValidationResult | None:
    """Valida la richiesta corrente contro la Costituzione.

    Ritorna ConstitutionalValidationResult in shadow/enforce mode.
    Ritorna None in disabled mode o su errore tecnico in shadow mode.
    """
    mode = config.CONSTITUTIONAL_CORE_MODE

    if mode == "disabled":
        return None

    corr_id = correlation_id or str(uuid.uuid4())

    request = ConstitutionalValidationRequest(
        decision_id=corr_id,
        organ_id=organ_key,
        action_type=decision_type,
        authority_mode=authority_mode,
        evidence_refs=evidence_refs or [],
        budget_impact=budget_impact,
        risk_level=risk_level,
        metadata=metadata or {},
    )

    if mode == "shadow":
        return _shadow_validate(conn, request, corr_id)
    else:  # enforce
        return _enforced_validate(conn, request, corr_id)


# ---------------------------------------------------------------------------
# Shadow
# ---------------------------------------------------------------------------

def _shadow_validate(
    conn: sqlite3.Connection,
    request: ConstitutionalValidationRequest,
    correlation_id: str,
) -> ConstitutionalValidationResult | None:
    """Shadow mode: valida, registra, non blocca mai."""
    try:
        registry = _registry_mod.get_default_registry()
        validator = ConstitutionalValidator(registry)
        result = validator.validate(request)
    except Exception as exc:
        _log_configuration_invalid(conn, request, correlation_id, exc)
        return None

    _log_validation_completed(conn, request, result, correlation_id, mode="shadow")

    if result.violated_principles:
        _log_violation_detected(conn, request, result, correlation_id, mode="shadow")

    return result


# ---------------------------------------------------------------------------
# Enforce
# ---------------------------------------------------------------------------

def _enforced_validate(
    conn: sqlite3.Connection,
    request: ConstitutionalValidationRequest,
    correlation_id: str,
) -> ConstitutionalValidationResult:
    """Enforce mode: valida e solleva ConstitutionalViolationError su DENY."""
    registry = _registry_mod.get_default_registry()
    validator = ConstitutionalValidator(registry)
    result = validator.validate(request)

    _log_validation_completed(conn, request, result, correlation_id, mode="enforce")

    if result.violated_principles:
        _log_violation_detected(conn, request, result, correlation_id, mode="enforce")

    if result.enforcement_action == EnforcementAction.DENY:
        raise ConstitutionalViolationError(
            f"CONSTITUTIONAL_VIOLATION_ENFORCED: {result.explanation}"
        )

    return result


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------

def _log_validation_completed(
    conn: sqlite3.Connection,
    request: ConstitutionalValidationRequest,
    result: ConstitutionalValidationResult,
    correlation_id: str,
    mode: str,
) -> None:
    """Registra un evento `constitution.validation.completed` nell'audit log."""
    try:
        log_action(
            conn,
            entity_type="constitutional",
            entity_id=0,
            action="constitution.validation.completed",
            actor="system",
            payload={
                "event_id": result.validation_id,
                "correlation_id": correlation_id,
                "decision_id": request.decision_id,
                "organ_id": request.organ_id,
                "constitution_version": result.constitution_version,
                "status": result.status.value,
                "enforcement_action": result.enforcement_action.value,
                "evaluated_principles": result.evaluated_principles,
                "passed_principles": result.passed_principles,
                "violated_principles": result.violated_principles,
                "warnings_count": len(result.warnings),
                "mode": mode,
            },
        )
    except Exception:
        pass  # Mai bloccare per un errore di audit


def _log_violation_detected(
    conn: sqlite3.Connection,
    request: ConstitutionalValidationRequest,
    result: ConstitutionalValidationResult,
    correlation_id: str,
    mode: str,
) -> None:
    """Registra un evento `constitution.violation.detected` nell'audit log."""
    try:
        log_action(
            conn,
            entity_type="constitutional",
            entity_id=0,
            action="constitution.violation.detected",
            actor="system",
            payload={
                "event_id": str(uuid.uuid4()),
                "correlation_id": correlation_id,
                "decision_id": request.decision_id,
                "organ_id": request.organ_id,
                "constitution_version": result.constitution_version,
                "principle_ids": result.violated_principles,
                "enforcement_action": result.enforcement_action.value,
                "explanation": result.explanation,
                "mode": mode,
            },
        )
    except Exception:
        pass


def _log_configuration_invalid(
    conn: sqlite3.Connection,
    request: ConstitutionalValidationRequest,
    correlation_id: str,
    exc: Exception,
) -> None:
    """Registra un evento `constitution.configuration.invalid` nell'audit log."""
    try:
        log_action(
            conn,
            entity_type="constitutional",
            entity_id=0,
            action="constitution.configuration.invalid",
            actor="system",
            payload={
                "event_id": str(uuid.uuid4()),
                "correlation_id": correlation_id,
                "organ_id": request.organ_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "mode": "shadow",
            },
        )
    except Exception:
        pass


def log_constitution_loaded(
    conn: sqlite3.Connection,
    registry: ConstitutionRegistry,
    correlation_id: str | None = None,
) -> None:
    """Registra `constitution.loaded` quando il registry viene inizializzato.

    Chiamata esplicitamente dal codice di bootstrap (init_schema / wiring),
    non internamente al registry (che non conosce il DB).
    """
    try:
        log_action(
            conn,
            entity_type="constitutional",
            entity_id=0,
            action="constitution.loaded",
            actor="system",
            payload={
                "event_id": str(uuid.uuid4()),
                "correlation_id": correlation_id or str(uuid.uuid4()),
                "constitution_version": registry.version_string,
                "principles_count": len(registry),
                "active_principles": len(registry.list_active_principles()),
            },
        )
    except Exception:
        pass

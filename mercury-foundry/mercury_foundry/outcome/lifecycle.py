"""State machine per il lifecycle di EconomicOutcomePlan — MF-OUTCOME-001.

Invarianti:
- Ogni transizione passa per apply_outcome_transition().
- Nessuna modifica diretta dello status senza passare per la state machine.
- Record di transizione immutabili (append-only in DB).
- Activation readiness verificata prima di PLANNED → ACTIVE.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from mercury_foundry.outcome.models import (
    EconomicOutcomePlan,
    OutcomeActivationCheck,
    OutcomePlanNotFoundError,
    OutcomeStatus,
    OutcomeTransitionRecord,
    OutcomeVersionConflict,
    _new_id,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Grafo delle transizioni
# ---------------------------------------------------------------------------

ALLOWED_OUTCOME_TRANSITIONS: dict[str, frozenset[str]] = {
    OutcomeStatus.PLANNED.value: frozenset({
        OutcomeStatus.ACTIVE.value,
        OutcomeStatus.ARCHIVED.value,
    }),
    OutcomeStatus.ACTIVE.value: frozenset({
        OutcomeStatus.UNDER_REVIEW.value,
        OutcomeStatus.SUCCEEDED.value,
        OutcomeStatus.FAILED.value,
        OutcomeStatus.STOPPED.value,
        OutcomeStatus.SCALED.value,
    }),
    OutcomeStatus.UNDER_REVIEW.value: frozenset({
        OutcomeStatus.ACTIVE.value,
        OutcomeStatus.STOPPED.value,
        OutcomeStatus.SCALED.value,
    }),
    OutcomeStatus.SCALED.value: frozenset({
        OutcomeStatus.ACTIVE.value,
        OutcomeStatus.STOPPED.value,
        OutcomeStatus.ARCHIVED.value,
    }),
    # Stati terminali
    OutcomeStatus.SUCCEEDED.value: frozenset({OutcomeStatus.ARCHIVED.value}),
    OutcomeStatus.FAILED.value:    frozenset({OutcomeStatus.ARCHIVED.value}),
    OutcomeStatus.STOPPED.value:   frozenset({OutcomeStatus.ARCHIVED.value}),
    OutcomeStatus.ARCHIVED.value:  frozenset(),
}

TERMINAL_OUTCOME_STATUSES: frozenset[str] = frozenset({
    OutcomeStatus.ARCHIVED.value,
})


class OutcomeTransitionError(ValueError):
    """Transizione non consentita dalla state machine."""


def can_outcome_transition(from_status: str, to_status: str) -> bool:
    return to_status in ALLOWED_OUTCOME_TRANSITIONS.get(from_status, frozenset())


def validate_outcome_transition(from_status: str, to_status: str) -> None:
    if not can_outcome_transition(from_status, to_status):
        allowed = sorted(ALLOWED_OUTCOME_TRANSITIONS.get(from_status, frozenset()))
        raise OutcomeTransitionError(
            f"Transizione outcome da {from_status!r} a {to_status!r} non consentita. "
            f"Transizioni valide da {from_status!r}: {allowed}"
        )


# ---------------------------------------------------------------------------
# Activation readiness check
# ---------------------------------------------------------------------------

def check_activation_readiness(plan: EconomicOutcomePlan) -> OutcomeActivationCheck:
    """Verifica se un OutcomePlan soddisfa i requisiti minimi per l'attivazione.

    Nessuna Mission economica può essere attivata senza:
    - outcome plan esistente
    - maximum_cost_minor > 0
    - maximum_duration_seconds > 0
    - termination criteria (kill_deadline + stop_threshold o stop raggiunto tramite policy)
    """
    blockers: list[str] = []
    warnings: list[str] = []

    if plan.maximum_cost_minor <= 0:
        blockers.append(
            f"maximum_cost_minor deve essere > 0 per l'attivazione, "
            f"ricevuto: {plan.maximum_cost_minor}"
        )
    if plan.maximum_duration_seconds <= 0:
        blockers.append(
            f"maximum_duration_seconds deve essere > 0 per l'attivazione, "
            f"ricevuto: {plan.maximum_duration_seconds}"
        )
    # kill_deadline obbligatoria e non già scaduta
    try:
        kd = datetime.fromisoformat(plan.kill_deadline)
        now = datetime.now(timezone.utc)
        if kd.tzinfo is None:
            kd = kd.replace(tzinfo=timezone.utc)
        if kd <= now:
            blockers.append(
                f"kill_deadline già scaduta: {plan.kill_deadline}"
            )
    except (ValueError, TypeError):
        blockers.append(f"kill_deadline non valida: {plan.kill_deadline!r}")

    # Criteria di terminazione: o stop_threshold o minimum_evidence_count > 0
    if plan.stop_threshold is None and plan.minimum_evidence_count == 0:
        warnings.append(
            "nessun stop_threshold né minimum_evidence_count: "
            "la policy STOP potrebbe non avere criteri sufficienti"
        )

    if plan.reversibility == "irreversible" and not plan.rollback_plan:
        warnings.append(
            "piano irreversibile senza rollback_plan: "
            "considerare di specificare un piano di ripristino"
        )

    return OutcomeActivationCheck(
        ready    = len(blockers) == 0,
        blockers = blockers,
        warnings = warnings,
    )


# ---------------------------------------------------------------------------
# apply_outcome_transition
# ---------------------------------------------------------------------------

def apply_outcome_transition(
    conn: sqlite3.Connection,
    *,
    outcome_plan_id: str,
    current_status: str,
    current_version: int,
    to_status: str,
    requested_by: str,
    reason: str,
    correlation_id: str,
    decision_id: str | None = None,
    metadata: dict | None = None,
) -> OutcomeTransitionRecord:
    """Applica una transizione di status a un OutcomePlan.

    Utilizza optimistic locking su `version`.
    Il record di transizione è immutabile (append-only).
    """
    validate_outcome_transition(current_status, to_status)

    now = _now_iso()
    transition_id = _new_id()
    meta = metadata or {}

    import json
    cur = conn.execute(
        """
        UPDATE economic_outcome_plans
           SET status     = ?,
               version    = version + 1,
               updated_at = ?
         WHERE outcome_plan_id = ?
           AND version         = ?
        """,
        (to_status, now, outcome_plan_id, current_version),
    )
    if cur.rowcount == 0:
        # Disambigua: plan non trovato vs. versione conflitto
        row = conn.execute(
            "SELECT outcome_plan_id FROM economic_outcome_plans WHERE outcome_plan_id = ?",
            (outcome_plan_id,),
        ).fetchone()
        if row is None:
            raise OutcomePlanNotFoundError(outcome_plan_id)
        raise OutcomeVersionConflict(
            f"OutcomePlan {outcome_plan_id}: versione {current_version} non corrisponde"
        )

    conn.execute(
        """
        INSERT INTO outcome_transition_records
               (transition_id, outcome_plan_id, mission_id, from_status, to_status,
                requested_by, requested_at, reason, correlation_id, decision_id,
                metadata_json)
        SELECT ?, ?, mission_id, ?, ?, ?, ?, ?, ?, ?, ?
          FROM economic_outcome_plans
         WHERE outcome_plan_id = ?
        """,
        (
            transition_id, outcome_plan_id,
            current_status, to_status,
            requested_by, now, reason, correlation_id,
            decision_id, json.dumps(meta),
            outcome_plan_id,
        ),
    )
    conn.commit()

    # Recupera mission_id per il record di ritorno
    row = conn.execute(
        "SELECT mission_id FROM economic_outcome_plans WHERE outcome_plan_id = ?",
        (outcome_plan_id,),
    ).fetchone()
    mission_id = row["mission_id"] if row else ""

    return OutcomeTransitionRecord(
        transition_id   = transition_id,
        outcome_plan_id = outcome_plan_id,
        mission_id      = mission_id,
        from_status     = current_status,
        to_status       = to_status,
        requested_by    = requested_by,
        requested_at    = now,
        reason          = reason,
        correlation_id  = correlation_id,
        decision_id     = decision_id,
        metadata        = meta,
    )

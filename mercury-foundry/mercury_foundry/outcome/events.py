"""Emissione eventi del layer Outcome Governance — MF-OUTCOME-001.

Tutti gli eventi outcome.* / resource.* vengono scritti nell'audit log esistente
con entity_type="outcome".

Principio: un'operazione produce esattamente un evento primario.
Non duplicare. Non produrre eventi fuori dal contesto di un'operazione reale.
"""

from __future__ import annotations

import sqlite3
import uuid

from mercury_foundry.audit.logger import log_action


# Tipi di evento supportati
OUTCOME_EVENT_TYPES = frozenset({
    "outcome.plan.created",
    "outcome.plan.invalid",
    "outcome.metric.recorded",
    "outcome.evaluation.completed",
    "outcome.decision.continue",
    "outcome.decision.pause",
    "outcome.decision.stop",
    "outcome.decision.scale_proposed",
    "outcome.decision.review_required",
    "resource.envelope.created",
    "resource.reserved",
    "resource.consumed",
    "resource.released",
    "resource.exhausted",
    "outcome.deadline.approaching",
    "outcome.deadline.exceeded",
})


def emit_outcome_event(
    conn: sqlite3.Connection,
    *,
    action: str,
    entity_id: int,
    mission_id: str,
    actor_id: str,
    correlation_id: str,
    outcome_plan_id: str | None = None,
    decision_type: str | None = None,
    decision_id: str | None = None,
    envelope_id: str | None = None,
    snapshot_id: str | None = None,
    authority_decision_id: str | None = None,
    constitutional_validation_id: str | None = None,
    metadata: dict | None = None,
    commit: bool = True,
) -> int:
    """Scrive un evento outcome.* o resource.* nell'audit log.

    Produce esattamente una riga per chiamata.
    `action` deve essere uno dei OUTCOME_EVENT_TYPES.
    """
    if action not in OUTCOME_EVENT_TYPES:
        raise ValueError(
            f"Tipo evento outcome non riconosciuto: {action!r}. "
            f"Validi: {sorted(OUTCOME_EVENT_TYPES)}"
        )

    payload: dict = {
        "event_id":       str(uuid.uuid4()),
        "correlation_id": correlation_id,
        "mission_id":     mission_id,
        "actor_id":       actor_id,
    }
    if outcome_plan_id is not None:
        payload["outcome_plan_id"] = outcome_plan_id
    if decision_type is not None:
        payload["decision_type"] = decision_type
    if decision_id is not None:
        payload["decision_id"] = decision_id
    if envelope_id is not None:
        payload["envelope_id"] = envelope_id
    if snapshot_id is not None:
        payload["snapshot_id"] = snapshot_id
    if authority_decision_id is not None:
        payload["authority_decision_id"] = authority_decision_id
    if constitutional_validation_id is not None:
        payload["constitutional_validation_id"] = constitutional_validation_id
    if metadata:
        payload["metadata"] = metadata

    return log_action(
        conn,
        entity_type = "outcome",
        entity_id   = entity_id,
        action      = action,
        actor       = actor_id,
        payload     = payload,
        commit      = commit,
    )

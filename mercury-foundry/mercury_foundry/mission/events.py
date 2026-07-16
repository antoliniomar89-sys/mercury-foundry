"""Emissione eventi del Mission Layer — MF-MISSION-001.

Tutti gli eventi mission.* vengono scritti nell'audit log esistente
(`audit/logger.py`) con `entity_type="mission"`.

Principio: un'operazione produce esattamente un evento primario nell'audit log.
Non duplicare. Non produrre eventi fuori dal contesto di un'operazione reale.
"""

from __future__ import annotations

import sqlite3
import uuid

from mercury_foundry.audit.logger import log_action


def emit_mission_event(
    conn: sqlite3.Connection,
    *,
    action: str,
    mission_db_id: int,
    mission_id: str,
    actor_id: str,
    correlation_id: str,
    organ_id: str | None = None,
    origin_type: str | None = None,
    previous_status: str | None = None,
    new_status: str | None = None,
    authority_decision_id: str | None = None,
    constitutional_validation_id: str | None = None,
    evidence_refs: list[str] | None = None,
    metadata: dict | None = None,
    commit: bool = True,
) -> int:
    """Scrive un evento mission.* nell'audit log.

    Produce esattamente una riga per chiamata. Non duplicare la chiamata
    per la stessa operazione.

    Campi comuni obbligatori (event_id, timestamp, correlation_id, mission_id,
    actor_id) sono sempre presenti nel payload.
    """
    payload: dict = {
        "event_id": str(uuid.uuid4()),
        "correlation_id": correlation_id,
        "mission_id": mission_id,
        "actor_id": actor_id,
    }
    if organ_id is not None:
        payload["organ_id"] = organ_id
    if origin_type is not None:
        payload["origin_type"] = origin_type
    if previous_status is not None:
        payload["previous_status"] = previous_status
    if new_status is not None:
        payload["new_status"] = new_status
    if authority_decision_id is not None:
        payload["authority_decision_id"] = authority_decision_id
    if constitutional_validation_id is not None:
        payload["constitutional_validation_id"] = constitutional_validation_id
    if evidence_refs:
        payload["evidence_refs"] = evidence_refs
    if metadata:
        payload["metadata"] = metadata

    return log_action(
        conn,
        entity_type="mission",
        entity_id=mission_db_id,
        action=action,
        actor=actor_id,
        payload=payload,
        commit=commit,
    )

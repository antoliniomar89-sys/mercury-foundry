"""Emissione eventi del Replication Layer — MF-REPL-001.

Tutti gli eventi replication.* vengono scritti nell'audit log esistente
(`audit/logger.py`) con `entity_type="replication"`.

Principio: un'operazione produce esattamente un evento primario.
Non duplicare. Non produrre eventi fuori dal contesto di un'operazione reale.

Catalogo eventi:
  replication.genesis.requested
  replication.genesis.duplicate_detected
  replication.genesis.proposed
  replication.genesis.approved
  replication.genesis.rejected
  replication.genetic_package.created
  replication.genetic_package.invalid
  replication.independence.assessed
  replication.independence.blocked
  replication.product_family.assessed
  replication.gate.completed
  replication.ready_for_provisioning
  replication.aborted
  replication.activation.blocked
  replication.archived
  replication.validation.failed
"""

from __future__ import annotations

import sqlite3
import uuid

from mercury_foundry.audit.logger import log_action


def emit_replication_event(
    conn: sqlite3.Connection,
    *,
    action: str,
    genesis_db_id: int,
    genesis_request_id: str,
    actor_id: str,
    correlation_id: str,
    source_mission_id: str | None = None,
    source_expedition_id: str | None = None,
    proposed_instance_id: str | None = None,
    organ_id: str | None = None,
    authority_decision_id: str | None = None,
    constitutional_validation_id: str | None = None,
    principle_ids: list[str] | None = None,
    result: str | None = None,
    evidence_refs: list[str] | None = None,
    metadata: dict | None = None,
    commit: bool = True,
) -> int:
    """Scrive un evento replication.* nell'audit log.

    Produce esattamente una riga per chiamata.
    """
    payload: dict = {
        "event_id": str(uuid.uuid4()),
        "correlation_id": correlation_id,
        "genesis_request_id": genesis_request_id,
        "actor_id": actor_id,
    }
    if source_mission_id:
        payload["source_mission_id"] = source_mission_id
    if source_expedition_id:
        payload["source_expedition_id"] = source_expedition_id
    if proposed_instance_id:
        payload["proposed_instance_id"] = proposed_instance_id
    if organ_id:
        payload["organ_id"] = organ_id
    if authority_decision_id:
        payload["authority_decision_id"] = authority_decision_id
    if constitutional_validation_id:
        payload["constitutional_validation_id"] = constitutional_validation_id
    if principle_ids:
        payload["principle_ids"] = principle_ids
    if result:
        payload["result"] = result
    if evidence_refs:
        payload["evidence_refs"] = evidence_refs
    if metadata:
        payload["metadata"] = metadata

    return log_action(
        conn,
        entity_type="replication",
        entity_id=genesis_db_id,
        action=action,
        actor=actor_id,
        payload=payload,
        commit=commit,
    )

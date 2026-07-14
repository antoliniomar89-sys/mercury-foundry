"""Audit log append-only: ogni azione rilevante del sistema viene registrata qui.

Nessun componente (Orchestrator, Builder, Evaluator, Approval Gate) esegue
un'azione di stato senza scrivere una riga corrispondente in audit_log.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_action(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    actor: str,
    payload: dict | None = None,
) -> int:
    """Scrive una riga di audit log e ritorna il suo id. Mai aggiornata né cancellata (append-only)."""
    cur = conn.execute(
        """
        INSERT INTO audit_log (entity_type, entity_id, action, actor, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (entity_type, entity_id, action, actor, json.dumps(payload or {}, ensure_ascii=False), _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_audit_log(
    conn: sqlite3.Connection,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM audit_log"
    conditions = []
    params: list = []
    if entity_type is not None:
        conditions.append("entity_type = ?")
        params.append(entity_type)
    if entity_id is not None:
        conditions.append("entity_id = ?")
        params.append(entity_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    return conn.execute(query, params).fetchall()

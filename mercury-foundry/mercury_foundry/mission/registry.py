"""Registry delle Mission — MF-MISSION-001.

Funzioni CRUD per le tabelle `missions` e `mission_transitions`.
Nessun ORM: raw sqlite3, coerente con il resto del repository.

Invarianti:
  - Nessuna cancellazione distruttiva (nemmeno soft delete: uso stato archived).
  - Optimistic locking su `version` per gli aggiornamenti di metadata.
  - Idempotency key UNIQUE: due intake con la stessa chiave restituiscono la
    stessa mission_id senza duplicare.
  - Tutti i timestamp in UTC ISO 8601.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from mercury_foundry.mission.models import (
    Mission,
    MissionIdempotencyReplay,
    MissionNotFoundError,
    MissionVersionConflict,
    _now_iso,
)


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------

def create_mission(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    idempotency_key: str,
    correlation_id: str,
    title: str,
    description: str,
    origin_type: str,
    mission_type: str,
    objective: str,
    created_by: str,
    constitutional_version: str,
    # Campi strutturati (già serializzati come JSON)
    expected_outcomes_json: str = "[]",
    success_criteria_json: str = "[]",
    termination_criteria_json: str = "[]",
    constraints_json: str = "{}",
    budget_json: str = "{}",
    risk_profile_json: str = "{}",
    authority_request_json: str = "{}",
    required_capabilities_json: str = "[]",
    # Enum come stringa
    priority: str = "normal",
    knowledge_scope: str = "mission_local",
    business_scope: str = "exploration",
    # Opzionali
    origin_ref: str | None = None,
    deadline: str | None = None,
    parent_mission_id: str | None = None,
    assigned_organ_id: int | None = None,
    metadata_json: str = "{}",
) -> int:
    """Crea una nuova Mission. Ritorna il rowid (id INTEGER).

    Solleva `MissionIdempotencyReplay` se `idempotency_key` è già presente.
    """
    # Controlla idempotency prima dell'INSERT per dare un errore chiaro
    existing = conn.execute(
        "SELECT mission_id FROM missions WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if existing is not None:
        raise MissionIdempotencyReplay(existing["mission_id"])

    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO missions (
            mission_id, idempotency_key, correlation_id,
            title, description, origin_type, origin_ref, mission_type,
            status, priority, objective,
            expected_outcomes_json, success_criteria_json, termination_criteria_json,
            constraints_json, budget_json, risk_profile_json,
            authority_request_json, required_capabilities_json,
            knowledge_scope, business_scope,
            deadline, parent_mission_id, candidate_business_cell_id,
            constitutional_version, created_by, assigned_organ_id,
            created_at, updated_at, version, metadata_json
        ) VALUES (
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            'draft', ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, NULL,
            ?, ?, ?,
            ?, ?, 1, ?
        )
        """,
        (
            mission_id, idempotency_key, correlation_id,
            title, description, origin_type, origin_ref, mission_type,
            priority, objective,
            expected_outcomes_json, success_criteria_json, termination_criteria_json,
            constraints_json, budget_json, risk_profile_json,
            authority_request_json, required_capabilities_json,
            knowledge_scope, business_scope,
            deadline, parent_mission_id,
            constitutional_version, created_by, assigned_organ_id,
            now, now, metadata_json,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------

def get_mission(conn: sqlite3.Connection, mission_id: str) -> Mission:
    """Carica una Mission per mission_id (UUID). Solleva MissionNotFoundError."""
    row = conn.execute(
        "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
    ).fetchone()
    if row is None:
        raise MissionNotFoundError(f"Mission {mission_id!r} non trovata")
    return Mission.from_row(row)


def get_mission_by_rowid(conn: sqlite3.Connection, rowid: int) -> Mission:
    """Carica una Mission per id INTEGER. Solleva MissionNotFoundError."""
    row = conn.execute("SELECT * FROM missions WHERE id = ?", (rowid,)).fetchone()
    if row is None:
        raise MissionNotFoundError(f"Mission rowid={rowid} non trovata")
    return Mission.from_row(row)


def get_by_idempotency_key(
    conn: sqlite3.Connection, idempotency_key: str
) -> Mission | None:
    """Cerca per idempotency_key. Ritorna None se non trovata."""
    row = conn.execute(
        "SELECT * FROM missions WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    return Mission.from_row(row) if row is not None else None


def list_missions(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[Mission]:
    rows = conn.execute(
        "SELECT * FROM missions ORDER BY id ASC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [Mission.from_row(r) for r in rows]


def list_by_status(conn: sqlite3.Connection, status: str) -> list[Mission]:
    rows = conn.execute(
        "SELECT * FROM missions WHERE status = ? ORDER BY id ASC",
        (status,),
    ).fetchall()
    return [Mission.from_row(r) for r in rows]


def list_by_origin(conn: sqlite3.Connection, origin_type: str) -> list[Mission]:
    rows = conn.execute(
        "SELECT * FROM missions WHERE origin_type = ? ORDER BY id ASC",
        (origin_type,),
    ).fetchall()
    return [Mission.from_row(r) for r in rows]


def list_by_business_scope(conn: sqlite3.Connection, business_scope: str) -> list[Mission]:
    rows = conn.execute(
        "SELECT * FROM missions WHERE business_scope = ? ORDER BY id ASC",
        (business_scope,),
    ).fetchall()
    return [Mission.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# UPDATE (solo metadata — con optimistic locking)
# ---------------------------------------------------------------------------

def update_metadata(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    current_version: int,
    new_metadata: dict,
) -> None:
    """Aggiorna solo il campo metadata_json della Mission.

    Usa optimistic locking: fallisce se la versione non corrisponde.
    Non può essere usata per cambiare status (usare lifecycle.apply_transition).
    """
    cur = conn.execute(
        """
        UPDATE missions
        SET metadata_json = ?, version = version + 1, updated_at = ?
        WHERE mission_id = ? AND version = ?
        """,
        (json.dumps(new_metadata, ensure_ascii=False), _now_iso(), mission_id, current_version),
    )
    if cur.rowcount == 0:
        row = conn.execute(
            "SELECT mission_id, version FROM missions WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()
        if row is None:
            raise MissionNotFoundError(f"Mission {mission_id!r} non trovata")
        raise MissionVersionConflict(
            f"Mission {mission_id!r}: versione attesa {current_version}, "
            f"trovata {row['version']}."
        )
    conn.commit()


# ---------------------------------------------------------------------------
# ARCHIVE (operazione di sola scrittura, non cancellazione)
# ---------------------------------------------------------------------------

def archive_mission(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    current_version: int,
    reason: str,
    requested_by: str,
    correlation_id: str,
) -> None:
    """Porta la Mission in stato archived tramite la state machine.

    Wrapper su `lifecycle.apply_transition` per uso diretto dai servizi.
    Non modifica direttamente status: rispetta la state machine.
    """
    from mercury_foundry.mission.lifecycle import apply_transition

    mission = get_mission(conn, mission_id)
    apply_transition(
        conn,
        mission_id=mission_id,
        current_status=mission.status.value,
        current_version=current_version,
        to_status="archived",
        requested_by=requested_by,
        reason=reason,
        correlation_id=correlation_id,
    )

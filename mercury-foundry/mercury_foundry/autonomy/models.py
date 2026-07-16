"""Modelli CRUD per le tabelle dell'Autonomy Boundary Layer.

Tabelle gestite:
  - organs               — unità decisionali con autorità locale esplicita
  - decision_mandates    — autorità delegata per tipo di decisione
  - decision_records     — log immutabile di ogni decisione presa
  - organ_events         — eventi tra organi (correlation tracking)

Nota: i campi "critici" di decision_records (organ_id, decision_type,
authority_mode, subject_type, subject_id) non hanno funzioni di aggiornamento
in questo modulo — sono append-only per design. Solo lo status può transitare
tramite `transition_decision_record_status`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# organs
# ---------------------------------------------------------------------------

def create_organ(
    conn: sqlite3.Connection,
    *,
    organ_key: str,
    name: str,
    mission: str,
    status: str = "active",
) -> int:
    """Crea un nuovo organo. organ_key deve essere UNIQUE."""
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO organs (organ_key, name, mission, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (organ_key, name, mission, status, now, now),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def get_organ_by_key(conn: sqlite3.Connection, organ_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM organs WHERE organ_key = ?", (organ_key,)
    ).fetchone()


def get_organ_by_id(conn: sqlite3.Connection, organ_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM organs WHERE id = ?", (organ_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# decision_mandates
# ---------------------------------------------------------------------------

def create_mandate(
    conn: sqlite3.Connection,
    *,
    organ_id: int,
    decision_type: str,
    authority_mode: str,
    max_risk_score: float | None = None,
    max_budget: float | None = None,
    requires_evidence: bool = False,
    enabled: bool = True,
) -> int:
    """Crea un mandato per (organ_id, decision_type). Pair deve essere UNIQUE."""
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO decision_mandates
            (organ_id, decision_type, authority_mode, max_risk_score, max_budget,
             requires_evidence, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (organ_id, decision_type, authority_mode, max_risk_score, max_budget,
         1 if requires_evidence else 0, 1 if enabled else 0, now, now),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def get_mandate(
    conn: sqlite3.Connection, organ_id: int, decision_type: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM decision_mandates WHERE organ_id = ? AND decision_type = ?",
        (organ_id, decision_type),
    ).fetchone()


def list_mandates_for_organ(conn: sqlite3.Connection, organ_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM decision_mandates WHERE organ_id = ? ORDER BY id",
        (organ_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# decision_records
# ---------------------------------------------------------------------------

ALLOWED_RECORD_STATUSES = frozenset(
    {"proposed", "authorized", "rejected", "escalated", "executed", "failed", "revoked"}
)

# Transizioni di stato consentite (source → set of valid targets)
ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed":    frozenset({"authorized", "rejected", "escalated", "revoked"}),
    "authorized":  frozenset({"executed", "failed", "revoked"}),
    "escalated":   frozenset({"authorized", "rejected", "revoked"}),
    "executed":    frozenset({"revoked"}),
    "rejected":    frozenset(),  # terminale
    "failed":      frozenset({"revoked"}),
    "revoked":     frozenset(),  # terminale
}


def create_decision_record(
    conn: sqlite3.Connection,
    *,
    organ_id: int,
    decision_type: str,
    authority_mode: str,
    subject_type: str,
    subject_id: str,
    input_evidence: dict | None = None,
    expected_outcome: dict | None = None,
    confidence: float | None = None,
    risk_score: float | None = None,
    status: str,
    reason: str | None = None,
) -> int:
    if status not in ALLOWED_RECORD_STATUSES:
        raise ValueError(f"status non valido per decision_record: {status!r}")
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO decision_records
            (organ_id, decision_type, authority_mode, subject_type, subject_id,
             input_evidence_json, expected_outcome_json, confidence, risk_score,
             status, reason, created_at, executed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            organ_id, decision_type, authority_mode, subject_type, subject_id,
            json.dumps(input_evidence) if input_evidence is not None else None,
            json.dumps(expected_outcome) if expected_outcome is not None else None,
            confidence, risk_score, status, reason, now,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def get_decision_record(conn: sqlite3.Connection, record_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM decision_records WHERE id = ?", (record_id,)
    ).fetchone()


def transition_decision_record_status(
    conn: sqlite3.Connection,
    record_id: int,
    new_status: str,
) -> None:
    """Transita lo status di un decision_record attraverso una transizione consentita.

    Solleva ValueError se la transizione non è consentita dal grafo
    ALLOWED_STATUS_TRANSITIONS. I campi critici (organ_id, decision_type,
    authority_mode, subject_type, subject_id) restano immutabili.
    """
    record = get_decision_record(conn, record_id)
    if record is None:
        raise ValueError(f"decision_record {record_id} non trovato")
    current = record["status"]
    allowed_next = ALLOWED_STATUS_TRANSITIONS.get(current, frozenset())
    if new_status not in allowed_next:
        raise ValueError(
            f"Transizione {current!r} → {new_status!r} non consentita per decision_record {record_id}. "
            f"Transizioni valide da {current!r}: {sorted(allowed_next) or 'nessuna (stato terminale)'}"
        )
    executed_at = _now_iso() if new_status == "executed" else None
    conn.execute(
        "UPDATE decision_records SET status = ?, executed_at = COALESCE(?, executed_at) WHERE id = ?",
        (new_status, executed_at, record_id),
    )
    conn.commit()


def count_orphan_decision_records(conn: sqlite3.Connection) -> int:
    """Conta i decision_records il cui organ_id non esiste in organs."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM decision_records dr
        LEFT JOIN organs o ON dr.organ_id = o.id
        WHERE o.id IS NULL
        """
    ).fetchone()
    return row["n"]


# ---------------------------------------------------------------------------
# organ_events
# ---------------------------------------------------------------------------

ALLOWED_EVENT_STATUSES = frozenset({"pending", "consumed", "failed", "ignored"})


def create_organ_event(
    conn: sqlite3.Connection,
    *,
    source_organ_id: int | None = None,
    target_organ_id: int | None = None,
    event_type: str,
    payload: dict | None = None,
    correlation_id: str,
    causation_id: str | None = None,
    status: str = "pending",
) -> int:
    if status not in ALLOWED_EVENT_STATUSES:
        raise ValueError(f"status non valido per organ_event: {status!r}")
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO organ_events
            (source_organ_id, target_organ_id, event_type, payload_json,
             correlation_id, causation_id, status, created_at, consumed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            source_organ_id, target_organ_id, event_type,
            json.dumps(payload) if payload is not None else None,
            correlation_id, causation_id, status, now,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def get_organ_event(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM organ_events WHERE id = ?", (event_id,)
    ).fetchone()


def list_events_by_correlation(
    conn: sqlite3.Connection, correlation_id: str
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM organ_events WHERE correlation_id = ? ORDER BY id",
        (correlation_id,),
    ).fetchall()

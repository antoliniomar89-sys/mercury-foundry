"""Accesso alle tabelle di stato. Funzioni semplici, nessun ORM (architettura minimale)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- goals -----------------------------------------------------------------

def create_goal(conn: sqlite3.Connection, description: str) -> int:
    cur = conn.execute(
        "INSERT INTO goals (description, status, created_at) VALUES (?, 'open', ?)",
        (description, _now()),
    )
    conn.commit()
    return cur.lastrowid


def update_goal_status(conn: sqlite3.Connection, goal_id: int, status: str) -> None:
    conn.execute("UPDATE goals SET status = ? WHERE id = ?", (status, goal_id))
    conn.commit()


def get_goal(conn: sqlite3.Connection, goal_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()


def list_goals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM goals ORDER BY id ASC").fetchall()


# --- tasks -------------------------------------------------------------------

def create_task(
    conn: sqlite3.Connection, goal_id: int, order_index: int, description: str, assigned_to: str
) -> int:
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO tasks (goal_id, order_index, description, assigned_to, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """,
        (goal_id, order_index, description, assigned_to, now, now),
    )
    conn.commit()
    return cur.lastrowid


def update_task_status(conn: sqlite3.Connection, task_id: int, status: str) -> None:
    conn.execute(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), task_id)
    )
    conn.commit()


def get_tasks_for_goal(conn: sqlite3.Connection, goal_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM tasks WHERE goal_id = ? ORDER BY order_index ASC", (goal_id,)
    ).fetchall()


def get_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


# --- attempts ------------------------------------------------------------------

def create_attempt(conn: sqlite3.Connection, task_id: int, attempt_number: int, phase: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO attempts (task_id, attempt_number, phase, status, started_at)
        VALUES (?, ?, ?, 'running', ?)
        """,
        (task_id, attempt_number, phase, _now()),
    )
    conn.commit()
    return cur.lastrowid


def update_attempt(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    phase: str | None = None,
    status: str | None = None,
    provider_name: str | None = None,
    is_simulated: bool | None = None,
    diff_summary: str | None = None,
    notes: str | None = None,
    close: bool = False,
) -> None:
    fields = []
    params: list = []
    if phase is not None:
        fields.append("phase = ?")
        params.append(phase)
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if provider_name is not None:
        fields.append("provider_name = ?")
        params.append(provider_name)
    if is_simulated is not None:
        fields.append("is_simulated = ?")
        params.append(1 if is_simulated else 0)
    if diff_summary is not None:
        fields.append("diff_summary = ?")
        params.append(diff_summary)
    if notes is not None:
        fields.append("notes = ?")
        params.append(notes)
    if close:
        fields.append("ended_at = ?")
        params.append(_now())
    if not fields:
        return
    params.append(attempt_id)
    conn.execute(f"UPDATE attempts SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()


def get_attempts_for_task(conn: sqlite3.Connection, task_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM attempts WHERE task_id = ? ORDER BY id ASC", (task_id,)
    ).fetchall()


# --- test_results -----------------------------------------------------------------

def record_test_result(
    conn: sqlite3.Connection,
    attempt_id: int,
    test_name: str,
    passed: bool,
    output: str,
    duration_ms: int,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO test_results (attempt_id, test_name, passed, output, duration_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (attempt_id, test_name, 1 if passed else 0, output, duration_ms, _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_test_results_for_attempt(conn: sqlite3.Connection, attempt_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM test_results WHERE attempt_id = ? ORDER BY id ASC", (attempt_id,)
    ).fetchall()


# --- candidates -----------------------------------------------------------------

def create_candidate(
    conn: sqlite3.Connection,
    goal_id: int,
    task_id: int,
    summary: str,
    *,
    provider_name: str,
    is_simulated: bool,
) -> int:
    """Crea una candidate. `provider_name`/`is_simulated` sono obbligatori:

    ogni candidate deve poter essere ispezionata senza ambiguità su chi/cosa
    ha generato la patch verificata, per non scambiare un risultato simulato
    per una generazione AI reale.
    """
    cur = conn.execute(
        """
        INSERT INTO candidates (goal_id, task_id, summary, status, provider_name, is_simulated, created_at)
        VALUES (?, ?, ?, 'pending_review', ?, ?, ?)
        """,
        (goal_id, task_id, summary, provider_name, 1 if is_simulated else 0, _now()),
    )
    conn.commit()
    return cur.lastrowid


def update_candidate_status(conn: sqlite3.Connection, candidate_id: int, status: str) -> None:
    conn.execute("UPDATE candidates SET status = ? WHERE id = ?", (status, candidate_id))
    conn.commit()


def get_candidate(conn: sqlite3.Connection, candidate_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()


def list_candidates(conn: sqlite3.Connection, goal_id: int | None = None) -> list[sqlite3.Row]:
    if goal_id is not None:
        return conn.execute(
            "SELECT * FROM candidates WHERE goal_id = ? ORDER BY id ASC", (goal_id,)
        ).fetchall()
    return conn.execute("SELECT * FROM candidates ORDER BY id ASC").fetchall()


# --- decisions -----------------------------------------------------------------

def create_decision(
    conn: sqlite3.Connection,
    *,
    task_id: int | None,
    candidate_id: int | None,
    decision_type: str,
    actor: str,
    rationale: str | None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO decisions (task_id, candidate_id, decision_type, actor, rationale, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (task_id, candidate_id, decision_type, actor, rationale, _now()),
    )
    conn.commit()
    return cur.lastrowid


def any_candidate_is_simulated(conn: sqlite3.Connection, goal_id: int | None = None) -> bool:
    """True se almeno una candidate (del goal, o di tutte) è marcata come simulata."""
    candidates = list_candidates(conn, goal_id)
    return any(bool(c["is_simulated"]) for c in candidates)


def maybe_complete_goal(conn: sqlite3.Connection, goal_id: int) -> bool:
    """Se tutte le candidate del goal sono approvate, marca il goal come 'done'."""
    candidates = list_candidates(conn, goal_id)
    if candidates and all(c["status"] == "approved" for c in candidates):
        update_goal_status(conn, goal_id, "done")
        return True
    return False

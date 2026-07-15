"""Accesso alle tabelle di stato. Funzioni semplici, nessun ORM (architettura minimale)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- goals -----------------------------------------------------------------

def create_goal(
    conn: sqlite3.Connection, description: str, literal_constraints_json: str | None = None
) -> int:
    cur = conn.execute(
        "INSERT INTO goals (description, status, created_at, literal_constraints_json) "
        "VALUES (?, 'open', ?, ?)",
        (description, _now(), literal_constraints_json),
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
    run_id: str | None = None,
    attempt_id: int | None = None,
    staging_root: str | None = None,
    target_snapshot_hash: str | None = None,
    manifest_json: str | None = None,
) -> int:
    """Crea una candidate. `provider_name`/`is_simulated` sono obbligatori:

    ogni candidate deve poter essere ispezionata senza ambiguità su chi/cosa
    ha generato la patch verificata, per non scambiare un risultato simulato
    per una generazione AI reale.

    `staging_root`/`target_snapshot_hash`/`manifest_json` collegano la
    candidate IMMUTABILMENTE al proprio staging isolato: lo staging non viene
    eliminato quando una candidate nasce `pending_review`, resta a
    disposizione dell'Approval Gate per la promozione o la pulizia dopo un
    reject. `target_snapshot_hash` è l'hash del target REALE al momento della
    creazione dello staging: usato per rilevare un conflitto (target
    cambiato) al momento dell'approvazione.
    """
    cur = conn.execute(
        """
        INSERT INTO candidates (
            goal_id, task_id, summary, status, provider_name, is_simulated, created_at,
            run_id, attempt_id, staging_root, target_snapshot_hash, manifest_json
        )
        VALUES (?, ?, ?, 'pending_review', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            goal_id,
            task_id,
            summary,
            provider_name,
            1 if is_simulated else 0,
            _now(),
            run_id,
            attempt_id,
            staging_root,
            target_snapshot_hash,
            manifest_json,
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_candidate_status(conn: sqlite3.Connection, candidate_id: int, status: str) -> None:
    conn.execute("UPDATE candidates SET status = ? WHERE id = ?", (status, candidate_id))
    conn.commit()


def update_candidate_status_no_commit(conn: sqlite3.Connection, candidate_id: int, status: str) -> None:
    """Come `update_candidate_status`, ma SENZA commit: usata dall'Approval
    Gate (MF-FIX-005) per far partecipare questa scrittura a una singola
    transazione DB coordinata insieme a `create_decision_no_commit` e
    `log_action(..., commit=False)`. Il chiamante è responsabile di
    chiamare `conn.commit()` (successo) o `conn.rollback()` (fallimento)."""
    conn.execute("UPDATE candidates SET status = ? WHERE id = ?", (status, candidate_id))


def set_candidate_backup_root(conn: sqlite3.Connection, candidate_id: int, backup_root: str | None) -> None:
    conn.execute("UPDATE candidates SET backup_root = ? WHERE id = ?", (backup_root, candidate_id))
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


def create_decision_no_commit(
    conn: sqlite3.Connection,
    *,
    task_id: int | None,
    candidate_id: int | None,
    decision_type: str,
    actor: str,
    rationale: str | None,
) -> int:
    """Come `create_decision`, ma SENZA commit — vedi `update_candidate_status_no_commit`."""
    cur = conn.execute(
        """
        INSERT INTO decisions (task_id, candidate_id, decision_type, actor, rationale, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (task_id, candidate_id, decision_type, actor, rationale, _now()),
    )
    return cur.lastrowid


# --- provider_calls ----------------------------------------------------------------

def create_provider_call(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    goal_id: int | None,
    task_id: int | None,
    attempt_id: int | None,
    provider_name: str,
    model: str | None,
    is_simulated: bool,
    operation: str,
    call_number: int,
    requested_at: str,
    responded_at: str | None,
    success: bool,
    usage: dict | None,
    estimated_cost_usd: float | None,
    error_summary: str | None,
    candidate_id: int | None = None,
) -> int:
    """Registra UNA invocazione REALE del provider AI (riuscita o fallita).

    `error_summary` deve arrivare già redatto (nessun segreto/prompt
    completo) — questo livello non applica ulteriore redazione.

    Idempotente rispetto a (run_id, provider_name, call_number): un secondo
    tentativo di registrare la STESSA chiamata (stesso run/provider/numero di
    chiamata) non inserisce una seconda riga, ritorna semplicemente l'id di
    quella già scritta. Questo garantisce "esattamente un record per chiamata
    reale" anche se un chiamante (bug, retry, doppio invio) tentasse di
    persistere lo stesso ProviderCallRecord più di una volta. La tabella resta
    append-only: qui non si aggiorna né cancella mai una riga esistente.
    """
    existing = conn.execute(
        "SELECT id FROM provider_calls WHERE run_id = ? AND provider_name = ? AND call_number = ?",
        (run_id, provider_name, call_number),
    ).fetchone()
    if existing is not None:
        return existing["id"]

    try:
        cur = conn.execute(
            """
            INSERT INTO provider_calls (
                run_id, goal_id, task_id, attempt_id, candidate_id, provider_name, model,
                is_simulated, operation, call_number, requested_at, responded_at, success,
                usage_json, estimated_cost_usd, error_summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                goal_id,
                task_id,
                attempt_id,
                candidate_id,
                provider_name,
                model,
                1 if is_simulated else 0,
                operation,
                call_number,
                requested_at,
                responded_at,
                1 if success else 0,
                json.dumps(usage, ensure_ascii=False) if usage is not None else None,
                estimated_cost_usd,
                error_summary,
                _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        # Race benigna contro l'indice univoco idx_provider_calls_dedup: un'altra
        # chiamata ha scritto la riga tra il SELECT e l'INSERT. Ritorniamo quella
        # riga invece di propagare l'errore o scriverne una seconda.
        conn.rollback()
        row = conn.execute(
            "SELECT id FROM provider_calls WHERE run_id = ? AND provider_name = ? AND call_number = ?",
            (run_id, provider_name, call_number),
        ).fetchone()
        if row is None:
            raise
        return row["id"]


def persist_provider_call_record(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    goal_id: int | None,
    task_id: int | None = None,
    attempt_id: int | None = None,
    candidate_id: int | None = None,
    record,
) -> int | None:
    """Traduce un `ProviderCallRecord` (prodotto da un provider reale) in una
    riga di `provider_calls`, se il provider ne ha effettivamente prodotto uno.

    Punto UNICO di persistenza usato sia da `Orchestrator` (fase PLAN) sia da
    `ExecutionLoop` (fase BUILD), così ogni chiamata reale — riuscita o
    fallita — viene registrata nello stesso modo, con lo stesso `run_id`.
    I provider simulati (FakeModel) lasciano `record` a `None`: in quel caso
    non viene scritta alcuna riga (nessuna chiamata è realmente avvenuta).
    """
    if record is None:
        return None
    return create_provider_call(
        conn,
        run_id=run_id,
        goal_id=goal_id,
        task_id=task_id,
        attempt_id=attempt_id,
        candidate_id=candidate_id,
        provider_name=record.provider_name,
        model=record.model,
        is_simulated=record.is_simulated,
        operation=record.operation,
        call_number=record.call_number,
        requested_at=record.requested_at,
        responded_at=record.responded_at,
        success=record.success,
        usage=record.usage,
        estimated_cost_usd=record.estimated_cost_usd,
        error_summary=record.error_summary,
    )


def associate_candidate_provider_calls(conn: sqlite3.Connection, run_id: str, candidate_id: int) -> None:
    """Collega TUTTE le provider_calls di un run (PLAN, BUILD/PATCH, FIX,
    EVALUATION) alla candidate creata da quel run, in modo APPEND-ONLY:
    inserisce righe in `candidate_provider_calls`, non aggiorna mai
    `provider_calls` (che resta append-only e immutabile riga per riga).

    MF-FIX-005 (gap 3): collega per `run_id`, non per `task_id`. La chiamata
    PLAN (fatta dall'Orchestrator prima che esista un task) ha `task_id`
    NULL: collegare solo per `task_id` la escluderebbe sempre dal totale
    token/costo della candidate, sottostimando la spesa reale del run.
    Collegare per `run_id` include automaticamente anche i tentativi FALLITI
    (attempt 1/2 di un task con retry): sono comunque chiamate reali già
    pagate, e il totale del run deve rendicontarle.

    Idempotente: rilanciare questa funzione per la stessa (run, candidate)
    non produce righe duplicate (INSERT OR IGNORE contro l'indice univoco
    della tabella)."""
    calls = list_provider_calls_for_run(conn, run_id)
    now = _now()
    for call in calls:
        conn.execute(
            """
            INSERT OR IGNORE INTO candidate_provider_calls (candidate_id, provider_call_id, created_at)
            VALUES (?, ?, ?)
            """,
            (candidate_id, call["id"], now),
        )
    conn.commit()


def list_candidate_provider_calls(conn: sqlite3.Connection, candidate_id: int) -> list[sqlite3.Row]:
    """Tutte le provider_calls associate a una candidate, tramite la tabella
    di giunzione append-only (mai tramite provider_calls.candidate_id)."""
    return conn.execute(
        """
        SELECT pc.* FROM provider_calls pc
        JOIN candidate_provider_calls cpc ON cpc.provider_call_id = pc.id
        WHERE cpc.candidate_id = ?
        ORDER BY pc.id ASC
        """,
        (candidate_id,),
    ).fetchall()


def list_provider_calls_for_task(conn: sqlite3.Connection, task_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM provider_calls WHERE task_id = ? ORDER BY id ASC", (task_id,)
    ).fetchall()


def list_provider_calls_for_run(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    """Tutte le provider_calls di un run (PLAN + ogni tentativo BUILD/FIX di
    ogni task del run), incluse quelle con `task_id` NULL (es. PLAN)."""
    return conn.execute(
        "SELECT * FROM provider_calls WHERE run_id = ? ORDER BY id ASC", (run_id,)
    ).fetchall()


def list_provider_calls_for_goal(conn: sqlite3.Connection, goal_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM provider_calls WHERE goal_id = ? ORDER BY id ASC", (goal_id,)
    ).fetchall()


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

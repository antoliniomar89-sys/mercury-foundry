"""Connessione SQLite e inizializzazione dello schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mercury_foundry import config


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Apre (creando se necessario) la connessione al DB di stato e inizializza lo schema."""
    path = Path(db_path) if db_path is not None else config.DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    schema_sql = config.SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)
    conn.commit()
    _migrate_provider_calls_columns(conn)


def _migrate_provider_calls_columns(conn: sqlite3.Connection) -> None:
    """Aggiunge in modo idempotente le colonne `run_id`/`operation` a `provider_calls`.

    `CREATE TABLE IF NOT EXISTS` (sopra) non altera una tabella già esistente:
    un DB creato PRIMA di questo task ha `provider_calls` senza queste due
    colonne. Qui le aggiungiamo se mancanti, così sia un DB nuovo sia uno
    esistente arrivano allo stesso schema, senza toccare i dati già presenti.
    """
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(provider_calls)").fetchall()
    }
    if "run_id" not in existing_columns:
        conn.execute("ALTER TABLE provider_calls ADD COLUMN run_id TEXT")
    if "operation" not in existing_columns:
        conn.execute("ALTER TABLE provider_calls ADD COLUMN operation TEXT NOT NULL DEFAULT 'UNKNOWN'")
    conn.commit()
    # L'indice univoco di deduplicazione va (ri)creato dopo che le colonne
    # esistono di sicuro; è idempotente (IF NOT EXISTS).
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_calls_dedup "
        "ON provider_calls(run_id, provider_name, call_number)"
    )
    conn.commit()

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

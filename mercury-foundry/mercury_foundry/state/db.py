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
    _migrate_goals_columns(conn)
    _migrate_candidates_columns(conn)
    _migrate_candidates_approval_revoked(conn)
    _migrate_audit_log_triggers(conn)
    # MF-ARCH-008: seeding idempotente dell'organo pilota FOUNDRY_GOVERNANCE
    # con i 4 mandati iniziali.  Va eseguito DOPO l'executescript che ha già
    # creato le tabelle organs/decision_mandates tramite schema.sql.
    from mercury_foundry.autonomy.seed import seed_foundry_governance  # lazy import: evita circolarità
    seed_foundry_governance(conn)


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


def _migrate_goals_columns(conn: sqlite3.Connection) -> None:
    """Aggiunge in modo idempotente `literal_constraints_json` a `goals`.

    Stesso motivo/pattern di `_migrate_provider_calls_columns`: un DB creato
    prima di questa colonna non la ha, e `CREATE TABLE IF NOT EXISTS` non la
    aggiungerebbe da solo.
    """
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(goals)").fetchall()}
    if "literal_constraints_json" not in existing_columns:
        conn.execute("ALTER TABLE goals ADD COLUMN literal_constraints_json TEXT")
    conn.commit()


def _migrate_audit_log_triggers(conn: sqlite3.Connection) -> None:
    """MF-FIX-007: installa trigger BEFORE UPDATE e BEFORE DELETE su `audit_log`.

    Rende l'append-only-ness dell'audit log un vincolo a livello DB, non solo
    una convenzione applicativa. I trigger usano RAISE(FAIL, ...) per bloccare
    qualsiasi UPDATE o DELETE con un messaggio di errore esplicito.

    Non viene usato `schema.sql` per questi trigger perché `conn.executescript()`
    divide il testo su ogni `;` — anche quelli dentro i blocchi BEGIN…END —
    producendo SQL malformato. `conn.execute()` singola gestisce correttamente
    la struttura multi-statement del trigger.

    La funzione è IDEMPOTENTE: `IF NOT EXISTS` garantisce che una seconda
    esecuzione non generi errori né duplica i trigger."""
    # RAISE(ABORT, ...) annulla l'istruzione corrente e solleva
    # sqlite3.OperationalError in Python — semanticamente corretta per
    # "operazione non permessa su questa tabella". RAISE(FAIL, ...) invece
    # solleva sqlite3.IntegrityError, che è meno intuitivo per un blocco
    # operativo (non è una violazione di integrità referenziale).
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS audit_log_no_update
            BEFORE UPDATE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE is not permitted (MF-FIX-007)');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
            BEFORE DELETE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only: DELETE is not permitted (MF-FIX-007)');
        END
        """
    )
    conn.commit()


def _migrate_candidates_approval_revoked(conn: sqlite3.Connection) -> None:
    """Migrazione idempotente: nessuna modifica di schema reale necessaria per
    `approval_revoked` (SQLite usa TEXT libero per i campi status), ma registra
    nel commento del codice che `approval_revoked` è uno stato valido dal punto
    di vista della Foundry (MF-INCIDENT-001). Questa funzione esegue un CHECK
    di consistenza leggero: verifica che la tabella `decisions` contenga la
    colonna `decision_type` (dove viene registrata `approval_revoke_incident`)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decisions)").fetchall()}
    if "decision_type" not in cols:
        raise RuntimeError(
            "Schema decisions mancante di 'decision_type': impossibile registrare "
            "decisioni di tipo 'approval_revoke_incident'."
        )
    conn.commit()


def _migrate_candidates_columns(conn: sqlite3.Connection) -> None:
    """Aggiunge in modo idempotente le colonne di staging/manifest a `candidates`.

    Stesso pattern delle altre migrazioni in questo file. Queste colonne
    supportano il modello "candidate = riferimento immutabile a uno staging
    isolato + manifest completo", mai popolate scrivendo direttamente sul
    target reale."""
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(candidates)").fetchall()
    }
    new_columns = {
        "run_id": "TEXT",
        "attempt_id": "INTEGER",
        "staging_root": "TEXT",
        "target_snapshot_hash": "TEXT",
        "manifest_json": "TEXT",
        # MF-FIX-005: percorso del backup restorabile del target, creato
        # dall'Approval Gate PRIMA di promuovere una candidate. Persistito
        # (non solo tenuto in memoria) perché deve restare ispezionabile
        # anche dopo un riavvio del processo, se l'approvazione finisce in
        # stato `recovery_required`.
        "backup_root": "TEXT",
    }
    for name, sql_type in new_columns.items():
        if name not in existing_columns:
            conn.execute(f"ALTER TABLE candidates ADD COLUMN {name} {sql_type}")
    conn.commit()

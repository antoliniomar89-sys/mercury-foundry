"""MF-FIX-007 — Test trigger append-only su audit_log.

Verifica che:
1. INSERT su audit_log sia sempre consentito.
2. UPDATE su audit_log sia bloccato con errore esplicito (trigger DB).
3. DELETE su audit_log sia bloccato con errore esplicito (trigger DB).
4. La migrazione dei trigger sia idempotente (eseguibile N volte senza errore).
5. Un DB pre-esistente (senza trigger) venga aggiornato correttamente.

Tutti i test usano tmp_path — nessuna scrittura sul DB di produzione.

Nota: RAISE(ABORT, ...) in un trigger SQLite solleva sqlite3.OperationalError
o sqlite3.IntegrityError a seconda della versione di Python/libsqlite3. Il test
verifica entrambi i tipi — ciò che conta è il messaggio d'errore, non la classe.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# Tuple di eccezioni accettabili da un trigger SQLite RAISE(ABORT/FAIL).
_TRIGGER_ERRORS = (sqlite3.OperationalError, sqlite3.IntegrityError)

from mercury_foundry.audit.logger import log_action, list_audit_log
from mercury_foundry.state import db
from mercury_foundry.state.db import _migrate_audit_log_triggers
from mercury_foundry.wiring import build_foundry


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _fresh_conn(tmp_path: Path) -> sqlite3.Connection:
    """Apre una connessione fresca con schema e trigger installati."""
    return db.connect(tmp_path / "mf.db")


def _triggers_installed(conn: sqlite3.Connection) -> tuple[bool, bool]:
    """Ritorna (update_trigger_present, delete_trigger_present)."""
    triggers = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='audit_log'"
        ).fetchall()
    }
    return ("audit_log_no_update" in triggers, "audit_log_no_delete" in triggers)


# ---------------------------------------------------------------------------
# Test 1 — INSERT sempre consentito
# ---------------------------------------------------------------------------

def test_audit_log_insert_allowed(tmp_path):
    """INSERT su audit_log deve funzionare normalmente con i trigger installati."""
    conn = _fresh_conn(tmp_path)
    update_ok, delete_ok = _triggers_installed(conn)
    assert update_ok and delete_ok, "I trigger devono essere installati prima del test"

    row_id = log_action(
        conn,
        entity_type="goal",
        entity_id=1,
        action="TEST_INSERT_ALLOWED",
        actor="system",
        payload={"note": "inserimento di prova"},
    )
    assert isinstance(row_id, int) and row_id > 0

    rows = list_audit_log(conn, limit=100)
    actions = [r["action"] for r in rows]
    assert "TEST_INSERT_ALLOWED" in actions, "La riga inserita deve essere leggibile"


# ---------------------------------------------------------------------------
# Test 2 — UPDATE bloccato
# ---------------------------------------------------------------------------

def test_audit_log_update_blocked(tmp_path):
    """UPDATE su audit_log deve fallire con OperationalError (trigger BEFORE UPDATE)."""
    conn = _fresh_conn(tmp_path)
    log_action(conn, entity_type="goal", entity_id=1, action="ORIGINAL_ACTION", actor="system")

    with pytest.raises(_TRIGGER_ERRORS) as exc_info:
        conn.execute("UPDATE audit_log SET action = 'TAMPERED' WHERE action = 'ORIGINAL_ACTION'")

    msg = str(exc_info.value).lower()
    assert "append-only" in msg or "not permitted" in msg or "update" in msg, (
        f"Messaggio di errore non chiaro: {exc_info.value}"
    )

    # Verifica che la riga sia rimasta invariata
    rows = list_audit_log(conn, limit=100)
    actions = [r["action"] for r in rows]
    assert "ORIGINAL_ACTION" in actions, "La riga originale non deve essere modificata"
    assert "TAMPERED" not in actions, "Il tentativo di UPDATE non deve avere avuto effetto"


# ---------------------------------------------------------------------------
# Test 3 — DELETE bloccato
# ---------------------------------------------------------------------------

def test_audit_log_delete_blocked(tmp_path):
    """DELETE su audit_log deve fallire con OperationalError (trigger BEFORE DELETE)."""
    conn = _fresh_conn(tmp_path)
    log_action(conn, entity_type="goal", entity_id=1, action="ROW_TO_DELETE", actor="system")

    rows_before = list_audit_log(conn, limit=100)
    count_before = len(rows_before)

    with pytest.raises(_TRIGGER_ERRORS) as exc_info:
        conn.execute("DELETE FROM audit_log WHERE action = 'ROW_TO_DELETE'")

    msg = str(exc_info.value).lower()
    assert "append-only" in msg or "not permitted" in msg or "delete" in msg, (
        f"Messaggio di errore non chiaro: {exc_info.value}"
    )

    rows_after = list_audit_log(conn, limit=100)
    assert len(rows_after) == count_before, "Il conteggio delle righe non deve cambiare dopo un DELETE bloccato"
    actions_after = [r["action"] for r in rows_after]
    assert "ROW_TO_DELETE" in actions_after, "La riga non deve essere stata cancellata"


# ---------------------------------------------------------------------------
# Test 4 — Migrazione idempotente (eseguibile due volte senza errore)
# ---------------------------------------------------------------------------

def test_audit_log_trigger_migration_is_idempotent(tmp_path):
    """_migrate_audit_log_triggers è idempotente: eseguibile N volte senza errore."""
    conn = _fresh_conn(tmp_path)

    # I trigger sono già stati installati da init_schema (dentro _fresh_conn).
    # Eseguiamo la migrazione altre 3 volte: non deve sollevare eccezioni.
    for _ in range(3):
        _migrate_audit_log_triggers(conn)

    update_ok, delete_ok = _triggers_installed(conn)
    assert update_ok, "Trigger audit_log_no_update non presente dopo N migrazioni"
    assert delete_ok, "Trigger audit_log_no_delete non presente dopo N migrazioni"


# ---------------------------------------------------------------------------
# Test 5 — DB pre-esistente aggiornato correttamente
# ---------------------------------------------------------------------------

def test_audit_log_triggers_installed_on_preexisting_db(tmp_path):
    """Un DB pre-esistente privo di trigger viene aggiornato da _migrate_audit_log_triggers."""
    db_path = tmp_path / "legacy.db"

    # Crea un DB con schema base ma SENZA trigger (simulando un DB creato prima di MF-FIX-007).
    from mercury_foundry import config
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.row_factory = sqlite3.Row
    raw_conn.executescript(config.SCHEMA_PATH.read_text())
    raw_conn.commit()

    # Verifica: i trigger non esistono ancora.
    pre_triggers = {
        row["name"]
        for row in raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='audit_log'"
        ).fetchall()
    }
    assert "audit_log_no_update" not in pre_triggers
    assert "audit_log_no_delete" not in pre_triggers

    # Chiudi e riapri tramite db.connect (che esegue la migrazione completa).
    raw_conn.close()
    migrated_conn = db.connect(db_path)

    update_ok, delete_ok = _triggers_installed(migrated_conn)
    assert update_ok, "Trigger audit_log_no_update non installato sul DB pre-esistente"
    assert delete_ok, "Trigger audit_log_no_delete non installato sul DB pre-esistente"

    # Verifica funzionale: INSERT OK, UPDATE bloccato, DELETE bloccato.
    log_action(migrated_conn, entity_type="goal", entity_id=1, action="POST_MIGRATION_INSERT", actor="system")
    with pytest.raises(_TRIGGER_ERRORS):
        migrated_conn.execute("UPDATE audit_log SET action='X' WHERE 1=1")
    with pytest.raises(_TRIGGER_ERRORS):
        migrated_conn.execute("DELETE FROM audit_log WHERE 1=1")

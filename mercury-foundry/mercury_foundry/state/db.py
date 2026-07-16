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
    # MF-MISSION-001: seeding idempotente di MISSION_CONTROL con i 9 mandati
    # iniziali. Va eseguito DOPO seed_foundry_governance (dipende dalla tabella
    # organs già popolata e da decision_mandates già presente).
    _migrate_mission_indexes(conn)
    from mercury_foundry.mission.seed import seed_mission_control  # lazy import
    seed_mission_control(conn)
    # MF-REPL-001: seeding idempotente di REPLICATION_GOVERNANCE con i 8 mandati
    # iniziali. Va eseguito DOPO seed_mission_control.
    _migrate_replication_indexes(conn)
    from mercury_foundry.replication.seed import seed_replication_governance  # lazy import
    seed_replication_governance(conn)
    # MF-OUTCOME-001: seeding idempotente di ECONOMIC_GOVERNANCE con gli 8 mandati
    # iniziali. Va eseguito DOPO seed_replication_governance.
    _migrate_outcome_indexes(conn)
    from mercury_foundry.outcome.seed import seed_economic_governance  # lazy import
    seed_economic_governance(conn)
    # MF-ECO-001: persistenza reservations + migrazione budget a minor units.
    # Deve girare DOPO seed_economic_governance (le tabelle outcome sono già presenti).
    _migrate_resource_reservations_indexes(conn)
    _migrate_mission_budget_to_minor(conn)


def _migrate_resource_reservations_indexes(conn: sqlite3.Connection) -> None:
    """Crea indici idempotenti per resource_reservations (MF-ECO-001)."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_reservations_envelope_id "
        "ON resource_reservations(envelope_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_reservations_mission_id "
        "ON resource_reservations(mission_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_reservations_status "
        "ON resource_reservations(status)"
    )
    conn.commit()


def _migrate_mission_budget_to_minor(conn: sqlite3.Connection) -> None:
    """Migra budget_json esistenti da float EUR a integer minor units (MF-ECO-001).

    Strategia:
    - DB nuovi: budget_json già emesso da MissionBudget.to_dict() include
      *_minor fields → nessuna conversione necessaria.
    - DB esistenti: budget_json con solo float (approved_amount, ecc.)
      → converti con Decimal, scrivi aggiornati includendo i *_minor fields.
    - Idempotente: se *_minor già presenti, non modifica.
    - Fail-esplicito: se il JSON non è parsabile, solleva RuntimeError.
    - Nessuna perdita di dati: i float originali vengono conservati nel JSON.
    """
    import json
    from decimal import ROUND_HALF_UP, Decimal

    def _eur_to_minor(v: float | int | str) -> int:
        """Converti EUR float → minor units via Decimal (deterministica)."""
        d = Decimal(str(v))
        return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    rows = conn.execute("SELECT mission_id, budget_json FROM missions").fetchall()
    for row in rows:
        mid = row["mission_id"]
        raw = row["budget_json"] or "{}"
        try:
            d = json.loads(raw)
        except Exception as exc:
            raise RuntimeError(
                f"budget_json non parsabile per mission_id={mid}: {exc}"
            ) from exc

        # Se i campi *_minor sono già presenti → idempotente, non modifica
        if "approved_amount_minor" in d:
            continue

        # Converti float → minor
        def _minor(key: str, default: float = 0.0) -> int:
            v = d.get(key, default)
            if v is None:
                return 0
            return _eur_to_minor(v)

        def _limit_minor(key: str) -> int | None:
            v = d.get(key)
            if v is None:
                return None
            return _eur_to_minor(v)

        d["approved_amount_minor"]        = _minor("approved_amount")
        d["committed_amount_minor"]       = _minor("committed_amount")
        d["spent_amount_minor"]           = _minor("spent_amount")
        d["compute_limit_minor"]          = _limit_minor("compute_limit")
        d["external_service_limit_minor"] = _limit_minor("external_service_limit")
        d["marketing_limit_minor"]        = _limit_minor("marketing_limit")
        d["human_service_limit_minor"]    = _limit_minor("human_service_limit")

        try:
            conn.execute(
                "UPDATE missions SET budget_json = ? WHERE mission_id = ?",
                (json.dumps(d), mid),
            )
        except Exception as exc:
            raise RuntimeError(
                f"Impossibile aggiornare budget_json per mission_id={mid}: {exc}"
            ) from exc

    conn.commit()


def _migrate_outcome_indexes(conn: sqlite3.Connection) -> None:
    """Crea indici idempotenti per le tabelle Outcome (MF-OUTCOME-001)."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_plans_mission_id "
        "ON economic_outcome_plans(mission_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_plans_status "
        "ON economic_outcome_plans(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_plans_priority "
        "ON economic_outcome_plans(priority_class)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_snapshots_plan_id "
        "ON outcome_metric_snapshots(outcome_plan_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_envelopes_mission_id "
        "ON resource_envelopes(mission_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_consumptions_envelope_id "
        "ON resource_consumptions(envelope_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_decisions_plan_id "
        "ON outcome_decisions(outcome_plan_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_decisions_decided_at "
        "ON outcome_decisions(decided_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_transitions_plan_id "
        "ON outcome_transition_records(outcome_plan_id)"
    )
    conn.commit()


def _migrate_replication_indexes(conn: sqlite3.Connection) -> None:
    """Crea indici idempotenti per le tabelle Replication (MF-REPL-001)."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_genesis_requests_status "
        "ON dedicated_mercury_genesis_requests(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_genesis_requests_source_mission "
        "ON dedicated_mercury_genesis_requests(source_mission_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_genesis_transitions_request_id "
        "ON dedicated_mercury_genesis_transitions(genesis_request_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_genetic_packages_genesis_id "
        "ON mercury_genetic_packages(genesis_request_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_independence_contracts_genesis_id "
        "ON dedicated_mercury_independence_contracts(genesis_request_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_family_assessments_genesis_id "
        "ON product_family_assessments(genesis_request_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gate_results_genesis_id "
        "ON replication_gate_results(genesis_request_id)"
    )
    conn.commit()


def _migrate_mission_indexes(conn: sqlite3.Connection) -> None:
    """Crea indici idempotenti per le tabelle Mission (MF-MISSION-001).

    Le tabelle missions/mission_transitions sono create da schema.sql via
    CREATE TABLE IF NOT EXISTS (idempotente). Gli indici aggiuntivi vengono
    creati qui perché schema.sql non supporta trigger (stessa convenzione
    adottata per audit_log_triggers).
    """
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_missions_status "
        "ON missions(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_missions_origin_type "
        "ON missions(origin_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_missions_business_scope "
        "ON missions(business_scope)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_missions_correlation_id "
        "ON missions(correlation_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mission_transitions_mission_id "
        "ON mission_transitions(mission_id)"
    )
    conn.commit()


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

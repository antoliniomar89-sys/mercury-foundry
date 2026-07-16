"""Diagnostica di sistema ("doctor") — nessuna dipendenza dal ciclo di esecuzione.

Ispeziona lo stato reale dell'installazione (Python, DB, sandbox, provider AI,
disponibilità di pytest, configurazione) e produce un report con un unico
stato complessivo finale: READY_SIMULATED | READY_REAL | NOT_READY.

Questo modulo non scrive mai in `target_project/` né avvia un ciclo di
esecuzione: fa controlli di sola lettura più una scrittura/lettura di prova
isolata e temporanea, per verificare l'isolamento della sandbox.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mercury_foundry import config
from mercury_foundry.ai.provider_factory import (
    ProviderUnavailableError,
    get_provider,
    resolve_provider_name,
)
from mercury_foundry.sandbox.workspace import SandboxViolation, Workspace
from mercury_foundry.state.db import init_schema

MIN_PYTHON = (3, 10)

EXPECTED_TABLES = {
    "goals",
    "tasks",
    "attempts",
    "test_results",
    "decisions",
    "candidates",
    "audit_log",
}

# MF-ARCH-008: tabelle dell'Autonomy Boundary Layer
AUTONOMY_TABLES = {
    "organs",
    "decision_mandates",
    "decision_records",
    "organ_events",
}

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"

OVERALL_READY_SIMULATED = "READY_SIMULATED"
OVERALL_READY_REAL = "READY_REAL"
OVERALL_NOT_READY = "NOT_READY"
OVERALL_READY_SHADOW         = "READY_SHADOW"          # MF-ARCH-008: autonomy boundary attivo in shadow mode
OVERALL_READY_MISSION_SHADOW = "READY_MISSION_SHADOW"  # MF-MISSION-001: mission layer attivo in shadow mode
OVERALL_READY_REPLICATION_CONTRACT_SHADOW = "READY_REPLICATION_CONTRACT_SHADOW"  # MF-REPL-001
OVERALL_READY_OUTCOME_SHADOW = "READY_OUTCOME_SHADOW"  # MF-OUTCOME-001

# MF-MISSION-001: tabelle del Mission Layer
MISSION_TABLES = {
    "missions",
    "mission_transitions",
}

# MF-REPL-001: tabelle del Replication Layer
REPLICATION_TABLES = {
    "dedicated_mercury_genesis_requests",
    "dedicated_mercury_genesis_transitions",
    "mercury_genetic_packages",
    "dedicated_mercury_independence_contracts",
    "product_family_assessments",
    "replication_gate_results",
}

# MF-OUTCOME-001: tabelle dell'Economic Outcome Governance
OUTCOME_TABLES = {
    "economic_outcome_plans",
    "outcome_metric_snapshots",
    "resource_envelopes",
    "resource_consumptions",
    "outcome_decisions",
    "outcome_transition_records",
}


@dataclass
class CheckResult:
    name: str
    status: str  # ok | warning | error
    detail: str


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)
    overall_status: str = OVERALL_NOT_READY

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(CheckResult(name=name, status=status, detail=detail))

    def has_errors(self) -> bool:
        return any(c.status == STATUS_ERROR for c in self.checks)

    def render(self) -> str:
        lines = ["Mercury Foundry — DOCTOR REPORT", ""]
        for c in self.checks:
            marker = {"ok": "OK", "warning": "WARN", "error": "ERROR"}[c.status]
            lines.append(f"[{marker:5}] {c.name}: {c.detail}")
        lines.append("")
        lines.append(f"OVERALL STATUS: {self.overall_status}")
        return "\n".join(lines)


def run_doctor(
    *,
    db_path: Path | str | None = None,
    sandbox_root: Path | str | None = None,
    provider_name: str | None = None,
) -> DoctorReport:
    report = DoctorReport()

    _check_python_runtime(report)
    _check_test_command_availability(report)
    _check_database(report, db_path)
    _check_sandbox(report, sandbox_root)
    provider_is_simulated = _check_provider(report, provider_name)
    _check_attempt_limit(report)
    _check_approval_gate(report)
    _check_audit_log_module(report)
    autonomy_ok = _check_autonomy_boundary(report, db_path)
    mission_ok = _check_mission_layer(report, db_path)      # MF-MISSION-001
    replication_ok = _check_replication_layer(report, db_path)  # MF-REPL-001
    outcome_ok = _check_outcome_layer(report, db_path)          # MF-OUTCOME-001

    report.overall_status = _compute_overall_status(
        report, provider_is_simulated, autonomy_ok, mission_ok, replication_ok, outcome_ok
    )
    return report


def _check_python_runtime(report: DoctorReport) -> None:
    current = sys.version_info[:2]
    if current >= MIN_PYTHON:
        report.add(
            "python_runtime",
            STATUS_OK,
            f"Python {sys.version.split()[0]} (>= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} richiesto)",
        )
    else:
        report.add(
            "python_runtime",
            STATUS_ERROR,
            f"Python {sys.version.split()[0]} è inferiore al minimo richiesto "
            f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
        )


def _check_test_command_availability(report: DoctorReport) -> None:
    python_exe = shutil.which("python3") or shutil.which("python")
    if python_exe is None:
        report.add("test_command", STATUS_ERROR, "Nessun eseguibile python3/python trovato in PATH")
        return

    try:
        import pytest as _pytest  # noqa: F401

        report.add(
            "test_command",
            STATUS_OK,
            f"pytest importabile (versione {_pytest.__version__}); eseguibile: {python_exe}",
        )
    except ImportError:
        report.add(
            "test_command",
            STATUS_ERROR,
            "pytest non è importabile nell'ambiente corrente: l'Evaluator non potrebbe "
            "eseguire test reali",
        )


def _check_database(report: DoctorReport, db_path: Path | str | None) -> None:
    path = Path(db_path) if db_path is not None else config.DEFAULT_DB_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        report.add("database", STATUS_ERROR, f"Impossibile creare la cartella del DB {path.parent}: {exc}")
        return

    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        # Riusa lo stesso init (schema + migrazioni idempotenti, es. le colonne
        # run_id/operation di provider_calls) usato da `state.db.connect`,
        # invece di duplicare qui solo l'executescript: altrimenti un DB
        # esistente pre-migrazione risulterebbe "valido" per doctor ma rotto
        # per il resto dell'app (schema desincronizzato tra i due percorsi).
        init_schema(conn)

        existing_tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        missing = EXPECTED_TABLES - existing_tables
        conn.close()

        if missing:
            report.add(
                "database",
                STATUS_ERROR,
                f"Schema incompleto in {path}: tabelle mancanti {sorted(missing)}",
            )
        else:
            report.add("database", STATUS_OK, f"DB raggiungibile e schema valido: {path}")
    except sqlite3.Error as exc:
        report.add("database", STATUS_ERROR, f"Errore SQLite su {path}: {exc}")


def _check_sandbox(report: DoctorReport, sandbox_root: Path | str | None) -> None:
    root = Path(sandbox_root) if sandbox_root is not None else config.TARGET_PROJECT_DIR
    resolved_root = root.resolve()
    resolved_base = config.BASE_DIR.resolve()

    if resolved_root == resolved_base:
        report.add(
            "sandbox_isolation",
            STATUS_ERROR,
            f"La sandbox ({resolved_root}) coincide con la radice del progetto: "
            "isolamento delle scritture compromesso",
        )
        return

    try:
        workspace = Workspace(root)
        probe_name = ".doctor_probe.tmp"
        workspace.write_file(probe_name, "doctor-check\n")
        try:
            workspace.resolve("../doctor_escape.tmp")
            blocked = False
        except SandboxViolation:
            blocked = True
        (workspace.root / probe_name).unlink(missing_ok=True)

        if blocked:
            report.add(
                "sandbox_isolation",
                STATUS_OK,
                f"target_project esistente/creabile in {workspace.root}; path traversal bloccato",
            )
        else:
            report.add(
                "sandbox_isolation",
                STATUS_ERROR,
                f"La sandbox in {workspace.root} non blocca correttamente i path fuori radice",
            )
    except (SandboxViolation, OSError) as exc:
        report.add("sandbox_isolation", STATUS_ERROR, f"Sandbox non utilizzabile in {root}: {exc}")


def _check_provider(report: DoctorReport, provider_name: str | None) -> bool | None:
    """Ritorna True se il provider configurato è simulato, False se reale, None se errore."""
    resolved_name = resolve_provider_name(provider_name)
    try:
        provider = get_provider(provider_name)
    except ProviderUnavailableError as exc:
        report.add(
            "ai_provider",
            STATUS_ERROR,
            f"Provider richiesto ('{resolved_name}') non configurabile in modo sicuro: {exc}",
        )
        return None

    if provider.is_simulated:
        report.add(
            "ai_provider",
            STATUS_WARNING,
            f"Provider attivo: '{provider.name}' — SIMULATO (is_simulated=True). "
            "Piani e patch NON provengono da un vero modello AI.",
        )
    else:
        report.add(
            "ai_provider",
            STATUS_OK,
            f"Provider attivo: '{provider.name}' — reale (is_simulated=False).",
        )
    return provider.is_simulated


def _check_attempt_limit(report: DoctorReport) -> None:
    if config.MAX_ATTEMPTS >= 1:
        report.add(
            "max_attempts",
            STATUS_OK,
            f"Limite massimo tentativi automatici per task: {config.MAX_ATTEMPTS}",
        )
    else:
        report.add(
            "max_attempts",
            STATUS_ERROR,
            f"MAX_ATTEMPTS non valido: {config.MAX_ATTEMPTS} (deve essere >= 1)",
        )


def _check_approval_gate(report: DoctorReport) -> None:
    # Il gate è un meccanismo di codice, non una feature flag disattivabile in
    # V0.1: la sua sola presenza come modulo obbligatorio nel flusso è ciò che
    # verifichiamo qui (nessuna candidate diventa "approved" senza passare da
    # mercury_foundry.approval.gate.approve_candidate).
    try:
        from mercury_foundry.approval import gate  # noqa: F401

        report.add(
            "approval_gate",
            STATUS_OK,
            "Approval Gate presente e obbligatorio: nessuna candidate passa a "
            "'approved' senza un'azione umana esplicita (approve_candidate)",
        )
    except ImportError as exc:
        report.add("approval_gate", STATUS_ERROR, f"Modulo Approval Gate non disponibile: {exc}")


def _check_audit_log_module(report: DoctorReport) -> None:
    try:
        from mercury_foundry.audit import logger  # noqa: F401

        report.add("audit_log", STATUS_OK, "Modulo audit log append-only disponibile")
    except ImportError as exc:
        report.add("audit_log", STATUS_ERROR, f"Modulo audit log non disponibile: {exc}")


def _check_autonomy_boundary(report: DoctorReport, db_path: Path | str | None) -> bool:
    """MF-ARCH-008: verifica il livello di autonomia decisionale (AUTONOMY_BOUNDARY).

    Ritorna True se tutti i controlli passano senza ERROR (la sola presenza
    di WARNING non impedisce READY_SHADOW).
    """
    from mercury_foundry import config as cfg
    from mercury_foundry.state.db import connect

    path = Path(db_path) if db_path is not None else cfg.DEFAULT_DB_PATH

    # -- tabelle presenti --
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        existing = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        missing_auto = AUTONOMY_TABLES - existing
        if missing_auto:
            report.add(
                "autonomy_boundary_tables",
                STATUS_ERROR,
                f"Tabelle autonomy mancanti: {sorted(missing_auto)}",
            )
            conn.close()
            return False
        report.add(
            "autonomy_boundary_tables",
            STATUS_OK,
            f"Tabelle autonomy presenti: {sorted(AUTONOMY_TABLES)}",
        )
    except sqlite3.Error as exc:
        report.add("autonomy_boundary_tables", STATUS_ERROR, f"Errore lettura tabelle: {exc}")
        return False

    # -- feature flag riconosciuta --
    mode = cfg.AUTONOMY_MODE
    if mode not in ("shadow", "enforced"):
        report.add(
            "autonomy_boundary_flag",
            STATUS_ERROR,
            f"MERCURY_AUTONOMY_MODE non valido: {mode!r}",
        )
        conn.close()
        return False
    report.add(
        "autonomy_boundary_flag",
        STATUS_OK,
        f"MERCURY_AUTONOMY_MODE riconosciuta: {mode!r}",
    )

    # -- organo pilota presente --
    from mercury_foundry.autonomy.models import get_organ_by_key, list_mandates_for_organ, count_orphan_decision_records
    organ = get_organ_by_key(conn, "FOUNDRY_GOVERNANCE")
    if organ is None:
        report.add(
            "autonomy_boundary_pilot_organ",
            STATUS_WARNING,
            "Organo pilota FOUNDRY_GOVERNANCE non trovato — eseguire seed_foundry_governance()",
        )
        conn.close()
        return False
    report.add(
        "autonomy_boundary_pilot_organ",
        STATUS_OK,
        f"Organo pilota FOUNDRY_GOVERNANCE presente (id={organ['id']})",
    )

    # -- mandati iniziali presenti (4 attesi) --
    mandates = list_mandates_for_organ(conn, organ["id"])
    n_mandates = len(mandates)
    expected_n = 4
    if n_mandates < expected_n:
        report.add(
            "autonomy_boundary_mandates",
            STATUS_WARNING,
            f"Mandati FOUNDRY_GOVERNANCE: {n_mandates} presenti, {expected_n} attesi",
        )
    else:
        report.add(
            "autonomy_boundary_mandates",
            STATUS_OK,
            f"Mandati FOUNDRY_GOVERNANCE presenti: {n_mandates} (>= {expected_n} attesi)",
        )

    # -- nessun mandato duplicato (garantito da UNIQUE, ma verifichiamo) --
    dup_row = conn.execute(
        """
        SELECT organ_id, decision_type, COUNT(*) AS n
        FROM decision_mandates GROUP BY organ_id, decision_type HAVING n > 1
        """
    ).fetchone()
    if dup_row is not None:
        report.add(
            "autonomy_boundary_no_duplicate_mandates",
            STATUS_ERROR,
            f"Mandati duplicati trovati: organ_id={dup_row['organ_id']}, "
            f"decision_type={dup_row['decision_type']}, count={dup_row['n']}",
        )
        conn.close()
        return False
    report.add(
        "autonomy_boundary_no_duplicate_mandates",
        STATUS_OK,
        "Nessun mandato duplicato (vincolo UNIQUE rispettato)",
    )

    # -- nessun decision_record orfano --
    orphans = count_orphan_decision_records(conn)
    if orphans > 0:
        report.add(
            "autonomy_boundary_no_orphan_records",
            STATUS_WARNING,
            f"{orphans} decision_record orfani (organ_id non esistente)",
        )
    else:
        report.add(
            "autonomy_boundary_no_orphan_records",
            STATUS_OK,
            "Nessun decision_record orfano",
        )

    # -- modalità corrente --
    report.add(
        "autonomy_boundary_mode",
        STATUS_OK if mode == "shadow" else STATUS_WARNING,
        f"Modalità corrente: {mode.upper()} "
        f"({'registra senza bloccare' if mode == 'shadow' else 'applica i mandati'})",
    )

    conn.close()

    # Autonomy ok se nessun ERROR nella sezione autonomy
    autonomy_checks = [
        c for c in report.checks if c.name.startswith("autonomy_boundary")
    ]
    return not any(c.status == STATUS_ERROR for c in autonomy_checks)


def _check_mission_layer(
    report: DoctorReport,
    db_path: Path | str | None = None,
) -> bool:
    """MF-MISSION-001: verifica il Mission Layer."""
    from mercury_foundry.mission.lifecycle import ALLOWED_TRANSITIONS, TERMINAL_STATUSES
    from mercury_foundry.mission.capability_contracts import (
        NullCapabilityProvider, NullKnowledgeProvider,
        NullDiscoveryProvider, NullDeliveryProvider,
    )
    from mercury_foundry.mission.seed import MISSION_CONTROL_KEY, INITIAL_MANDATES
    from mercury_foundry.autonomy.models import get_organ_by_key, list_mandates_for_organ

    path = Path(db_path) if db_path is not None else config.DEFAULT_DB_PATH
    if not path.exists():
        report.add("mission_schema", STATUS_ERROR, "DB non trovato")
        return False

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # -- tabelle Mission presenti --
    existing = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing_tables = MISSION_TABLES - existing
    if missing_tables:
        report.add(
            "mission_schema",
            STATUS_ERROR,
            f"Tabelle Mission mancanti: {sorted(missing_tables)}",
        )
        conn.close()
        return False
    report.add("mission_schema", STATUS_OK, "Tabelle missions e mission_transitions presenti")

    # -- indici presenti --
    idx_names = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_mission%'"
        ).fetchall()
    }
    required_indexes = {
        "idx_missions_status",
        "idx_missions_origin_type",
        "idx_missions_business_scope",
        "idx_missions_correlation_id",
        "idx_mission_transitions_mission_id",
    }
    missing_idx = required_indexes - idx_names
    if missing_idx:
        report.add(
            "mission_indexes",
            STATUS_WARNING,
            f"Indici Mission mancanti: {sorted(missing_idx)}",
        )
    else:
        report.add("mission_indexes", STATUS_OK, "Indici Mission presenti")

    # -- UNIQUE constraint su idempotency_key --
    idx_info = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='missions' AND sql LIKE '%idempotency_key%'"
    ).fetchone()
    # La UNIQUE viene anche come indice automatico — controlla via PRAGMA
    pragma = conn.execute("PRAGMA index_list(missions)").fetchall()
    idem_unique = any("idempotency_key" in str(r["name"]) for r in pragma)
    if not idem_unique:
        # Verifica diretta via info_list
        col_info = conn.execute("PRAGMA table_info(missions)").fetchall()
        report.add(
            "mission_idempotency_constraint",
            STATUS_WARNING,
            "Indice UNIQUE su idempotency_key non rilevato tramite PRAGMA index_list",
        )
    else:
        report.add(
            "mission_idempotency_constraint",
            STATUS_OK,
            "Vincolo UNIQUE su idempotency_key presente",
        )

    # -- MISSION_CONTROL organ presente --
    organ = get_organ_by_key(conn, MISSION_CONTROL_KEY)
    if organ is None:
        report.add(
            "mission_control_organ",
            STATUS_ERROR,
            f"Organo {MISSION_CONTROL_KEY!r} non trovato nel DB",
        )
        conn.close()
        return False
    report.add(
        "mission_control_organ",
        STATUS_OK,
        f"Organo {MISSION_CONTROL_KEY!r} presente (id={organ['id']})",
    )

    # -- mandati MISSION_CONTROL presenti --
    mandates = list_mandates_for_organ(conn, organ["id"])
    mandate_types = {m["decision_type"] for m in mandates}
    expected_types = {dt for dt, _ in INITIAL_MANDATES}
    missing_mandates = expected_types - mandate_types
    if missing_mandates:
        report.add(
            "mission_control_mandates",
            STATUS_ERROR,
            f"Mandati MISSION_CONTROL mancanti: {sorted(missing_mandates)}",
        )
        conn.close()
        return False
    report.add(
        "mission_control_mandates",
        STATUS_OK,
        f"Tutti i {len(expected_types)} mandati MISSION_CONTROL presenti",
    )

    # -- state machine valida (no transizioni illegali in TERMINAL_STATUSES) --
    invalid_from_terminal = [
        s for s in TERMINAL_STATUSES
        if ALLOWED_TRANSITIONS.get(s, frozenset())
    ]
    if invalid_from_terminal:
        report.add(
            "mission_state_machine",
            STATUS_ERROR,
            f"Transizioni uscenti da stati terminali: {invalid_from_terminal}",
        )
        conn.close()
        return False
    report.add(
        "mission_state_machine",
        STATUS_OK,
        f"State machine valida: {len(ALLOWED_TRANSITIONS)} stati, "
        f"{len(TERMINAL_STATUSES)} terminali senza uscite",
    )

    # -- provider default caricabili --
    try:
        NullCapabilityProvider()
        NullKnowledgeProvider()
        NullDiscoveryProvider()
        NullDeliveryProvider()
        report.add("mission_null_providers", STATUS_OK, "Null providers caricabili")
    except Exception as exc:
        report.add("mission_null_providers", STATUS_ERROR, f"Null provider non caricabile: {exc}")
        conn.close()
        return False

    # -- expedition contract disponibile --
    try:
        from mercury_foundry.mission.expedition import ExpeditionRequest, ExpeditionReadinessResult
        report.add(
            "mission_expedition_contract",
            STATUS_OK,
            "ExpeditionRequest e ExpeditionReadinessResult importabili",
        )
    except Exception as exc:
        report.add(
            "mission_expedition_contract",
            STATUS_ERROR,
            f"Expedition contract non importabile: {exc}",
        )
        conn.close()
        return False

    # -- runtime non esecutivo (MISSION_PROMOTE_TO_BUSINESS_CELL forbidden) --
    promote_mandate = next(
        (m for m in mandates if m["decision_type"] == "MISSION_PROMOTE_TO_BUSINESS_CELL"),
        None,
    )
    if promote_mandate and promote_mandate["authority_mode"] == "forbidden":
        report.add(
            "mission_runtime_not_executive",
            STATUS_OK,
            "MISSION_PROMOTE_TO_BUSINESS_CELL è forbidden: nessuna Business Cell creata in V0",
        )
    else:
        report.add(
            "mission_runtime_not_executive",
            STATUS_WARNING,
            "MISSION_PROMOTE_TO_BUSINESS_CELL non è forbidden: verificare configurazione",
        )

    conn.close()

    mission_checks = [c for c in report.checks if c.name.startswith("mission_")]
    return not any(c.status == STATUS_ERROR for c in mission_checks)


def _check_replication_layer(
    report: DoctorReport,
    db_path: Path | str | None = None,
) -> bool:
    """MF-REPL-001: verifica il Replication Layer (Genesis Contract V0)."""
    from mercury_foundry.replication.seed import (
        REPLICATION_GOVERNANCE_KEY,
        INITIAL_MANDATES as REPLICATION_MANDATES,
    )
    from mercury_foundry.autonomy.models import get_organ_by_key, list_mandates_for_organ
    from mercury_foundry.replication.lifecycle import (
        ALLOWED_TRANSITIONS as GENESIS_TRANSITIONS,
        TERMINAL_STATUSES as GENESIS_TERMINAL_STATUSES,
        V0_BLOCKED_TRANSITIONS,
    )

    path = Path(db_path) if db_path is not None else config.DEFAULT_DB_PATH
    if not path.exists():
        report.add("replication_schema", STATUS_ERROR, "DB non trovato")
        return False

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # --- 1. Tabelle presenti ---
    existing = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing_tables = REPLICATION_TABLES - existing
    if missing_tables:
        report.add(
            "replication_schema",
            STATUS_ERROR,
            f"Tabelle Replication mancanti: {sorted(missing_tables)}",
        )
        conn.close()
        return False
    report.add(
        "replication_schema",
        STATUS_OK,
        f"Tutte le {len(REPLICATION_TABLES)} tabelle Replication presenti",
    )

    # --- 2. Indici presenti ---
    idx_names = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_genesis%' OR name LIKE 'idx_genetic%' "
            "OR name LIKE 'idx_independence%' OR name LIKE 'idx_family%' "
            "OR name LIKE 'idx_gate%'"
        ).fetchall()
    }
    required_indexes = {
        "idx_genesis_requests_status",
        "idx_genesis_requests_source_mission",
        "idx_genesis_transitions_request_id",
        "idx_genetic_packages_genesis_id",
        "idx_independence_contracts_genesis_id",
        "idx_family_assessments_genesis_id",
        "idx_gate_results_genesis_id",
    }
    missing_idx = required_indexes - idx_names
    if missing_idx:
        report.add(
            "replication_indexes",
            STATUS_WARNING,
            f"Indici Replication mancanti: {sorted(missing_idx)}",
        )
    else:
        report.add("replication_indexes", STATUS_OK, f"{len(required_indexes)} indici Replication presenti")

    # --- 3. REPLICATION_GOVERNANCE presente ---
    organ = get_organ_by_key(conn, REPLICATION_GOVERNANCE_KEY)
    if organ is None:
        report.add(
            "replication_governance_organ",
            STATUS_ERROR,
            f"Organo {REPLICATION_GOVERNANCE_KEY!r} non trovato nel DB",
        )
        conn.close()
        return False
    report.add(
        "replication_governance_organ",
        STATUS_OK,
        f"Organo {REPLICATION_GOVERNANCE_KEY!r} presente (id={organ['id']})",
    )

    # --- 4. Mandati REPLICATION_GOVERNANCE presenti ---
    mandates = list_mandates_for_organ(conn, organ["id"])
    mandate_types = {m["decision_type"] for m in mandates}
    expected_types = {dt for dt, _ in REPLICATION_MANDATES}
    missing_mandates = expected_types - mandate_types
    if missing_mandates:
        report.add(
            "replication_governance_mandates",
            STATUS_ERROR,
            f"Mandati REPLICATION_GOVERNANCE mancanti: {sorted(missing_mandates)}",
        )
        conn.close()
        return False
    report.add(
        "replication_governance_mandates",
        STATUS_OK,
        f"Tutti i {len(expected_types)} mandati REPLICATION_GOVERNANCE presenti",
    )

    # --- 5. GENESIS_ACTIVATE = forbidden (V0 invariante) ---
    activate_mandate = next(
        (m for m in mandates if m["decision_type"] == "GENESIS_ACTIVATE"),
        None,
    )
    if activate_mandate and activate_mandate["authority_mode"] == "forbidden":
        report.add(
            "replication_activation_forbidden",
            STATUS_OK,
            "GENESIS_ACTIVATE è forbidden: nessuna replica attivata automaticamente in V0",
        )
    else:
        report.add(
            "replication_activation_forbidden",
            STATUS_ERROR,
            "GENESIS_ACTIVATE non è forbidden: violazione dell'invariante V0",
        )
        conn.close()
        return False

    # --- 6. State machine valida ---
    invalid_from_terminal = [
        s for s in GENESIS_TERMINAL_STATUSES
        if GENESIS_TRANSITIONS.get(s, frozenset()) - {s}
    ]
    if invalid_from_terminal:
        report.add(
            "replication_state_machine",
            STATUS_ERROR,
            f"Transizioni uscenti da stati terminali: {invalid_from_terminal}",
        )
        conn.close()
        return False
    report.add(
        "replication_state_machine",
        STATUS_OK,
        f"State machine valida: {len(GENESIS_TRANSITIONS)} stati, "
        f"{len(GENESIS_TERMINAL_STATUSES)} terminali senza uscite non-self",
    )

    # --- 7. V0 blocked transitions presenti ---
    expected_blocked = {
        ("ready_for_provisioning", "provisioning"),
        ("provisioning", "activated"),
    }
    if V0_BLOCKED_TRANSITIONS >= expected_blocked:
        report.add(
            "replication_v0_blocked",
            STATUS_OK,
            f"V0 blocked transitions correttamente definite: {sorted(V0_BLOCKED_TRANSITIONS)}",
        )
    else:
        missing_blocks = expected_blocked - V0_BLOCKED_TRANSITIONS
        report.add(
            "replication_v0_blocked",
            STATUS_ERROR,
            f"V0 blocked transitions mancanti: {sorted(missing_blocks)}",
        )
        conn.close()
        return False

    # --- 8. Feature flag activation disabled ---
    if not config.REPLICATION_ACTIVATION_ENABLED:
        report.add(
            "replication_feature_flags",
            STATUS_OK,
            "REPLICATION_ACTIVATION_ENABLED=False (V0 default corretto)",
        )
    else:
        report.add(
            "replication_feature_flags",
            STATUS_WARNING,
            "REPLICATION_ACTIVATION_ENABLED=True: verificare che provisioning runtime sia pronto",
        )

    # --- 9. GenesisService importabile ---
    try:
        from mercury_foundry.replication.genesis_service import GenesisService
        GenesisService()
        report.add("replication_genesis_service", STATUS_OK, "GenesisService importabile")
    except Exception as exc:
        report.add(
            "replication_genesis_service",
            STATUS_ERROR,
            f"GenesisService non importabile: {exc}",
        )
        conn.close()
        return False

    # --- 10. MotherReplicaFederationContract importabile ---
    try:
        from mercury_foundry.replication.models import MotherReplicaFederationContract
        report.add("replication_federation_contract", STATUS_OK, "MotherReplicaFederationContract importabile")
    except Exception as exc:
        report.add(
            "replication_federation_contract",
            STATUS_ERROR,
            f"MotherReplicaFederationContract non importabile: {exc}",
        )
        conn.close()
        return False

    # --- 11. IndependenceEvaluator importabile ---
    try:
        from mercury_foundry.replication.independence import evaluate_independence
        report.add("replication_independence_evaluator", STATUS_OK, "IndependenceEvaluator importabile")
    except Exception as exc:
        report.add(
            "replication_independence_evaluator",
            STATUS_ERROR,
            f"IndependenceEvaluator non importabile: {exc}",
        )
        conn.close()
        return False

    # --- 12. GeneticPackageBuilder importabile ---
    try:
        from mercury_foundry.replication.genetic_package import build_genetic_package
        report.add("replication_genetic_package_builder", STATUS_OK, "build_genetic_package importabile")
    except Exception as exc:
        report.add(
            "replication_genetic_package_builder",
            STATUS_ERROR,
            f"build_genetic_package non importabile: {exc}",
        )
        conn.close()
        return False

    conn.close()

    replication_checks = [c for c in report.checks if c.name.startswith("replication_")]
    return not any(c.status == STATUS_ERROR for c in replication_checks)


def _check_outcome_layer(
    report: DoctorReport,
    db_path: Path | str | None = None,
) -> bool:
    """Verifica il layer Economic Outcome Governance (MF-OUTCOME-001).

    Ritorna True se tutti i check obbligatori passano.
    """
    path = Path(db_path) if db_path is not None else config.DEFAULT_DB_PATH
    if not path.exists():
        report.add("outcome_schema", STATUS_ERROR, "DB non trovato — impossibile verificare outcome layer")
        return False

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # --- 1. Schema tabelle outcome presenti ---
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    missing = OUTCOME_TABLES - tables
    if missing:
        report.add(
            "outcome_schema",
            STATUS_ERROR,
            f"Tabelle outcome mancanti: {sorted(missing)}",
        )
        conn.close()
        return False
    report.add(
        "outcome_schema",
        STATUS_OK,
        f"Schema outcome presente: {sorted(OUTCOME_TABLES)}",
    )

    # --- 2. Indici su mission_id e status presenti ---
    indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    expected_indexes = {
        "idx_outcome_plans_mission_id",
        "idx_outcome_plans_status",
        "idx_outcome_decisions_plan_id",
        "idx_resource_envelopes_mission_id",
    }
    missing_indexes = expected_indexes - indexes
    if missing_indexes:
        report.add(
            "outcome_indexes",
            STATUS_WARNING,
            f"Indici outcome mancanti (non critici): {sorted(missing_indexes)}",
        )
    else:
        report.add(
            "outcome_indexes",
            STATUS_OK,
            f"Indici outcome presenti: {len(expected_indexes)} indici verificati",
        )

    # --- 3. ECONOMIC_GOVERNANCE organ presente ---
    organ = conn.execute(
        "SELECT id FROM organs WHERE organ_key = 'ECONOMIC_GOVERNANCE'",
    ).fetchone()
    if organ is None:
        report.add("outcome_governance_organ", STATUS_ERROR, "Organo ECONOMIC_GOVERNANCE non trovato")
        conn.close()
        return False
    report.add("outcome_governance_organ", STATUS_OK, "ECONOMIC_GOVERNANCE presente nel DB")
    organ_id = organ["id"]

    # --- 4. Mandati ECONOMIC_GOVERNANCE presenti (8 attesi) ---
    mandates = conn.execute(
        "SELECT decision_type, authority_mode FROM decision_mandates WHERE organ_id = ?",
        (organ_id,),
    ).fetchall()
    mandate_map = {m["decision_type"]: m["authority_mode"] for m in mandates}
    expected_mandates = {
        "OUTCOME_PLAN_CREATE":     "proposal",
        "RESOURCE_ALLOCATE":       "escalation_required",
        "RESOURCE_CONSUME":        "proposal",
        "OUTCOME_EVALUATE":        "proposal",
        "OUTCOME_PAUSE":           "proposal",
        "OUTCOME_STOP":            "escalation_required",
        "OUTCOME_SCALE_PROPOSE":   "proposal",
        "OUTCOME_BUDGET_INCREASE": "forbidden",
    }
    mandate_errors: list[str] = []
    for dt, mode in expected_mandates.items():
        if dt not in mandate_map:
            mandate_errors.append(f"{dt} mancante")
        elif mandate_map[dt] != mode:
            mandate_errors.append(f"{dt}: atteso {mode!r}, trovato {mandate_map[dt]!r}")
    if mandate_errors:
        report.add(
            "outcome_governance_mandates",
            STATUS_ERROR,
            f"Mandati ECONOMIC_GOVERNANCE errati: {mandate_errors}",
        )
        conn.close()
        return False
    report.add(
        "outcome_governance_mandates",
        STATUS_OK,
        f"8 mandati ECONOMIC_GOVERNANCE verificati (OUTCOME_BUDGET_INCREASE=forbidden)",
    )

    # --- 5. Budget increase forbidden (invariante V0) ---
    bi_mandate = mandate_map.get("OUTCOME_BUDGET_INCREASE")
    if bi_mandate == "forbidden":
        report.add(
            "outcome_budget_increase_forbidden",
            STATUS_OK,
            "OUTCOME_BUDGET_INCREASE=forbidden (V0 invariante corretto)",
        )
    else:
        report.add(
            "outcome_budget_increase_forbidden",
            STATUS_ERROR,
            f"OUTCOME_BUDGET_INCREASE deve essere forbidden, trovato: {bi_mandate!r}",
        )
        conn.close()
        return False

    # --- 6. Feature flags: auto_scale e auto_budget_increase disabilitati ---
    if not config.OUTCOME_AUTO_SCALE_ENABLED:
        report.add(
            "outcome_no_auto_scale",
            STATUS_OK,
            "OUTCOME_AUTO_SCALE_ENABLED=False (V0 default corretto)",
        )
    else:
        report.add(
            "outcome_no_auto_scale",
            STATUS_WARNING,
            "OUTCOME_AUTO_SCALE_ENABLED=True: verificare che scale automatico sia intenzionale",
        )

    if not config.OUTCOME_AUTO_BUDGET_INCREASE_ENABLED:
        report.add(
            "outcome_no_auto_budget_increase",
            STATUS_OK,
            "OUTCOME_AUTO_BUDGET_INCREASE_ENABLED=False (V0 default corretto)",
        )
    else:
        report.add(
            "outcome_no_auto_budget_increase",
            STATUS_ERROR,
            "OUTCOME_AUTO_BUDGET_INCREASE_ENABLED=True: violazione invariante V0",
        )
        conn.close()
        return False

    # --- 7. OutcomeScorer importabile ---
    try:
        from mercury_foundry.outcome.scoring import OutcomeScorer
        _s = OutcomeScorer()
        report.add("outcome_scorer", STATUS_OK, "OutcomeScorer importabile e istanziabile")
    except Exception as exc:
        report.add("outcome_scorer", STATUS_ERROR, f"OutcomeScorer non importabile: {exc}")
        conn.close()
        return False

    # --- 8. PolicyEvaluator importabile ---
    try:
        from mercury_foundry.outcome.policy import OutcomePolicyEvaluator
        _p = OutcomePolicyEvaluator()
        report.add("outcome_policy_evaluator", STATUS_OK, "OutcomePolicyEvaluator importabile e istanziabile")
    except Exception as exc:
        report.add("outcome_policy_evaluator", STATUS_ERROR, f"OutcomePolicyEvaluator non importabile: {exc}")
        conn.close()
        return False

    # --- 9. Registry inizializzabile ---
    try:
        from mercury_foundry.outcome.registry import list_outcome_plans
        list_outcome_plans(conn, limit=1)
        report.add("outcome_registry", STATUS_OK, "Registry outcome inizializzabile")
    except Exception as exc:
        report.add("outcome_registry", STATUS_ERROR, f"Registry outcome non funzionante: {exc}")
        conn.close()
        return False

    # --- 10. ResourceAllocator importabile ---
    try:
        from mercury_foundry.outcome.allocator import ResourceAllocator
        _a = ResourceAllocator()
        report.add("outcome_resource_allocator", STATUS_OK, "ResourceAllocator importabile e istanziabile")
    except Exception as exc:
        report.add("outcome_resource_allocator", STATUS_ERROR, f"ResourceAllocator non importabile: {exc}")
        conn.close()
        return False

    # --- 11. Mission integration presente ---
    try:
        from mercury_foundry.outcome.service import OutcomeService
        _svc = OutcomeService()
        report.add("outcome_mission_integration", STATUS_OK, "OutcomeService importabile (Mission integration)")
    except Exception as exc:
        report.add("outcome_mission_integration", STATUS_ERROR, f"OutcomeService non importabile: {exc}")
        conn.close()
        return False

    # --- 12. Constitutional Core raggiungibile ---
    try:
        from mercury_foundry.constitutional.shadow import maybe_validate_constitution
        report.add("outcome_constitutional_core", STATUS_OK, "Constitutional Core raggiungibile da outcome layer")
    except Exception as exc:
        report.add("outcome_constitutional_core", STATUS_ERROR, f"Constitutional Core non raggiungibile: {exc}")
        conn.close()
        return False

    conn.close()

    outcome_checks = [c for c in report.checks if c.name.startswith("outcome_")]
    return not any(c.status == STATUS_ERROR for c in outcome_checks)


def _compute_overall_status(
    report: DoctorReport,
    provider_is_simulated: bool | None,
    autonomy_ok: bool = False,
    mission_ok: bool = False,
    replication_ok: bool = False,
    outcome_ok: bool = False,
) -> str:
    if report.has_errors():
        return OVERALL_NOT_READY
    if provider_is_simulated is None:
        return OVERALL_NOT_READY
    # MF-OUTCOME-001: READY_OUTCOME_SHADOW è il livello più alto
    if autonomy_ok and mission_ok and replication_ok and outcome_ok:
        return OVERALL_READY_OUTCOME_SHADOW
    # MF-REPL-001: READY_REPLICATION_CONTRACT_SHADOW prevale su READY_MISSION_SHADOW
    if autonomy_ok and mission_ok and replication_ok:
        return OVERALL_READY_REPLICATION_CONTRACT_SHADOW
    # MF-MISSION-001: READY_MISSION_SHADOW prevale su READY_SHADOW quando il
    # Mission Layer è correttamente inizializzato.
    if autonomy_ok and mission_ok:
        return OVERALL_READY_MISSION_SHADOW
    # MF-ARCH-008: READY_SHADOW prevale su READY_SIMULATED/READY_REAL
    if autonomy_ok:
        return OVERALL_READY_SHADOW
    return OVERALL_READY_SIMULATED if provider_is_simulated else OVERALL_READY_REAL

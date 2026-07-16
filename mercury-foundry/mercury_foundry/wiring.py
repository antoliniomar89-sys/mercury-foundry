"""Punto unico di composizione: costruisce Orchestrator/Builder/Evaluator/DB.

Usato sia dalla CLI sia dai test, per non duplicare la logica di wiring.

MF-INTEGRATE-001: se ADAPTIVE_VERIFICATION_ENABLED è True, costruisce e
inietta un VerificationRunner in ExecutionLoop. Il VerificationRunner è
opzionale: tutti i chiamanti esistenti che costruiscono ExecutionLoop
direttamente continuano a funzionare invariati.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mercury_foundry import config
from mercury_foundry.agents.builder import Builder
from mercury_foundry.agents.evaluator import Evaluator
from mercury_foundry.ai.provider import AIProvider
from mercury_foundry.ai.provider_factory import get_provider
from mercury_foundry.execution.loop import ExecutionLoop
from mercury_foundry.orchestrator.orchestrator import Orchestrator
from mercury_foundry.sandbox.workspace import Workspace
from mercury_foundry.state import db
from mercury_foundry.testing.runner import TestRunner


@dataclass
class Foundry:
    conn: sqlite3.Connection
    ai_provider: AIProvider
    workspace: Workspace
    orchestrator: Orchestrator
    backup_base_dir: Path


def build_foundry(
    *,
    db_path: Path | str | None = None,
    sandbox_root: Path | str | None = None,
    provider_name: str | None = None,
    staging_base_dir: Path | str | None = None,
    backup_base_dir: Path | str | None = None,
    adaptive_verification: bool | None = None,
) -> Foundry:
    """Costruisce un Foundry completamente cablato.

    Args:
        db_path: percorso del DB SQLite (default: config.DEFAULT_DB_PATH).
        sandbox_root: root del target project (default: config.TARGET_PROJECT_DIR).
        provider_name: nome del provider AI (default: da configurazione).
        staging_base_dir: radice degli staging per-tentativo.
        backup_base_dir: radice dei backup dell'Approval Gate.
        adaptive_verification: True/False per override esplicito del flag
            ADAPTIVE_VERIFICATION_ENABLED. None (default) usa il valore
            dalla configurazione (variabile d'ambiente).
    """
    conn = db.connect(db_path)
    ai_provider = get_provider(provider_name)
    workspace = Workspace(Path(sandbox_root) if sandbox_root is not None else config.TARGET_PROJECT_DIR)

    if staging_base_dir is not None:
        resolved_staging_base_dir = Path(staging_base_dir)
    elif sandbox_root is not None:
        # Un `sandbox_root` custom indica quasi sempre un target isolato (es.
        # `tmp_path` nei test): lo staging va co-locato accanto ad esso,
        # invece che nella cartella condivisa `config.STAGING_BASE_DIR` del
        # progetto reale, per non far trapelare stato tra run/test diversi.
        resolved_staging_base_dir = Path(sandbox_root).resolve().parent / "mf_staging"
    else:
        resolved_staging_base_dir = config.STAGING_BASE_DIR

    if backup_base_dir is not None:
        resolved_backup_base_dir = Path(backup_base_dir)
    elif sandbox_root is not None:
        # Stesso motivo dello staging co-locato: i backup dell'Approval Gate
        # (MF-FIX-005) non devono finire nella cartella condivisa
        # `config.BACKUP_BASE_DIR` del progetto reale quando si usa un
        # target isolato (es. `tmp_path` nei test).
        resolved_backup_base_dir = Path(sandbox_root).resolve().parent / "mf_backups"
    else:
        resolved_backup_base_dir = config.BACKUP_BASE_DIR

    # ----------------------------------------------------------------
    # MF-INTEGRATE-001: VerificationRunner opzionale
    #
    # Costruito solo se l'adaptive verification è abilitata (flag di
    # configurazione o override esplicito). Usa config.BASE_DIR come
    # project_root: le SOURCE_MAPPINGS mappano il codice di mercury_foundry.
    # L'esecuzione effettiva dei test avviene sempre nel staging isolato
    # (via Evaluator.evaluate), non nella project_root del runner.
    # ----------------------------------------------------------------
    _adaptive_enabled = (
        adaptive_verification
        if adaptive_verification is not None
        else config.ADAPTIVE_VERIFICATION_ENABLED
    )
    verification_runner = None
    if _adaptive_enabled:
        try:
            from mercury_foundry.verification.runner import VerificationRunner
            verification_runner = VerificationRunner()
        except Exception:  # noqa: BLE001
            # VerificationRunner è opzionale: se non disponibile il ciclo
            # usa il percorso legacy senza interrompere il boot.
            verification_runner = None

    builder = Builder(ai_provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    execution_loop = ExecutionLoop(
        conn,
        builder,
        evaluator,
        staging_base_dir=resolved_staging_base_dir,
        verification_runner=verification_runner,
    )
    orchestrator = Orchestrator(conn, ai_provider, execution_loop)

    return Foundry(
        conn=conn,
        ai_provider=ai_provider,
        workspace=workspace,
        orchestrator=orchestrator,
        backup_base_dir=resolved_backup_base_dir,
    )

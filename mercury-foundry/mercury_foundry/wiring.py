"""Punto unico di composizione: costruisce Orchestrator/Builder/Evaluator/DB.

Usato sia dalla CLI sia dai test, per non duplicare la logica di wiring.
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


def build_foundry(
    *,
    db_path: Path | str | None = None,
    sandbox_root: Path | str | None = None,
    provider_name: str | None = None,
    staging_base_dir: Path | str | None = None,
) -> Foundry:
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

    builder = Builder(ai_provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    execution_loop = ExecutionLoop(conn, builder, evaluator, staging_base_dir=resolved_staging_base_dir)
    orchestrator = Orchestrator(conn, ai_provider, execution_loop)

    return Foundry(conn=conn, ai_provider=ai_provider, workspace=workspace, orchestrator=orchestrator)

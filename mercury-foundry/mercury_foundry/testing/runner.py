"""Esecuzione REALE dei test (mai simulata) dentro la sandbox del target_project."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from mercury_foundry import config


@dataclass
class TestRunResult:
    passed: bool
    output: str
    returncode: int
    duration_ms: int


class TestRunner:
    def __init__(self, sandbox_root: Path):
        self.sandbox_root = sandbox_root

    def run(self, command: list[str] | None = None, env: dict[str, str] | None = None) -> TestRunResult:
        """Esegue i test reali. Se `command` è fornito (es. da un
        `literal_constraints.exact_test_command`), esegue ESATTAMENTE quel
        comando invece di affidarsi a qualunque file di test scritto dal
        provider — questo è ciò che rende la verifica indipendente da test
        "sempre veri" che il provider potrebbe aver generato.

        `env`, se fornito, aggiunge/sovrascrive variabili sull'ambiente
        corrente (mai una shell: resta un exec diretto di `command`, gli
        eventuali override sono passati a `subprocess.run` via `env=`)."""
        cmd = command if command is not None else ["python3", "-m", "pytest", "-q"]
        run_env = {**os.environ, **env} if env else None
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.sandbox_root),
                capture_output=True,
                text=True,
                timeout=config.TEST_TIMEOUT_SECONDS,
                env=run_env,
            )
            output = proc.stdout + proc.stderr
            returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            output = f"Timeout dopo {config.TEST_TIMEOUT_SECONDS}s eseguendo pytest.\n{exc}"
            returncode = -1
        duration_ms = int((time.monotonic() - start) * 1000)
        return TestRunResult(
            passed=returncode == 0, output=output, returncode=returncode, duration_ms=duration_ms
        )

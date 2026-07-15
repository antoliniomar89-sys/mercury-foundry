"""Esecuzione REALE dei test (mai simulata), sempre dentro uno staging isolato.

L'ambiente del sottoprocesso è costruito da un'allowlist minima (mai una
copia di `os.environ`): vedi `mercury_foundry.sandbox.test_env`.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from mercury_foundry import config
from mercury_foundry.sandbox.test_env import build_sanitized_test_env


@dataclass
class TestRunResult:
    passed: bool
    output: str
    returncode: int
    duration_ms: int


class TestRunner:
    def __init__(self, sandbox_root: Path | None = None):
        # Retro-compatibilità: se fornito, usato come cwd di default quando
        # `run()` non specifica esplicitamente `cwd`. Il chiamante principale
        # (ExecutionLoop) passa sempre `cwd` esplicitamente (lo staging del
        # tentativo corrente), quindi questo default non viene più usato nel
        # percorso normale, ma resta valido per qualunque uso diretto.
        self.sandbox_root = sandbox_root

    def run(
        self,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> TestRunResult:
        """Esegue i test reali dentro `cwd` (o `self.sandbox_root` se non
        specificato). Se `command` è fornito (es. da un
        `literal_constraints.exact_test_command`), esegue ESATTAMENTE quel
        comando invece di affidarsi a qualunque file di test scritto dal
        provider — questo è ciò che rende la verifica indipendente da test
        "sempre veri" che il provider potrebbe aver generato. Mai `shell=True`:
        resta un exec diretto dell'argv risultante.

        `env`, se fornito, aggiunge/sovrascrive variabili sull'ambiente
        sanitizzato costruito per questa esecuzione (mai sull'`os.environ`
        del processo host)."""
        root = Path(cwd) if cwd is not None else self.sandbox_root
        if root is None:
            raise ValueError(
                "TestRunner.run richiede un cwd esplicito (nessun sandbox_root di default configurato)"
            )

        cmd = command if command is not None else ["python3", "-m", "pytest", "-q"]
        run_env = build_sanitized_test_env(
            home_dir=root / ".mf_test_home",
            tmp_dir=root / ".mf_test_tmp",
            extra=env,
        )

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(root),
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

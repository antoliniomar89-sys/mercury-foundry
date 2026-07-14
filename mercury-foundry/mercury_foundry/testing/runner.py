"""Esecuzione REALE dei test (mai simulata) dentro la sandbox del target_project."""

from __future__ import annotations

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

    def run(self) -> TestRunResult:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                ["python3", "-m", "pytest", "-q"],
                cwd=str(self.sandbox_root),
                capture_output=True,
                text=True,
                timeout=config.TEST_TIMEOUT_SECONDS,
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

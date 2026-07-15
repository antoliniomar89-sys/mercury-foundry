"""Evaluator — esegue REALMENTE i test e riporta pass/fail (mai simulato)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mercury_foundry.testing.runner import TestRunner, TestRunResult


@dataclass
class EvalResult:
    passed: bool
    output: str
    duration_ms: int


class Evaluator:
    def __init__(self, test_runner: TestRunner):
        self.test_runner = test_runner

    def evaluate(
        self,
        cwd: Path | None = None,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> EvalResult:
        """Esegue i test in `cwd` (lo staging isolato del tentativo corrente).

        `cwd` è opzionale solo per retro-compatibilità con un `TestRunner`
        costruito con un `sandbox_root` fisso; il chiamante principale
        (ExecutionLoop) lo passa sempre esplicitamente."""
        result: TestRunResult = self.test_runner.run(command=command, env=env, cwd=cwd)
        return EvalResult(passed=result.passed, output=result.output, duration_ms=result.duration_ms)

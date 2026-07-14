"""Evaluator — esegue REALMENTE i test e riporta pass/fail (mai simulato)."""

from __future__ import annotations

from dataclasses import dataclass

from mercury_foundry.testing.runner import TestRunner, TestRunResult


@dataclass
class EvalResult:
    passed: bool
    output: str
    duration_ms: int


class Evaluator:
    def __init__(self, test_runner: TestRunner):
        self.test_runner = test_runner

    def evaluate(self) -> EvalResult:
        result: TestRunResult = self.test_runner.run()
        return EvalResult(passed=result.passed, output=result.output, duration_ms=result.duration_ms)

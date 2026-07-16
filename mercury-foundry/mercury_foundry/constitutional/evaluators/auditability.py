"""Evaluator per CONST-002 — Auditability.

Condizione verificabile:
  - Sempre applicabile.
  - decision_id deve essere non vuoto (dimostra che il sistema di audit è attivo).
  - Se vuoto → violazione.
"""

from __future__ import annotations

from mercury_foundry.constitutional.evaluators.base import PrincipleEvaluator
from mercury_foundry.constitutional.models import (
    ConstitutionalPrinciple,
    ConstitutionalValidationRequest,
    PrincipleEvaluationDetail,
)


class AuditabilityEvaluator(PrincipleEvaluator):

    @property
    def principle_id(self) -> str:
        return "CONST-002"

    def evaluate(
        self,
        principle: ConstitutionalPrinciple,
        request: ConstitutionalValidationRequest,
    ) -> PrincipleEvaluationDetail:
        if not request.decision_id or not request.decision_id.strip():
            return PrincipleEvaluationDetail(
                principle_id=self.principle_id,
                applicable=True,
                passed=False,
                warning=None,
                violation_reason=(
                    "CONST-002: decision_id assente o vuoto. "
                    "Ogni decisione rilevante deve produrre un record ricostruibile."
                ),
                data_missing="decision_id",
            )

        return PrincipleEvaluationDetail(
            principle_id=self.principle_id,
            applicable=True,
            passed=True,
            warning=None,
            violation_reason=None,
            data_missing=None,
        )

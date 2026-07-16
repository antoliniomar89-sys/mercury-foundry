"""Evaluator per CONST-004 — Reversible First.

Condizione verificabile (advisory):
  - Applicabile solo ad azioni autonomous.
  - Se risk_level = "high" e authority_mode = "autonomous" → warning.
  - Non è una violazione in V0 (enforcement=advisory).
  - Se il metadata contiene rollback_plan: qualsiasi stringa non vuota
    soddisfa la best practice.
"""

from __future__ import annotations

from mercury_foundry.constitutional.evaluators.base import PrincipleEvaluator
from mercury_foundry.constitutional.models import (
    ConstitutionalPrinciple,
    ConstitutionalValidationRequest,
    PrincipleEvaluationDetail,
)


class ReversibilityEvaluator(PrincipleEvaluator):

    @property
    def principle_id(self) -> str:
        return "CONST-004"

    def evaluate(
        self,
        principle: ConstitutionalPrinciple,
        request: ConstitutionalValidationRequest,
    ) -> PrincipleEvaluationDetail:
        # Applicabile solo ad azioni autonomous
        if request.authority_mode != "autonomous":
            return PrincipleEvaluationDetail(
                principle_id=self.principle_id,
                applicable=False,
                passed=None,
                warning=None,
                violation_reason=None,
                data_missing=None,
            )

        # Best practice: piano di rollback
        has_rollback_plan = bool(
            request.metadata.get("rollback_plan", "")
        )

        warning = None
        if request.risk_level == "high" and not has_rollback_plan:
            warning = (
                "CONST-004: azione autonoma ad alto rischio senza rollback_plan "
                "nel metadata. Preferire decisioni reversibili prima di assumere "
                "impegni irreversibili."
            )

        return PrincipleEvaluationDetail(
            principle_id=self.principle_id,
            applicable=True,
            passed=True,  # advisory: non è una violazione in V0
            warning=warning,
            violation_reason=None,
            data_missing=None,
        )

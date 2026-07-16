"""Evaluator per CONST-007 — Human Approval for Constitutional Change.

Condizione verificabile (deterministico al 100%):
  - Applicabile quando action_type contiene "constitutional" (case-insensitive).
  - Se authority_mode = "autonomous" → violazione.
  - Nessun agente può attivare autonomamente una modifica costituzionale.

Questo è il principio più facilmente misurabile in modo deterministico:
il solo fatto che action_type contenga "constitutional" e authority_mode
sia "autonomous" è sufficiente per classificare la richiesta come violazione.
"""

from __future__ import annotations

from mercury_foundry.constitutional.evaluators.base import PrincipleEvaluator
from mercury_foundry.constitutional.models import (
    ConstitutionalPrinciple,
    ConstitutionalValidationRequest,
    PrincipleEvaluationDetail,
)


class ConstitutionalChangeProtectionEvaluator(PrincipleEvaluator):

    @property
    def principle_id(self) -> str:
        return "CONST-007"

    def evaluate(
        self,
        principle: ConstitutionalPrinciple,
        request: ConstitutionalValidationRequest,
    ) -> PrincipleEvaluationDetail:
        # Applicabile solo ad azioni di tipo constitutional
        if "constitutional" not in request.action_type.lower():
            return PrincipleEvaluationDetail(
                principle_id=self.principle_id,
                applicable=False,
                passed=None,
                warning=None,
                violation_reason=None,
                data_missing=None,
            )

        if request.authority_mode == "autonomous":
            return PrincipleEvaluationDetail(
                principle_id=self.principle_id,
                applicable=True,
                passed=False,
                warning=None,
                violation_reason=(
                    f"CONST-007: azione '{request.action_type}' di tipo constitutional "
                    "con authority_mode='autonomous'. "
                    "Nessun agente può attivare autonomamente una modifica costituzionale. "
                    "È richiesta approvazione umana esplicita."
                ),
                data_missing=None,
            )

        return PrincipleEvaluationDetail(
            principle_id=self.principle_id,
            applicable=True,
            passed=True,
            warning=None,
            violation_reason=None,
            data_missing=None,
        )

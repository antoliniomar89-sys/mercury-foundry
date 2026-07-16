"""Evaluator per CONST-001 — Evidence Before Investment.

Condizione verificabile:
  - Applicabile solo quando budget_impact > 0.
  - Se evidence_refs è vuoto → violazione (dato presente, evidenza assente).
  - Se budget_impact è None → non applicabile.
"""

from __future__ import annotations

from mercury_foundry.constitutional.evaluators.base import PrincipleEvaluator
from mercury_foundry.constitutional.models import (
    ConstitutionalPrinciple,
    ConstitutionalValidationRequest,
    PrincipleEvaluationDetail,
)


class EvidenceBeforeInvestmentEvaluator(PrincipleEvaluator):

    @property
    def principle_id(self) -> str:
        return "CONST-001"

    def evaluate(
        self,
        principle: ConstitutionalPrinciple,
        request: ConstitutionalValidationRequest,
    ) -> PrincipleEvaluationDetail:
        # Non applicabile se non c'è impatto economico dichiarato
        if request.budget_impact is None or request.budget_impact <= 0:
            return PrincipleEvaluationDetail(
                principle_id=self.principle_id,
                applicable=False,
                passed=None,
                warning=None,
                violation_reason=None,
                data_missing=None,
            )

        # budget_impact > 0: verifica la presenza di evidence
        if not request.evidence_refs:
            return PrincipleEvaluationDetail(
                principle_id=self.principle_id,
                applicable=True,
                passed=False,
                warning=None,
                violation_reason=(
                    f"CONST-001: budget_impact={request.budget_impact} ma "
                    "nessuna evidenza fornita (evidence_refs vuoto). "
                    "Nessun investimento rilevante deve essere autorizzato senza evidenza adeguata."
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

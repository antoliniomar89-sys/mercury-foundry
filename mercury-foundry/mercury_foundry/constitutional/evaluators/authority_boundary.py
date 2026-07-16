"""Evaluator per CONST-003 — Bounded Autonomy.

Condizioni verificabili:
  - Sempre applicabile.
  - authority_mode deve essere uno dei valori riconosciuti dal sistema.
  - organ_id deve essere non vuoto.
  - Violazione se authority_mode è sconosciuto o organ_id è vuoto.
  - Warning se authority_mode è "autonomous" su azioni ad alto rischio.
"""

from __future__ import annotations

from mercury_foundry.constitutional.evaluators.base import PrincipleEvaluator
from mercury_foundry.constitutional.models import (
    ConstitutionalPrinciple,
    ConstitutionalValidationRequest,
    PrincipleEvaluationDetail,
)

_VALID_AUTHORITY_MODES = frozenset({
    "autonomous",
    "proposal",
    "escalation_required",
    "forbidden",
    "unknown",
})


class AuthorityBoundaryEvaluator(PrincipleEvaluator):

    @property
    def principle_id(self) -> str:
        return "CONST-003"

    def evaluate(
        self,
        principle: ConstitutionalPrinciple,
        request: ConstitutionalValidationRequest,
    ) -> PrincipleEvaluationDetail:
        # organ_id obbligatorio
        if not request.organ_id or not request.organ_id.strip():
            return PrincipleEvaluationDetail(
                principle_id=self.principle_id,
                applicable=True,
                passed=False,
                warning=None,
                violation_reason=(
                    "CONST-003: organ_id assente. Ogni organo deve essere "
                    "identificato per operare entro il proprio mandato."
                ),
                data_missing="organ_id",
            )

        # authority_mode deve essere riconosciuto
        if request.authority_mode not in _VALID_AUTHORITY_MODES:
            return PrincipleEvaluationDetail(
                principle_id=self.principle_id,
                applicable=True,
                passed=False,
                warning=None,
                violation_reason=(
                    f"CONST-003: authority_mode={request.authority_mode!r} non riconosciuto. "
                    f"Valori validi: {sorted(_VALID_AUTHORITY_MODES)}."
                ),
                data_missing=None,
            )

        # Warning opzionale: azione autonoma ad alto rischio
        warning = None
        if (
            request.authority_mode == "autonomous"
            and request.risk_level == "high"
        ):
            warning = (
                "CONST-003: azione autonoma con risk_level=high — verificare "
                "che l'organo abbia il mandato corretto per questo livello di rischio."
            )

        return PrincipleEvaluationDetail(
            principle_id=self.principle_id,
            applicable=True,
            passed=True,
            warning=warning,
            violation_reason=None,
            data_missing=None,
        )

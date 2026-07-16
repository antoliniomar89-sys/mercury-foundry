"""Validator deterministico per la Costituzione — MF-CONST-001.

Flusso:
  1. Recupera i principi applicabili dal registry.
  2. Per ogni principio attivo, chiama il suo evaluator dedicato.
  3. Aggrega i risultati in un ConstitutionalValidationResult.
  4. Determina lo status complessivo (pass / pass_with_warnings / fail).
  5. Determina l'enforcement_action in base ai principi violati e al loro enforcement.

Non usa LLM. Non fa assunzioni su dati non presenti nella richiesta.
Non contiene un unico blocco if/else non manutenibile: la logica è distribuita
negli evaluator, uno per principio.

Estensibilità: aggiungere un evaluator = creare un modulo in evaluators/ e
registrarlo qui in `_build_evaluator_registry()`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from mercury_foundry.constitutional.evaluators.authority_boundary import AuthorityBoundaryEvaluator
from mercury_foundry.constitutional.evaluators.auditability import AuditabilityEvaluator
from mercury_foundry.constitutional.evaluators.base import PrincipleEvaluator
from mercury_foundry.constitutional.evaluators.constitutional_change import ConstitutionalChangeProtectionEvaluator
from mercury_foundry.constitutional.evaluators.evidence import EvidenceBeforeInvestmentEvaluator
from mercury_foundry.constitutional.evaluators.reversibility import ReversibilityEvaluator
from mercury_foundry.constitutional.models import (
    ConstitutionalPrinciple,
    ConstitutionalValidationRequest,
    ConstitutionalValidationResult,
    EnforcementAction,
    PrincipleEnforcement,
    PrincipleEvaluationDetail,
    ValidationStatus,
)
from mercury_foundry.constitutional.registry import ConstitutionRegistry


def _build_evaluator_registry() -> dict[str, PrincipleEvaluator]:
    """Costruisce il registro degli evaluator per principle_id.

    Per aggiungere un evaluator: istanziarlo qui. Non richiede modifica al
    validator né alla logica di aggregazione.
    """
    evaluators: list[PrincipleEvaluator] = [
        EvidenceBeforeInvestmentEvaluator(),   # CONST-001
        AuditabilityEvaluator(),               # CONST-002
        AuthorityBoundaryEvaluator(),          # CONST-003
        ReversibilityEvaluator(),              # CONST-004
        ConstitutionalChangeProtectionEvaluator(),  # CONST-007
        # CONST-005 e CONST-006 sono in shadow/advisory e non hanno ancora
        # un evaluator deterministico in V0: i loro principi non vengono
        # valutati (passes=None, applicable=False implicito).
    ]
    return {e.principle_id: e for e in evaluators}


_EVALUATOR_REGISTRY: dict[str, PrincipleEvaluator] = _build_evaluator_registry()


class ConstitutionalValidator:
    """Valida una richiesta rispetto alla Costituzione attiva.

    Orchestrazione:
      - Itera sui principi attivi del registry.
      - Per ogni principio con un evaluator registrato, chiama evaluate().
      - Principi senza evaluator → non valutati, non contano come violazioni.
      - Aggrega i risultati in un ConstitutionalValidationResult.
    """

    def __init__(self, registry: ConstitutionRegistry) -> None:
        self._registry = registry

    def validate(
        self,
        request: ConstitutionalValidationRequest,
    ) -> ConstitutionalValidationResult:
        """Valida la richiesta e restituisce un risultato machine-readable.

        Non solleva mai eccezioni: i principi non valutabili per dati mancanti
        o inapplicabilità sono rappresentati nel risultato.
        """
        active_principles = self._registry.list_active_principles()
        details: list[PrincipleEvaluationDetail] = []

        for principle in active_principles:
            evaluator = _EVALUATOR_REGISTRY.get(principle.principle_id)
            if evaluator is None:
                # Nessun evaluator disponibile: principio non valutato.
                # Non conteggiato tra i violati.
                continue
            try:
                detail = evaluator.evaluate(principle, request)
            except Exception as exc:
                # L'evaluator ha sollevato un'eccezione inattesa: trattiamo
                # il principio come data_missing per non bloccare la validazione.
                detail = PrincipleEvaluationDetail(
                    principle_id=principle.principle_id,
                    applicable=False,
                    passed=None,
                    warning=None,
                    violation_reason=None,
                    data_missing=f"evaluator error: {exc}",
                )
            details.append(detail)

        return self._aggregate(request, details)

    # ------------------------------------------------------------------
    # Aggregazione
    # ------------------------------------------------------------------

    def _aggregate(
        self,
        request: ConstitutionalValidationRequest,
        details: list[PrincipleEvaluationDetail],
    ) -> ConstitutionalValidationResult:
        evaluated: list[str] = []
        passed: list[str] = []
        violated: list[str] = []
        warnings: list[str] = []

        for d in details:
            if not d.applicable:
                continue  # non applicabile → non contribuisce
            evaluated.append(d.principle_id)
            if d.passed is True:
                passed.append(d.principle_id)
                if d.warning:
                    warnings.append(d.warning)
            elif d.passed is False:
                violated.append(d.principle_id)
                if d.violation_reason:
                    warnings.append(d.violation_reason)
            # passed=None + applicable=True: dato mancante, non è una violazione
            else:
                if d.data_missing:
                    warnings.append(
                        f"{d.principle_id}: dato mancante per la valutazione — {d.data_missing}"
                    )

        # Status complessivo
        if violated:
            # Controlla se almeno una violazione riguarda un principio BLOCKING
            violated_principles = {
                d.principle_id for d in details if d.principle_id in violated
            }
            registry_principles = {
                p.principle_id: p for p in self._registry.list_active_principles()
            }
            has_blocking = any(
                registry_principles.get(pid, None) is not None
                and registry_principles[pid].enforcement == PrincipleEnforcement.BLOCKING
                for pid in violated_principles
            )
            status = ValidationStatus.FAIL if has_blocking else ValidationStatus.PASS_WITH_WARNINGS
        elif warnings:
            status = ValidationStatus.PASS_WITH_WARNINGS
        else:
            status = ValidationStatus.PASS

        enforcement_action = self._determine_enforcement_action(
            status, violated, details
        )

        explanation = self._build_explanation(
            status, evaluated, passed, violated, warnings
        )

        return ConstitutionalValidationResult(
            validation_id=str(uuid.uuid4()),
            decision_id=request.decision_id,
            constitution_version=self._registry.version_string,
            status=status,
            evaluated_principles=evaluated,
            passed_principles=passed,
            violated_principles=violated,
            warnings=warnings,
            enforcement_action=enforcement_action,
            explanation=explanation,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _determine_enforcement_action(
        self,
        status: ValidationStatus,
        violated: list[str],
        details: list[PrincipleEvaluationDetail],
    ) -> EnforcementAction:
        """Determina l'azione da intraprendere in base allo status e ai principi violati."""
        if status == ValidationStatus.PASS:
            return EnforcementAction.ALLOW

        # Cerca la violazione più grave tra i principi violati
        if violated:
            registry_principles = {
                p.principle_id: p for p in self._registry.list_active_principles()
            }
            has_blocking = any(
                registry_principles.get(pid, None) is not None
                and registry_principles[pid].enforcement == PrincipleEnforcement.BLOCKING
                for pid in violated
            )
            has_audit_only = any(
                registry_principles.get(pid, None) is not None
                and registry_principles[pid].enforcement == PrincipleEnforcement.AUDIT_ONLY
                for pid in violated
            )

            if has_blocking:
                return EnforcementAction.DENY
            if has_audit_only:
                return EnforcementAction.ALLOW_SHADOW

        # Solo warnings (advisory) → allow con shadow notation
        return EnforcementAction.ALLOW_SHADOW

    def _build_explanation(
        self,
        status: ValidationStatus,
        evaluated: list[str],
        passed: list[str],
        violated: list[str],
        warnings: list[str],
    ) -> str:
        parts = [
            f"Validazione costituzionale: {status.value.upper()}.",
            f"Principi valutati: {len(evaluated)}.",
        ]
        if passed:
            parts.append(f"Conformi: {', '.join(passed)}.")
        if violated:
            parts.append(f"In violazione: {', '.join(violated)}.")
        if warnings:
            parts.append(f"Avvertimenti ({len(warnings)}): " + " | ".join(warnings[:3]))
            if len(warnings) > 3:
                parts.append(f"... e altri {len(warnings) - 3} avvertimenti.")
        return " ".join(parts)

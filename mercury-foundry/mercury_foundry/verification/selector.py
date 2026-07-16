"""TestSelector — seleziona i test da eseguire in base all'analisi di impatto.

MF-VERIFY-001: produce un VerificationPlan completo con i test selezionati,
le motivazioni, il livello assegnato e l'indicazione se la suite completa
è stata saltata e perché.
"""

from __future__ import annotations

from mercury_foundry.verification.impact import ImpactAnalysis
from mercury_foundry.verification.mapping import (
    CRITICAL_TESTS,
    REQUIRES_CONSTITUTIONAL_TESTS,
    RISK_TO_MINIMUM_LEVEL,
)
from mercury_foundry.verification.models import (
    BudgetStatus,
    CostBudget,
    RiskClass,
    VerificationLevel,
    VerificationPlan,
    _new_id,
    _now_iso,
)


# Costo operativo stimato per tipo di run (op-unit = 1 file test eseguito)
_COST_PER_TEST_FILE = 1
_COST_DOCTOR = 2         # Doctor conta 2 op-unit
_COST_STATIC_CHECK = 0   # Static check è gratuito


class TestSelector:
    """Seleziona i test da eseguire in base all'impatto e al budget.

    Regole:
    - STATIC:   solo syntax/import check, nessuna suite funzionale.
    - TARGETED: solo test direttamente associati ai file modificati.
    - IMPACTED: test associati + dipendenti + test critici costituzionali.
    - FULL:     tutti i test.

    La suite completa è obbligatoria solo in casi specifici (milestone, CRITICAL
    release, schema DB cambiato, richiesta esplicita, impatto non determinabile).
    Durante lo sviluppo intermedio si usa sempre TARGETED o IMPACTED.
    """

    FULL_SUITE_REQUIRED_TRIGGERS = frozenset({
        "milestone",
        "release",
        "schema_changed",
        "constitution_changed",
        "mission_lifecycle_changed",
        "shared_code_changed",
        "explicit_request",
        "impact_undetermined",
        "risk_critical_final",
    })

    def select(
        self,
        impact: ImpactAnalysis,
        *,
        budget: CostBudget | None = None,
        budget_status: BudgetStatus | None = None,
        force_level: VerificationLevel | None = None,
        triggers: set[str] | None = None,
    ) -> VerificationPlan:
        """Produce un VerificationPlan da una ImpactAnalysis.

        Args:
            impact: risultato dell'analisi di impatto.
            budget: budget configurato per la missione (opzionale).
            budget_status: stato corrente del budget (opzionale).
            force_level: forza un livello specifico (override).
            triggers: motivi che richiedono FULL (es. {"milestone"}).

        Returns:
            VerificationPlan con test selezionati, motivazioni, livello.
        """
        triggers = triggers or set()
        level = force_level if force_level is not None else impact.minimum_level

        # --- Verifica se la suite completa è necessaria ---
        full_required, full_reason = self._is_full_required(
            impact, level, budget, budget_status, triggers
        )

        if full_required:
            level = VerificationLevel.FULL

        # --- Seleziona i test in base al livello ---
        selected, reasons = self._select_tests_for_level(impact, level)

        # --- Se budget non permette FULL, retrocedi ---
        if (
            level == VerificationLevel.FULL
            and budget is not None
            and budget_status is not None
            and budget_status.full_suite_runs_used >= budget.max_full_suite_runs
        ):
            level = VerificationLevel.IMPACTED
            full_required = False
            full_reason = None
            selected, reasons = self._select_tests_for_level(impact, level)
            reasons.append(
                "Suite completa saltata: max_full_suite_runs esaurito per questa missione"
            )

        # --- Calcola costo stimato ---
        estimated_cost = self._estimate_cost(selected, level)

        # --- Costruisci e ritorna il piano ---
        return VerificationPlan(
            plan_id                   = _new_id(),
            level                     = level,
            risk_class                = impact.aggregate_risk_class,
            changed_files             = impact.changed_files,
            classified_files          = impact.classified_files,
            selected_tests            = selected,
            selection_reasons         = reasons,
            full_suite_skipped        = not full_required and level < VerificationLevel.FULL,
            full_suite_skip_reason    = self._full_skip_reason(impact, level, triggers),
            requires_full_at_milestone = impact.requires_full_at_milestone,
            estimated_ops_cost        = estimated_cost,
            created_at                = _now_iso(),
        )

    # ----------------------------------------------------------------
    # Logica interna
    # ----------------------------------------------------------------

    def _is_full_required(
        self,
        impact: ImpactAnalysis,
        level: VerificationLevel,
        budget: CostBudget | None,
        budget_status: BudgetStatus | None,
        triggers: set[str],
    ) -> tuple[bool, str | None]:
        """Determina se la suite completa è obbligatoria."""
        # Trigger espliciti
        active = triggers & self.FULL_SUITE_REQUIRED_TRIGGERS
        if active:
            return True, f"Trigger obbligatorio: {', '.join(sorted(active))}"

        # Forza esplicita a FULL
        if level == VerificationLevel.FULL:
            return True, "Livello FULL richiesto esplicitamente"

        return False, None

    def _select_tests_for_level(
        self,
        impact: ImpactAnalysis,
        level: VerificationLevel,
    ) -> tuple[list[str], list[str]]:
        """Ritorna (test_files, reasons) per il livello dato."""
        selected: list[str] = []
        reasons: list[str] = []

        if level == VerificationLevel.STATIC:
            reasons.append(
                "STATIC: solo syntax/import check — nessuna suite funzionale. "
                "Modifica a basso rischio (documentazione o non-comportamentale)."
            )
            return [], reasons

        if level == VerificationLevel.FULL:
            reasons.append(
                "FULL: suite completa richiesta — milestone/release/schema/constitutional "
                "o trigger esplicito."
            )
            return ["tests/"], reasons  # pytest tests/ = tutta la suite

        # TARGETED o IMPACTED: usa i test mappati
        base_tests = list(impact.selected_test_files)
        for tf in base_tests:
            matching_domains = [
                m.domains
                for m in impact.matched_mappings
                if tf in m.test_files
            ]
            domain_str = (
                ", ".join(sorted({d for ds in matching_domains for d in ds}))
                if matching_domains
                else "dominio mappato"
            )
            reasons.append(
                f"{tf}: associato ai file modificati [{domain_str}]"
            )

        # IMPACTED: aggiungi test critici costituzionali se richiesti
        if level == VerificationLevel.IMPACTED:
            if impact.requires_constitutional_tests:
                for ct in CRITICAL_TESTS:
                    if ct not in base_tests:
                        base_tests.append(ct)
                        reasons.append(
                            f"{ct}: incluso perché la modifica è {impact.aggregate_risk_class.value.upper()} "
                            f"e richiede verifica degli invarianti costituzionali"
                        )

        # File sconosciuti → nota
        if impact.unknown_files:
            reasons.append(
                f"File non mappati {impact.unknown_files}: inclusi test IMPACTED "
                f"per escalation prudente"
            )

        # Aggiungi note dall'analisi
        for note in impact.analysis_notes:
            reasons.append(f"Nota impatto: {note}")

        # Spiega perché la suite completa non è eseguita
        if level < VerificationLevel.FULL:
            skip_msg = self._full_skip_reason(impact, level, set())
            if skip_msg:
                reasons.append(f"Suite completa SALTATA: {skip_msg}")

        selected = sorted(set(base_tests))
        return selected, reasons

    def _full_skip_reason(
        self,
        impact: ImpactAnalysis,
        level: VerificationLevel,
        triggers: set[str],
    ) -> str | None:
        """Spiega perché la suite completa non viene eseguita."""
        if level >= VerificationLevel.FULL:
            return None
        if not triggers:
            parts = []
            if level == VerificationLevel.TARGETED:
                parts.append(
                    "modifica a impatto limitato — i test selezionati coprono i file modificati"
                )
            elif level == VerificationLevel.IMPACTED:
                parts.append(
                    "impatto contenuto — i test selezionati coprono modulo e dipendenti diretti"
                )
            parts.append(
                "la suite completa (~490 test, ~290s) è riservata a milestone e release"
            )
            return "; ".join(parts)
        return None

    def _estimate_cost(self, selected: list[str], level: VerificationLevel) -> int:
        """Stima il costo in op-unit."""
        if level == VerificationLevel.STATIC:
            return _COST_STATIC_CHECK
        if level == VerificationLevel.FULL:
            return 25 + _COST_DOCTOR  # tutti i file + Doctor
        base = len(selected) * _COST_PER_TEST_FILE
        if level == VerificationLevel.IMPACTED:
            base += _COST_DOCTOR
        return base

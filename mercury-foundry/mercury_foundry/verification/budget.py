"""DevelopmentCostGovernor — budget operativo per missione di sviluppo.

MF-VERIFY-001: traccia iterazioni, test run, full suite run, tentativi falliti
senza miglioramento, tempo trascorso. Interrompe loop improduttivi e produce
EscalationReport quando il budget è esaurito.

Budget separato per missione — mai globale.
Nessun dato economico reale: registra usage units.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import MutableMapping

from mercury_foundry.verification.models import (
    BudgetStatus,
    CheckpointRecord,
    CostBudget,
    EscalationReport,
    ProgressSnapshot,
    RiskClass,
    TestRunRecord,
    VerificationLevel,
    _new_id,
    _now_iso,
)


# ---------------------------------------------------------------------------
# _MissionState (interno)
# ---------------------------------------------------------------------------

@dataclass
class _MissionState:
    """Stato interno per una missione tracciata dal governor."""
    budget:                        CostBudget
    started_at:                    float = field(default_factory=time.monotonic)
    iterations_used:               int   = 0
    test_runs_used:                int   = 0
    full_suite_runs_used:          int   = 0
    failed_runs_without_improvement: int = 0
    ai_calls_used:                 int   = 0
    run_records:                   list[TestRunRecord] = field(default_factory=list)
    snapshots:                     list[ProgressSnapshot] = field(default_factory=list)
    checkpoints:                   list[CheckpointRecord] = field(default_factory=list)
    exhausted:                     bool  = False
    exhaustion_reason:             str | None = None
    requires_human_approval:       bool  = False

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at


# ---------------------------------------------------------------------------
# DevelopmentCostGovernor
# ---------------------------------------------------------------------------

class DevelopmentCostGovernor:
    """Applica budget operativi per missione e blocca loop improduttivi.

    Ogni missione ha un budget indipendente. Il governor non ha stato globale.

    Esempio:
        governor = DevelopmentCostGovernor()
        budget = CostBudget(mission_id="my-mission", max_test_runs=5)
        governor.start_mission(budget)
        # ... dopo ogni test run ...
        status = governor.record_test_run(budget.mission_id, run_record)
        if not status.can_run(budget):
            report = governor.get_escalation_report(budget.mission_id)
            print(report.render())
    """

    def __init__(self) -> None:
        self._states: MutableMapping[str, _MissionState] = {}

    # ----------------------------------------------------------------
    # API pubblica
    # ----------------------------------------------------------------

    def start_mission(self, budget: CostBudget) -> BudgetStatus:
        """Inizializza il tracking per una nuova missione.

        Sovrascrive uno stato precedente per la stessa mission_id.
        """
        errors = budget.validate()
        if errors:
            raise ValueError(f"Budget non valido per mission {budget.mission_id!r}: {errors}")
        self._states[budget.mission_id] = _MissionState(budget=budget)
        return self._status(budget.mission_id)

    def record_iteration(self, mission_id: str) -> BudgetStatus:
        """Incrementa il contatore di iterazioni per la missione."""
        state = self._require(mission_id)
        state.iterations_used += 1
        self._check_exhaustion(mission_id)
        return self._status(mission_id)

    def record_test_run(
        self,
        mission_id: str,
        record: TestRunRecord,
    ) -> BudgetStatus:
        """Registra un'esecuzione di test e aggiorna il budget.

        Solleva BudgetExhaustedError se stop_on_budget_exhaustion=True
        e il budget è esaurito PRIMA di registrare questo run.
        """
        state = self._require(mission_id)

        # Verifica budget PRIMA di permettere il run
        if state.exhausted and state.budget.stop_on_budget_exhaustion:
            raise BudgetExhaustedError(
                mission_id=mission_id,
                reason=state.exhaustion_reason or "budget esaurito",
            )

        state.test_runs_used += 1
        state.run_records.append(record)

        if record.level == VerificationLevel.FULL:
            state.full_suite_runs_used += 1

        self._check_exhaustion(mission_id)
        return self._status(mission_id)

    def record_progress(
        self,
        mission_id: str,
        snapshot: ProgressSnapshot,
    ) -> tuple[bool, BudgetStatus]:
        """Registra un snapshot di progresso e valuta il miglioramento.

        Returns:
            (should_continue, BudgetStatus)
            should_continue = True se esistono miglioramenti o il budget non è esaurito.
        """
        state = self._require(mission_id)
        state.snapshots.append(snapshot)

        if snapshot.failed_count > 0:
            # Fallimento: verifica miglioramento
            if snapshot.is_improvement:
                state.failed_runs_without_improvement = 0
            else:
                state.failed_runs_without_improvement += 1
        else:
            # Successo totale → reset contatore
            state.failed_runs_without_improvement = 0

        self._check_exhaustion(mission_id)
        status = self._status(mission_id)
        should_continue = not state.exhausted
        return should_continue, status

    def record_ai_call(self, mission_id: str, count: int = 1) -> BudgetStatus:
        """Registra chiamate AI (se tracciabili)."""
        state = self._require(mission_id)
        state.ai_calls_used += count
        self._check_exhaustion(mission_id)
        return self._status(mission_id)

    def record_checkpoint(
        self,
        mission_id: str,
        git_hash: str,
        risk_class: RiskClass,
        files_at_risk: list[str],
        working_tree_clean: bool,
        notes: str = "",
    ) -> CheckpointRecord:
        """Registra un checkpoint recuperabile prima di modifiche HIGH/CRITICAL."""
        state = self._require(mission_id)
        cp = CheckpointRecord(
            checkpoint_id      = _new_id(),
            mission_id         = mission_id,
            git_hash           = git_hash,
            risk_class         = risk_class,
            files_at_risk      = list(files_at_risk),
            working_tree_clean = working_tree_clean,
            recorded_at        = _now_iso(),
            notes              = notes,
        )
        state.checkpoints.append(cp)
        return cp

    def check_budget(self, mission_id: str) -> BudgetStatus:
        """Ritorna lo stato corrente del budget per la missione."""
        return self._status(mission_id)

    def get_escalation_report(self, mission_id: str) -> EscalationReport:
        """Produce un EscalationReport per la missione corrente."""
        state = self._require(mission_id)
        status = self._status(mission_id)

        trigger = state.exhaustion_reason or "unknown"
        recommendation = self._build_recommendation(state, trigger)

        return EscalationReport(
            report_id                = _new_id(),
            mission_id               = mission_id,
            trigger                  = trigger,
            last_snapshots           = list(state.snapshots[-5:]),
            budget_status            = status,
            recommendation           = recommendation,
            requires_human_decision  = (
                state.budget.require_human_approval_on_exhaustion
                or state.requires_human_approval
            ),
            created_at               = _now_iso(),
        )

    def should_propose_rollback(self, mission_id: str) -> bool:
        """True se è opportuno proporre rollback (no auto-reset distruttivo)."""
        state = self._require(mission_id)
        if not state.exhausted:
            return False
        # Proponi rollback solo se ci sono errori multipli senza miglioramento
        return state.failed_runs_without_improvement >= state.budget.max_failed_runs_without_improvement

    def get_latest_checkpoint(self, mission_id: str) -> CheckpointRecord | None:
        """Ritorna l'ultimo checkpoint registrato per la missione."""
        state = self._require(mission_id)
        return state.checkpoints[-1] if state.checkpoints else None

    def mission_exists(self, mission_id: str) -> bool:
        return mission_id in self._states

    # ----------------------------------------------------------------
    # Privati
    # ----------------------------------------------------------------

    def _require(self, mission_id: str) -> _MissionState:
        state = self._states.get(mission_id)
        if state is None:
            raise MissionNotStartedError(mission_id)
        return state

    def _status(self, mission_id: str) -> BudgetStatus:
        state = self._states[mission_id]
        return BudgetStatus(
            mission_id                       = mission_id,
            iterations_used                  = state.iterations_used,
            test_runs_used                   = state.test_runs_used,
            full_suite_runs_used             = state.full_suite_runs_used,
            failed_runs_without_improvement  = state.failed_runs_without_improvement,
            elapsed_seconds                  = state.elapsed_seconds(),
            ai_calls_used                    = state.ai_calls_used,
            exhausted                        = state.exhausted,
            exhaustion_reason                = state.exhaustion_reason,
            requires_human_approval          = state.requires_human_approval,
        )

    def _check_exhaustion(self, mission_id: str) -> None:
        """Aggiorna lo stato di exhaustion. Non solleva mai eccezioni qui."""
        state = self._states[mission_id]
        if state.exhausted:
            return
        budget = state.budget

        # max_iterations
        if state.iterations_used > budget.max_iterations:
            state.exhausted = True
            state.exhaustion_reason = (
                f"max_iterations esaurito ({state.iterations_used} > {budget.max_iterations})"
            )
            return

        # max_test_runs (>= perché il budget si esaurisce DOPO aver usato tutti i run)
        if state.test_runs_used >= budget.max_test_runs:
            state.exhausted = True
            state.exhaustion_reason = (
                f"max_test_runs esaurito ({state.test_runs_used} >= {budget.max_test_runs})"
            )
            return

        # max_full_suite_runs
        if state.full_suite_runs_used >= budget.max_full_suite_runs:
            state.exhausted = True
            state.exhaustion_reason = (
                f"max_full_suite_runs esaurito "
                f"({state.full_suite_runs_used} >= {budget.max_full_suite_runs})"
            )
            return

        # max_failed_runs_without_improvement
        if state.failed_runs_without_improvement >= budget.max_failed_runs_without_improvement:
            state.exhausted = True
            state.exhaustion_reason = (
                f"nessun miglioramento per {state.failed_runs_without_improvement} "
                f"tentativi consecutivi (max: {budget.max_failed_runs_without_improvement})"
            )
            state.requires_human_approval = budget.require_human_approval_on_exhaustion
            return

        # max_elapsed_seconds
        if (
            budget.max_elapsed_seconds is not None
            and state.elapsed_seconds() > budget.max_elapsed_seconds
        ):
            state.exhausted = True
            state.exhaustion_reason = (
                f"timeout elapsed ({state.elapsed_seconds():.0f}s > "
                f"{budget.max_elapsed_seconds}s)"
            )
            return

        # max_ai_calls
        if (
            budget.max_ai_calls is not None
            and state.ai_calls_used > budget.max_ai_calls
        ):
            state.exhausted = True
            state.exhaustion_reason = (
                f"max_ai_calls esaurito ({state.ai_calls_used} > {budget.max_ai_calls})"
            )
            return

    def _build_recommendation(self, state: _MissionState, trigger: str) -> str:
        if "nessun miglioramento" in trigger or "no_improvement" in trigger:
            return (
                "Il sistema ha eseguito il numero massimo di tentativi senza miglioramento. "
                "Analizzare l'ultimo escalation report, identificare la causa radice, "
                "e riprendere solo dopo aver modificato l'approccio. "
                "Non effettuare altre modifiche automatiche."
            )
        if "max_test_runs" in trigger or "max_iterations" in trigger:
            return (
                "Il budget operativo è esaurito. "
                "Valutare se aumentare i limiti o suddividere la missione in sotto-task. "
                "Nessuna modifica automatica aggiuntiva."
            )
        if "timeout" in trigger:
            return (
                "Il tempo massimo per la missione è trascorso. "
                "Verificare se le ultime modifiche sono stabili e procedere manualmente."
            )
        return (
            f"Budget esaurito ({trigger}). "
            "Richiedere revisione umana prima di procedere."
        )


# ---------------------------------------------------------------------------
# Eccezioni specifiche
# ---------------------------------------------------------------------------

class BudgetExhaustedError(Exception):
    """Sollevato quando si tenta di eseguire test con budget esaurito."""
    def __init__(self, mission_id: str, reason: str):
        super().__init__(
            f"Budget esaurito per mission {mission_id!r}: {reason}. "
            "Richiedere decisione umana prima di procedere."
        )
        self.mission_id = mission_id
        self.reason = reason


class MissionNotStartedError(Exception):
    """Sollevato quando si accede a una missione non ancora inizializzata."""
    def __init__(self, mission_id: str):
        super().__init__(
            f"Missione {mission_id!r} non trovata nel governor. "
            "Chiamare start_mission() prima di usare il governor per questa missione."
        )
        self.mission_id = mission_id


# ---------------------------------------------------------------------------
# Costruttore di snapshot di progresso
# ---------------------------------------------------------------------------

def build_progress_snapshot(
    attempt: int,
    current_record: TestRunRecord,
    previous_record: TestRunRecord | None = None,
) -> ProgressSnapshot:
    """Costruisce un ProgressSnapshot confrontando il run corrente con il precedente."""
    current_failed = set(current_record.failed_test_ids)
    is_improvement = False
    improvement_reason: str | None = None

    if previous_record is None:
        # Primo tentativo: considerato non-improvement (baseline)
        is_improvement = current_record.failed == 0
        if is_improvement:
            improvement_reason = "Primo tentativo riuscito"
    else:
        prev_failed = set(previous_record.failed_test_ids)
        # Miglioramento: meno test falliti
        if len(current_failed) < len(prev_failed):
            is_improvement = True
            improvement_reason = (
                f"Falliti diminuiti: {len(prev_failed)} → {len(current_failed)}"
            )
        # Miglioramento: eliminato almeno un errore precedente
        elif prev_failed - current_failed:
            is_improvement = True
            eliminated = sorted(prev_failed - current_failed)
            improvement_reason = f"Errori eliminati: {eliminated[:3]}"
        # Miglioramento: tutti i test passati
        elif current_record.failed == 0:
            is_improvement = True
            improvement_reason = "Tutti i test passati"

    return ProgressSnapshot(
        attempt            = attempt,
        failed_count       = current_record.failed,
        failed_test_ids    = list(current_failed),
        error_types        = [],   # popolato dal runner se disponibile
        duration_seconds   = current_record.duration_seconds,
        is_improvement     = is_improvement,
        improvement_reason = improvement_reason,
    )

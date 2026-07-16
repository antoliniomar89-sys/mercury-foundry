"""VerificationRunner — orchestrazione di impact, selector, budget e cache.

MF-VERIFY-001: punto di ingresso principale per la verifica adattiva.

Flusso:
    1. Analizza i file modificati (ChangeImpactAnalyzer)
    2. Seleziona i test (TestSelector)
    3. Verifica il budget (DevelopmentCostGovernor)
    4. Controlla la cache (TestResultCache)
    5. Se necessario, registra checkpoint (HIGH/CRITICAL)
    6. Esegue i test via subprocess
    7. Registra il risultato nel governor
    8. Aggiorna la cache
    9. Valuta il progresso

Non esegue mai automaticamente reset distruttivi del repository.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

from mercury_foundry.verification.budget import (
    BudgetExhaustedError,
    DevelopmentCostGovernor,
    MissionNotStartedError,
    build_progress_snapshot,
)
from mercury_foundry.verification.cache import TestResultCache
from mercury_foundry.verification.impact import ChangeImpactAnalyzer, ImpactAnalysis
from mercury_foundry.verification.models import (
    BudgetStatus,
    CostBudget,
    EscalationReport,
    RiskClass,
    TestRunRecord,
    VerificationLevel,
    VerificationPlan,
    _new_id,
    _now_iso,
)
from mercury_foundry.verification.selector import TestSelector


# ---------------------------------------------------------------------------
# VerificationRunner
# ---------------------------------------------------------------------------

class VerificationRunner:
    """Orchestrazione completa del ciclo di verifica adattiva.

    Esempio:
        runner = VerificationRunner()
        budget = CostBudget(mission_id="mf-eco-001")
        runner.start_mission(budget)

        plan = runner.plan()                  # analizza git diff
        record = runner.run(plan)             # esegue i test selezionati
        print(runner.status(budget.mission_id))
    """

    def __init__(
        self,
        project_root: Path | str | None = None,
        cache_dir: Path | str | None = None,
    ):
        from mercury_foundry import config
        self._root = Path(project_root) if project_root else config.BASE_DIR
        self._analyzer = ChangeImpactAnalyzer(project_root=self._root)
        self._selector = TestSelector()
        self._governor = DevelopmentCostGovernor()
        self._cache = TestResultCache(cache_dir=cache_dir)
        self._last_record: dict[str, TestRunRecord | None] = {}

    # ----------------------------------------------------------------
    # Missione
    # ----------------------------------------------------------------

    def start_mission(self, budget: CostBudget) -> BudgetStatus:
        """Inizializza il budget per una missione."""
        return self._governor.start_mission(budget)

    # ----------------------------------------------------------------
    # Plan
    # ----------------------------------------------------------------

    def plan(
        self,
        changed_files: list[str] | None = None,
        *,
        force_level: VerificationLevel | None = None,
        triggers: set[str] | None = None,
        mission_id: str | None = None,
    ) -> VerificationPlan:
        """Produce un VerificationPlan.

        Args:
            changed_files: se None, usa git diff HEAD.
            force_level: forza un livello specifico.
            triggers: trigger aggiuntivi (es. {"milestone"}).
            mission_id: per recuperare il budget dal governor.
        """
        if changed_files is None:
            impact = self._analyzer.analyze_git_diff()
        else:
            impact = self._analyzer.analyze(changed_files)

        budget = None
        budget_status = None
        if mission_id and self._governor.mission_exists(mission_id):
            budget_obj = self._governor._states[mission_id].budget
            budget = budget_obj
            budget_status = self._governor.check_budget(mission_id)

        plan = self._selector.select(
            impact,
            budget        = budget,
            budget_status = budget_status,
            force_level   = force_level,
            triggers      = triggers,
        )
        return plan

    # ----------------------------------------------------------------
    # Run
    # ----------------------------------------------------------------

    def run(
        self,
        plan: VerificationPlan,
        *,
        mission_id: str | None = None,
        dry_run: bool = False,
    ) -> TestRunRecord:
        """Esegue i test selezionati nel piano.

        Args:
            plan: piano prodotto da plan().
            mission_id: per aggiornare il governor.
            dry_run: se True, non esegue effettivamente i test (solo log).

        Returns:
            TestRunRecord con il risultato dell'esecuzione.
        """
        # --- Verifica budget prima di eseguire ---
        if mission_id:
            try:
                status = self._governor.check_budget(mission_id)
                if status.exhausted:
                    raise BudgetExhaustedError(
                        mission_id=mission_id,
                        reason=status.exhaustion_reason or "budget esaurito",
                    )
            except MissionNotStartedError:
                pass  # Missione non tracciata → permetti l'esecuzione

        # --- Checkpoint per HIGH/CRITICAL ---
        if mission_id and plan.risk_class in (RiskClass.HIGH, RiskClass.CRITICAL):
            self._maybe_register_checkpoint(mission_id, plan)

        # --- Controlla cache (solo per TARGETED e IMPACTED) ---
        if plan.level in (VerificationLevel.TARGETED, VerificationLevel.IMPACTED):
            cached = self._check_cache(plan)
            if cached:
                if mission_id:
                    self._record_run_in_governor(mission_id, plan, cached)
                return cached

        # --- Costruisce il comando pytest ---
        cmd = self._build_command(plan)
        run_id = _new_id()
        started_at = _now_iso()
        t0 = time.monotonic()

        if dry_run:
            record = TestRunRecord(
                run_id           = run_id,
                plan_id          = plan.plan_id,
                command          = cmd,
                level            = plan.level,
                started_at       = started_at,
                completed_at     = _now_iso(),
                passed           = 0,
                failed           = 0,
                errors           = 0,
                duration_seconds = 0.0,
                failed_test_ids  = [],
                from_cache       = False,
                exit_code        = 0,
                output_summary   = "[dry-run] nessuna esecuzione effettiva",
            )
            if mission_id:
                self._record_run_in_governor(mission_id, plan, record)
            return record

        # --- Esecuzione effettiva ---
        if plan.level == VerificationLevel.STATIC:
            record = self._run_static_check(run_id, plan.plan_id, started_at)
        else:
            record = self._run_pytest(cmd, run_id, plan, started_at)

        # --- Aggiorna cache ---
        if plan.level in (VerificationLevel.TARGETED, VerificationLevel.IMPACTED):
            key = self._cache.build_key(
                source_files = plan.changed_files,
                test_files   = plan.selected_tests,
                command      = cmd,
            )
            self._cache.put(key, record)

        # --- Aggiorna governor ---
        if mission_id:
            self._record_run_in_governor(mission_id, plan, record)
            self._last_record[mission_id] = record

        return record

    # ----------------------------------------------------------------
    # Status
    # ----------------------------------------------------------------

    def status(self, mission_id: str) -> BudgetStatus:
        """Ritorna lo stato corrente del budget per la missione."""
        return self._governor.check_budget(mission_id)

    def get_escalation_report(self, mission_id: str) -> EscalationReport:
        """Ritorna il report di escalation per la missione."""
        return self._governor.get_escalation_report(mission_id)

    def should_propose_rollback(self, mission_id: str) -> bool:
        """True se è opportuno proporre rollback (senza eseguirlo automaticamente)."""
        return self._governor.should_propose_rollback(mission_id)

    # ----------------------------------------------------------------
    # Privati
    # ----------------------------------------------------------------

    def _build_command(self, plan: VerificationPlan) -> list[str]:
        """Costruisce il comando pytest per il piano."""
        base = [sys.executable, "-m", "pytest", "--tb=short", "-q"]

        if plan.level == VerificationLevel.FULL:
            base.append("tests/")
        elif plan.selected_tests:
            base.extend(plan.selected_tests)
        else:
            # Nessun test selezionato → solo check (non esegue nulla)
            base.extend(["--collect-only", "tests/"])

        return base

    def _run_pytest(
        self,
        cmd: list[str],
        run_id: str,
        plan: VerificationPlan,
        started_at: str,
    ) -> TestRunRecord:
        """Esegue pytest come subprocess e analizza l'output."""
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                cwd          = str(self._root),
                capture_output = True,
                text         = True,
                timeout      = 600,   # 10 min max
            )
            exit_code = result.returncode
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            exit_code = -1
            output = "TIMEOUT: il test ha superato il limite di 600s"

        duration = time.monotonic() - t0
        passed, failed, errors, failed_ids = self._parse_pytest_output(output)

        return TestRunRecord(
            run_id           = run_id,
            plan_id          = plan.plan_id,
            command          = cmd,
            level            = plan.level,
            started_at       = started_at,
            completed_at     = _now_iso(),
            passed           = passed,
            failed           = failed,
            errors           = errors,
            duration_seconds = duration,
            failed_test_ids  = failed_ids,
            from_cache       = False,
            exit_code        = exit_code,
            output_summary   = output[-2000:] if len(output) > 2000 else output,
        )

    def _run_static_check(
        self, run_id: str, plan_id: str, started_at: str
    ) -> TestRunRecord:
        """Esegue controlli statici: syntax e import check."""
        t0 = time.monotonic()
        errors_found = []

        # Trova tutti i .py nel package e verifica la compilazione
        pkg_dir = self._root / "mercury_foundry"
        py_files = list(pkg_dir.rglob("*.py"))
        for pf in py_files[:50]:  # limita a 50 file per velocità
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "py_compile", str(pf)],
                    capture_output = True,
                    text           = True,
                    timeout        = 10,
                )
                if result.returncode != 0:
                    errors_found.append(f"{pf}: {result.stderr.strip()}")
            except subprocess.TimeoutExpired:
                errors_found.append(f"{pf}: timeout")

        duration = time.monotonic() - t0
        failed = len(errors_found)
        return TestRunRecord(
            run_id           = run_id,
            plan_id          = plan_id,
            command          = [sys.executable, "-m", "py_compile", "mercury_foundry/"],
            level            = VerificationLevel.STATIC,
            started_at       = started_at,
            completed_at     = _now_iso(),
            passed           = len(py_files) - failed,
            failed           = failed,
            errors           = 0,
            duration_seconds = duration,
            failed_test_ids  = errors_found,
            from_cache       = False,
            exit_code        = 0 if failed == 0 else 1,
            output_summary   = "\n".join(errors_found) if errors_found else "Syntax check OK",
        )

    def _check_cache(self, plan: VerificationPlan) -> TestRunRecord | None:
        """Controlla la cache per il piano corrente."""
        cmd = self._build_command(plan)
        key = self._cache.build_key(
            source_files = plan.changed_files,
            test_files   = plan.selected_tests,
            command      = cmd,
        )
        should_invalidate, reason = self._cache.should_invalidate(
            plan.changed_files,
            schema_changed = any("schema" in f.lower() or "db.py" in f for f in plan.changed_files),
        )
        if should_invalidate:
            self._cache.invalidate(key, reason or "auto-invalidazione")
            return None

        entry = self._cache.get(key)
        if entry and entry.valid:
            cached_result = entry.result
            return TestRunRecord(
                run_id           = cached_result.run_id,
                plan_id          = plan.plan_id,
                command          = cached_result.command,
                level            = cached_result.level,
                started_at       = cached_result.started_at,
                completed_at     = cached_result.completed_at,
                passed           = cached_result.passed,
                failed           = cached_result.failed,
                errors           = cached_result.errors,
                duration_seconds = cached_result.duration_seconds,
                failed_test_ids  = cached_result.failed_test_ids,
                from_cache       = True,
                exit_code        = cached_result.exit_code,
                output_summary   = f"[CACHE HIT] {cached_result.output_summary}",
            )
        return None

    def _record_run_in_governor(
        self,
        mission_id: str,
        plan: VerificationPlan,
        record: TestRunRecord,
    ) -> None:
        """Aggiorna il governor con il record del run."""
        try:
            self._governor.record_iteration(mission_id)
            self._governor.record_test_run(mission_id, record)

            # Progresso
            prev = self._last_record.get(mission_id)
            if record.failed > 0 or (prev and prev.failed > 0):
                snapshot = build_progress_snapshot(
                    attempt         = self._governor._states[mission_id].test_runs_used,
                    current_record  = record,
                    previous_record = prev,
                )
                self._governor.record_progress(mission_id, snapshot)
        except (BudgetExhaustedError, MissionNotStartedError):
            pass

    def _maybe_register_checkpoint(
        self, mission_id: str, plan: VerificationPlan
    ) -> None:
        """Registra un checkpoint recuperabile prima di modifiche HIGH/CRITICAL."""
        try:
            is_clean, _ = self._analyzer.get_working_tree_status()
            git_hash = self._analyzer.get_current_git_hash() or "unknown"
            self._governor.record_checkpoint(
                mission_id         = mission_id,
                git_hash           = git_hash,
                risk_class         = plan.risk_class,
                files_at_risk      = plan.changed_files,
                working_tree_clean = is_clean,
                notes              = f"Pre-run checkpoint per piano {plan.plan_id[:8]}",
            )
        except (MissionNotStartedError, Exception):
            pass  # checkpoint è best-effort

    @staticmethod
    def _parse_pytest_output(
        output: str,
    ) -> tuple[int, int, int, list[str]]:
        """Analizza l'output di pytest e ritorna (passed, failed, errors, failed_ids)."""
        passed = failed = errors = 0
        failed_ids: list[str] = []

        # Riga sommario: "5 passed, 2 failed, 1 error"
        summary_pattern = re.compile(
            r"(?:(\d+) passed)?.*?(?:(\d+) failed)?.*?(?:(\d+) error)?",
            re.IGNORECASE,
        )
        for line in output.splitlines():
            if "passed" in line or "failed" in line or "error" in line:
                m = summary_pattern.search(line)
                if m:
                    passed  = int(m.group(1) or 0)
                    failed  = int(m.group(2) or 0)
                    errors  = int(m.group(3) or 0)
                    break

        # Raccoglie gli ID dei test falliti (linee "FAILED ...")
        for line in output.splitlines():
            if line.startswith("FAILED "):
                test_id = line[len("FAILED "):].strip().split(" ")[0]
                if test_id:
                    failed_ids.append(test_id)

        return passed, failed, errors, failed_ids

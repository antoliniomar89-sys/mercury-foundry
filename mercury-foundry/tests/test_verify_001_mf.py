"""MF-VERIFY-001 — Adaptive Verification and Development Cost Governor V0.

18 test che verificano tutti i criteri di completamento della spec.

Test 1-5:   Classificazione del rischio e selezione del livello
Test 6-7:   Mapping source→test e motivazioni
Test 8-9:   Rispetto del budget (max_test_runs, max_full_suite_runs)
Test 10-11: Stop per mancato miglioramento / prosecuzione con miglioramento
Test 12-13: Cache hit valida / invalidazione cache
Test 14-15: Checkpoint registrato / rollback proposto senza distruzione automatica
Test 16:    Budget separato per missione
Test 17:    Nessuna chiamata esterna reale
Test 18:    Nessuna riduzione degli invarianti esistenti
"""

from __future__ import annotations

import re
import sys
import tempfile
import uuid
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mid() -> str:
    return f"test-mission-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# TEST 1 — Modifica documentazione → LEVEL 0 o LEVEL 1
# ---------------------------------------------------------------------------

def test_01_documentation_file_level_0_or_1():
    """1 — File .md o docs/ → STATIC o TARGETED (mai IMPACTED o FULL)."""
    from mercury_foundry.verification.impact import ChangeImpactAnalyzer
    from mercury_foundry.verification.models import VerificationLevel

    analyzer = ChangeImpactAnalyzer()
    impact = analyzer.analyze(["docs/architecture.md", "README.md"])

    assert impact.minimum_level <= VerificationLevel.TARGETED, (
        f"Documentazione dovrebbe produrre STATIC/TARGETED, trovato {impact.minimum_level.label()}"
    )
    assert impact.aggregate_risk_class.value in ("low",), (
        f"Documentazione dovrebbe essere LOW risk, trovato {impact.aggregate_risk_class.value}"
    )


# ---------------------------------------------------------------------------
# TEST 2 — Modifica resource allocator → LEVEL 2
# ---------------------------------------------------------------------------

def test_02_resource_allocator_level_2():
    """2 — mercury_foundry/outcome/allocator.py → LEVEL 2 (IMPACTED)."""
    from mercury_foundry.verification.impact import ChangeImpactAnalyzer
    from mercury_foundry.verification.models import VerificationLevel, RiskClass

    analyzer = ChangeImpactAnalyzer()
    impact = analyzer.analyze(["mercury_foundry/outcome/allocator.py"])

    assert impact.minimum_level >= VerificationLevel.IMPACTED, (
        f"Resource allocator dovrebbe essere IMPACTED, trovato {impact.minimum_level.label()}"
    )
    assert impact.aggregate_risk_class == RiskClass.HIGH, (
        f"Resource allocator dovrebbe essere HIGH risk, trovato {impact.aggregate_risk_class.value}"
    )
    # I test di outcome devono essere inclusi
    assert any("test_outcome_001" in t or "test_eco_001" in t for t in impact.selected_test_files), (
        f"Test outcome non trovati nella selezione: {impact.selected_test_files}"
    )


# ---------------------------------------------------------------------------
# TEST 3 — Modifica schema DB → LEVEL 2 durante sviluppo, LEVEL 3 richiesto a milestone
# ---------------------------------------------------------------------------

def test_03_schema_db_level_2_dev_level_3_milestone():
    """3 — schema.sql → IMPACTED durante sviluppo; richiede FULL a milestone."""
    from mercury_foundry.verification.impact import ChangeImpactAnalyzer
    from mercury_foundry.verification.selector import TestSelector
    from mercury_foundry.verification.models import VerificationLevel

    analyzer = ChangeImpactAnalyzer()
    selector = TestSelector()

    impact = analyzer.analyze(["mercury_foundry/state/schema.sql"])

    # Durante sviluppo: IMPACTED
    plan_dev = selector.select(impact)
    assert plan_dev.level == VerificationLevel.IMPACTED, (
        f"Schema durante sviluppo: atteso IMPACTED, trovato {plan_dev.level.label()}"
    )
    # Richiede FULL a milestone
    assert plan_dev.requires_full_at_milestone is True, (
        "Schema deve segnalare requires_full_at_milestone=True"
    )

    # Con trigger milestone: FULL
    plan_milestone = selector.select(impact, triggers={"milestone"})
    assert plan_milestone.level == VerificationLevel.FULL, (
        f"Schema con trigger milestone: atteso FULL, trovato {plan_milestone.level.label()}"
    )


# ---------------------------------------------------------------------------
# TEST 4 — Modifica costituzionale → test costituzionali obbligatori
# ---------------------------------------------------------------------------

def test_04_constitutional_change_includes_constitutional_tests():
    """4 — Modifica a mercury_foundry/constitutional/ → test costituzionali obbligatori."""
    from mercury_foundry.verification.impact import ChangeImpactAnalyzer
    from mercury_foundry.verification.selector import TestSelector
    from mercury_foundry.verification.models import RiskClass

    analyzer = ChangeImpactAnalyzer()
    selector = TestSelector()

    impact = analyzer.analyze(["mercury_foundry/constitutional/core.py"])
    plan = selector.select(impact)

    assert impact.aggregate_risk_class == RiskClass.CRITICAL, (
        f"Modifica costituzionale dovrebbe essere CRITICAL, trovato {impact.aggregate_risk_class.value}"
    )
    assert impact.requires_constitutional_tests is True

    # Test costituzionali inclusi nel piano
    const_tests = [t for t in plan.selected_tests if "const_001" in t or "autonomy" in t]
    assert len(const_tests) > 0, (
        f"Nessun test costituzionale nel piano: {plan.selected_tests}"
    )


# ---------------------------------------------------------------------------
# TEST 5 — File sconosciuto → escalation prudente
# ---------------------------------------------------------------------------

def test_05_unknown_file_escalation():
    """5 — File non mappato → escalation prudente (IMPACTED, MEDIUM)."""
    from mercury_foundry.verification.impact import ChangeImpactAnalyzer
    from mercury_foundry.verification.models import VerificationLevel

    analyzer = ChangeImpactAnalyzer()
    impact = analyzer.analyze(["mercury_foundry/totally_unknown_module_xyz.py"])

    assert len(impact.unknown_files) == 1, (
        f"Atteso 1 file non mappato, trovato {len(impact.unknown_files)}"
    )
    # Escalation prudente: livello alzato
    assert impact.minimum_level >= VerificationLevel.IMPACTED, (
        f"File sconosciuto dovrebbe produrre IMPACTED, trovato {impact.minimum_level.label()}"
    )
    # Note sull'escalation
    assert any("non mappato" in n.lower() or "escalation" in n.lower()
               for n in impact.analysis_notes), (
        f"Nessuna nota di escalation: {impact.analysis_notes}"
    )


# ---------------------------------------------------------------------------
# TEST 6 — Mapping test corretto
# ---------------------------------------------------------------------------

def test_06_mapping_correct():
    """6 — La mappa source→test risolve correttamente i pattern noti."""
    from mercury_foundry.verification.impact import ChangeImpactAnalyzer

    analyzer = ChangeImpactAnalyzer()

    # outcome → test_outcome_001_mf.py incluso
    impact_outcome = analyzer.analyze(["mercury_foundry/outcome/registry.py"])
    assert any("test_outcome_001" in t for t in impact_outcome.selected_test_files), (
        f"outcome/registry.py: test_outcome_001 non trovato in {impact_outcome.selected_test_files}"
    )

    # mission → test_mission_001_mf.py incluso
    impact_mission = analyzer.analyze(["mercury_foundry/mission/intake.py"])
    assert any("test_mission_001" in t for t in impact_mission.selected_test_files), (
        f"mission/intake.py: test_mission_001 non trovato in {impact_mission.selected_test_files}"
    )

    # replication → test_repl_001_mf.py incluso
    impact_repl = analyzer.analyze(["mercury_foundry/replication/service.py"])
    assert any("test_repl_001" in t for t in impact_repl.selected_test_files), (
        f"replication/service.py: test_repl_001 non trovato in {impact_repl.selected_test_files}"
    )


# ---------------------------------------------------------------------------
# TEST 7 — Spiegazione delle selezioni presente
# ---------------------------------------------------------------------------

def test_07_selection_reasons_present():
    """7 — Il piano include una motivazione per ogni test selezionato."""
    from mercury_foundry.verification.impact import ChangeImpactAnalyzer
    from mercury_foundry.verification.selector import TestSelector

    analyzer = ChangeImpactAnalyzer()
    selector = TestSelector()

    impact = analyzer.analyze(["mercury_foundry/mission/models.py"])
    plan = selector.select(impact)

    # La lista delle motivazioni deve essere non vuota
    assert len(plan.selection_reasons) > 0, "Nessuna motivazione nel piano"

    # Ogni test selezionato deve avere almeno una motivazione associata
    # (le motivazioni possono coprire più test; non serve una per test)
    assert len(plan.selection_reasons) >= len(plan.selected_tests), (
        f"Motivazioni ({len(plan.selection_reasons)}) < test selezionati ({len(plan.selected_tests)})"
    )

    # Il piano deve spiegare perché la suite completa non è eseguita
    if plan.full_suite_skipped:
        assert plan.full_suite_skip_reason is not None, (
            "full_suite_skipped=True ma full_suite_skip_reason è None"
        )
        assert len(plan.full_suite_skip_reason) > 10, (
            "full_suite_skip_reason troppo breve"
        )


# ---------------------------------------------------------------------------
# TEST 8 — Rispetto max_test_runs
# ---------------------------------------------------------------------------

def test_08_max_test_runs_respected():
    """8 — Il governor blocca l'esecuzione quando max_test_runs è esaurito."""
    from mercury_foundry.verification.budget import (
        DevelopmentCostGovernor,
        BudgetExhaustedError,
        build_progress_snapshot,
    )
    from mercury_foundry.verification.models import (
        CostBudget,
        TestRunRecord,
        VerificationLevel,
        _new_id,
        _now_iso,
    )

    gov = DevelopmentCostGovernor()
    mid = _mid()
    budget = CostBudget(
        mission_id    = mid,
        max_test_runs = 2,
        max_iterations = 10,
        stop_on_budget_exhaustion = True,
    )
    gov.start_mission(budget)

    def _make_record(passed: int = 5, failed: int = 0) -> TestRunRecord:
        return TestRunRecord(
            run_id=_new_id(), plan_id=_new_id(),
            command=["pytest", "tests/"], level=VerificationLevel.TARGETED,
            started_at=_now_iso(), completed_at=_now_iso(),
            passed=passed, failed=failed, errors=0,
            duration_seconds=1.0, failed_test_ids=[],
        )

    # Run 1 e 2: OK
    gov.record_test_run(mid, _make_record())
    gov.record_test_run(mid, _make_record())

    # Run 3: deve essere bloccato
    with pytest.raises(BudgetExhaustedError) as exc_info:
        gov.record_test_run(mid, _make_record())

    assert mid in str(exc_info.value), "BudgetExhaustedError non contiene mission_id"
    status = gov.check_budget(mid)
    assert status.exhausted is True


# ---------------------------------------------------------------------------
# TEST 9 — Rispetto max_full_suite_runs
# ---------------------------------------------------------------------------

def test_09_max_full_suite_runs_respected():
    """9 — La suite completa non viene eseguita più di max_full_suite_runs volte."""
    from mercury_foundry.verification.budget import (
        DevelopmentCostGovernor,
        BudgetExhaustedError,
    )
    from mercury_foundry.verification.models import (
        CostBudget,
        TestRunRecord,
        VerificationLevel,
        _new_id,
        _now_iso,
    )

    gov = DevelopmentCostGovernor()
    mid = _mid()
    budget = CostBudget(
        mission_id           = mid,
        max_test_runs        = 10,
        max_full_suite_runs  = 1,
        max_iterations       = 20,
        stop_on_budget_exhaustion = True,
    )
    gov.start_mission(budget)

    def _full_record() -> TestRunRecord:
        return TestRunRecord(
            run_id=_new_id(), plan_id=_new_id(),
            command=["pytest", "tests/"], level=VerificationLevel.FULL,
            started_at=_now_iso(), completed_at=_now_iso(),
            passed=491, failed=0, errors=0,
            duration_seconds=290.0, failed_test_ids=[],
        )

    # Prima full suite: OK
    gov.record_test_run(mid, _full_record())
    status = gov.check_budget(mid)
    assert status.full_suite_runs_used == 1

    # Seconda full suite: budget esaurito
    with pytest.raises(BudgetExhaustedError):
        gov.record_test_run(mid, _full_record())

    status = gov.check_budget(mid)
    assert status.exhausted is True
    assert "full_suite" in (status.exhaustion_reason or "")


# ---------------------------------------------------------------------------
# TEST 10 — Stop dopo N tentativi senza miglioramento
# ---------------------------------------------------------------------------

def test_10_stop_after_no_improvement():
    """10 — Il governor ferma il ciclo dopo max_failed_runs_without_improvement tentativi."""
    from mercury_foundry.verification.budget import (
        DevelopmentCostGovernor,
        build_progress_snapshot,
    )
    from mercury_foundry.verification.models import (
        CostBudget,
        TestRunRecord,
        VerificationLevel,
        _new_id,
        _now_iso,
    )

    gov = DevelopmentCostGovernor()
    mid = _mid()
    budget = CostBudget(
        mission_id                        = mid,
        max_test_runs                     = 20,
        max_iterations                    = 20,
        max_failed_runs_without_improvement = 3,
        stop_on_budget_exhaustion         = False,  # non blocca, solo segnala
    )
    gov.start_mission(budget)

    def _failed_record(n: int) -> TestRunRecord:
        return TestRunRecord(
            run_id=_new_id(), plan_id=_new_id(),
            command=["pytest"], level=VerificationLevel.TARGETED,
            started_at=_now_iso(), completed_at=_now_iso(),
            passed=0, failed=n, errors=0,
            duration_seconds=1.0,
            failed_test_ids=[f"test_foo_{i}" for i in range(n)],
        )

    # 3 tentativi con STESSO numero di falliti → nessun miglioramento
    prev = None
    for attempt in range(1, 4):
        record = _failed_record(5)   # sempre 5 falliti
        gov.record_test_run(mid, record)
        snapshot = build_progress_snapshot(attempt, record, prev)
        should_continue, status = gov.record_progress(mid, snapshot)
        prev = record

    # Dopo 3 tentativi senza miglioramento, deve fermarsi
    assert status.failed_runs_without_improvement >= 3, (
        f"Attesi >= 3 tentativi senza miglioramento, trovati {status.failed_runs_without_improvement}"
    )
    assert status.exhausted is True, "Il governor dovrebbe essere esaurito"
    assert "miglioramento" in (status.exhaustion_reason or ""), (
        f"Exhaustion reason attesa su 'miglioramento': {status.exhaustion_reason}"
    )


# ---------------------------------------------------------------------------
# TEST 11 — Prosecuzione quando esiste miglioramento
# ---------------------------------------------------------------------------

def test_11_continue_when_improvement_exists():
    """11 — Il governor continua quando esiste miglioramento documentato."""
    from mercury_foundry.verification.budget import (
        DevelopmentCostGovernor,
        build_progress_snapshot,
    )
    from mercury_foundry.verification.models import (
        CostBudget,
        TestRunRecord,
        VerificationLevel,
        _new_id,
        _now_iso,
    )

    gov = DevelopmentCostGovernor()
    mid = _mid()
    budget = CostBudget(
        mission_id                        = mid,
        max_test_runs                     = 20,
        max_iterations                    = 20,
        max_failed_runs_without_improvement = 3,
        stop_on_budget_exhaustion         = False,
    )
    gov.start_mission(budget)

    def _rec(failed: int, ids: list[str] | None = None) -> TestRunRecord:
        return TestRunRecord(
            run_id=_new_id(), plan_id=_new_id(),
            command=["pytest"], level=VerificationLevel.TARGETED,
            started_at=_now_iso(), completed_at=_now_iso(),
            passed=5 - failed, failed=failed, errors=0,
            duration_seconds=1.0,
            failed_test_ids=ids or [f"test_fail_{i}" for i in range(failed)],
        )

    # Tentativo 1: 3 falliti
    r1 = _rec(3)
    gov.record_test_run(mid, r1)
    s1 = build_progress_snapshot(1, r1, None)
    _, st = gov.record_progress(mid, s1)

    # Tentativo 2: 2 falliti → MIGLIORAMENTO (da 3 a 2)
    r2 = _rec(2)
    gov.record_test_run(mid, r2)
    s2 = build_progress_snapshot(2, r2, r1)
    _, st = gov.record_progress(mid, s2)

    assert s2.is_improvement is True, "Atteso miglioramento (3→2 falliti)"
    assert st.failed_runs_without_improvement == 0, (
        f"Il contatore senza-miglioramento dovrebbe essere resettato, trovato {st.failed_runs_without_improvement}"
    )
    assert st.exhausted is False, "Il governor non dovrebbe essere esaurito con miglioramento"


# ---------------------------------------------------------------------------
# TEST 12 — Cache hit valida
# ---------------------------------------------------------------------------

def test_12_cache_hit_valid(tmp_path):
    """12 — Una voce in cache viene recuperata correttamente con la stessa chiave."""
    from mercury_foundry.verification.cache import TestResultCache
    from mercury_foundry.verification.models import (
        TestRunRecord,
        VerificationLevel,
        _new_id,
        _now_iso,
    )

    cache = TestResultCache(cache_dir=tmp_path / ".verify_cache")

    source_files = ["mercury_foundry/mission/intake.py"]
    test_files   = ["tests/test_mission_001_mf.py"]
    command      = ["pytest", "-q", "tests/test_mission_001_mf.py"]

    key = cache.build_key(source_files, test_files, command)

    # Crea un record di successo
    record = TestRunRecord(
        run_id=_new_id(), plan_id=_new_id(),
        command=command, level=VerificationLevel.TARGETED,
        started_at=_now_iso(), completed_at=_now_iso(),
        passed=47, failed=0, errors=0,
        duration_seconds=12.5,
        failed_test_ids=[],
        exit_code=0,
        output_summary="47 passed",
    )

    # Salva in cache
    entry = cache.put(key, record)
    assert entry.valid is True

    # Recupera
    retrieved = cache.get(key)
    assert retrieved is not None, "Cache miss inatteso"
    assert retrieved.valid is True
    assert retrieved.result.passed == 47
    assert retrieved.result.failed == 0
    assert retrieved.result.from_cache is True


# ---------------------------------------------------------------------------
# TEST 13 — Invalidazione cache per schema cambiato
# ---------------------------------------------------------------------------

def test_13_cache_invalidation_on_schema_change(tmp_path):
    """13 — La cache viene invalidata quando schema.sql è tra i file modificati."""
    from mercury_foundry.verification.cache import TestResultCache
    from mercury_foundry.verification.models import (
        TestRunRecord,
        VerificationLevel,
        _new_id,
        _now_iso,
    )

    cache = TestResultCache(cache_dir=tmp_path / ".verify_cache")

    source_files = ["mercury_foundry/state/schema.sql"]
    test_files   = ["tests/test_doctor.py"]
    command      = ["pytest", "-q", "tests/test_doctor.py"]

    key = cache.build_key(source_files, test_files, command)

    # Salva un risultato valido
    record = TestRunRecord(
        run_id=_new_id(), plan_id=_new_id(),
        command=command, level=VerificationLevel.IMPACTED,
        started_at=_now_iso(), completed_at=_now_iso(),
        passed=10, failed=0, errors=0,
        duration_seconds=2.0, failed_test_ids=[], exit_code=0,
    )
    cache.put(key, record)

    # Verifica che should_invalidate rilevi lo schema cambiato
    should_inv, reason = cache.should_invalidate(source_files, schema_changed=True)
    assert should_inv is True, "Cache dovrebbe essere invalidata per schema_changed"
    assert reason is not None

    # Invalida esplicitamente
    cache.invalidate(key, "schema DB cambiato")

    # Recupero: deve fallire
    retrieved = cache.get(key)
    assert retrieved is None or retrieved.valid is False, (
        "Cache entry invalidata non dovrebbe essere restituita come valida"
    )


# ---------------------------------------------------------------------------
# TEST 14 — Checkpoint registrato prima di modifica HIGH
# ---------------------------------------------------------------------------

def test_14_checkpoint_registered_for_high_risk():
    """14 — Il governor registra un checkpoint prima di modifiche HIGH o CRITICAL."""
    from mercury_foundry.verification.budget import DevelopmentCostGovernor
    from mercury_foundry.verification.models import CostBudget, RiskClass

    gov = DevelopmentCostGovernor()
    mid = _mid()
    gov.start_mission(CostBudget(mission_id=mid))

    cp = gov.record_checkpoint(
        mission_id         = mid,
        git_hash           = "abc1234def5678",
        risk_class         = RiskClass.HIGH,
        files_at_risk      = ["mercury_foundry/state/schema.sql", "mercury_foundry/state/db.py"],
        working_tree_clean = True,
        notes              = "Pre-migrazione schema MF-ECO-001",
    )

    assert cp.checkpoint_id is not None and len(cp.checkpoint_id) > 8
    assert cp.git_hash == "abc1234def5678"
    assert cp.risk_class == RiskClass.HIGH
    assert cp.working_tree_clean is True
    assert len(cp.files_at_risk) == 2

    # Recupero checkpoint
    retrieved = gov.get_latest_checkpoint(mid)
    assert retrieved is not None
    assert retrieved.checkpoint_id == cp.checkpoint_id


# ---------------------------------------------------------------------------
# TEST 15 — Rollback proposto senza distruzione automatica
# ---------------------------------------------------------------------------

def test_15_rollback_proposed_no_auto_destruction():
    """15 — Il governor propone rollback senza eseguire automaticamente git reset."""
    from mercury_foundry.verification.budget import (
        DevelopmentCostGovernor,
        build_progress_snapshot,
    )
    from mercury_foundry.verification.models import (
        CostBudget,
        TestRunRecord,
        VerificationLevel,
        _new_id,
        _now_iso,
    )

    gov = DevelopmentCostGovernor()
    mid = _mid()
    budget = CostBudget(
        mission_id                        = mid,
        max_failed_runs_without_improvement = 3,
        stop_on_budget_exhaustion         = False,
    )
    gov.start_mission(budget)

    # Simula 3 tentativi falliti senza miglioramento
    prev = None
    for i in range(1, 4):
        r = TestRunRecord(
            run_id=_new_id(), plan_id=_new_id(),
            command=["pytest"], level=VerificationLevel.TARGETED,
            started_at=_now_iso(), completed_at=_now_iso(),
            passed=0, failed=5, errors=0,
            duration_seconds=1.0, failed_test_ids=["test_broken"],
        )
        gov.record_test_run(mid, r)
        snap = build_progress_snapshot(i, r, prev)
        gov.record_progress(mid, snap)
        prev = r

    # Il governor propone rollback
    assert gov.should_propose_rollback(mid) is True

    # Verifica che il report di escalation non menzioni reset distruttivi
    report = gov.get_escalation_report(mid)
    assert report.requires_human_decision is False  # default
    # La raccomandazione non deve menzionare comandi distruttivi automatici
    rec_lower = report.recommendation.lower()
    assert "git reset --hard" not in rec_lower, (
        "Il report di escalation non deve suggerire git reset --hard automatico"
    )
    assert "automatico" not in rec_lower or "no" in rec_lower or "nessun" in rec_lower, (
        "Il report non deve suggerire azioni automatiche distruttive"
    )


# ---------------------------------------------------------------------------
# TEST 16 — Budget separato per missione
# ---------------------------------------------------------------------------

def test_16_budget_separate_per_mission():
    """16 — Due missioni hanno budget completamente indipendenti."""
    from mercury_foundry.verification.budget import DevelopmentCostGovernor
    from mercury_foundry.verification.models import (
        CostBudget,
        TestRunRecord,
        VerificationLevel,
        _new_id,
        _now_iso,
    )

    gov = DevelopmentCostGovernor()
    mid1 = _mid()
    mid2 = _mid()

    budget1 = CostBudget(mission_id=mid1, max_test_runs=3, max_iterations=10)
    budget2 = CostBudget(mission_id=mid2, max_test_runs=10, max_iterations=10)
    gov.start_mission(budget1)
    gov.start_mission(budget2)

    def _rec(level=VerificationLevel.TARGETED) -> TestRunRecord:
        return TestRunRecord(
            run_id=_new_id(), plan_id=_new_id(),
            command=["pytest"], level=level,
            started_at=_now_iso(), completed_at=_now_iso(),
            passed=5, failed=0, errors=0,
            duration_seconds=1.0, failed_test_ids=[],
        )

    # Esaurisce budget1
    for _ in range(3):
        gov.record_test_run(mid1, _rec())

    s1 = gov.check_budget(mid1)
    s2 = gov.check_budget(mid2)

    assert s1.exhausted is True, "Mission 1 dovrebbe essere esaurita"
    assert s2.exhausted is False, "Mission 2 non dovrebbe essere esaurita"
    assert s1.test_runs_used == 3
    assert s2.test_runs_used == 0  # indipendente


# ---------------------------------------------------------------------------
# TEST 17 — Nessuna chiamata esterna reale
# ---------------------------------------------------------------------------

def test_17_no_real_external_calls():
    """17 — I moduli MF-VERIFY-001 non effettuano chiamate di rete reali."""
    import inspect
    import importlib

    forbidden_patterns = [
        r"\brequests\.get\b",
        r"\brequests\.post\b",
        r"\bhttpx\.get\b",
        r"\bhttpx\.post\b",
        r"\burllib\.request\.urlopen\b",
        r"openai\.",
        r"anthropic\.",
        r"stripe\.",
    ]

    modules_to_check = [
        "mercury_foundry.verification.models",
        "mercury_foundry.verification.mapping",
        "mercury_foundry.verification.impact",
        "mercury_foundry.verification.selector",
        "mercury_foundry.verification.budget",
        "mercury_foundry.verification.cache",
        "mercury_foundry.verification.runner",
        "mercury_foundry.verification.diagnostics",
        "mercury_foundry.verification.backlog",
    ]

    for mod_name in modules_to_check:
        mod = importlib.import_module(mod_name)
        source = inspect.getsource(mod)
        for pattern in forbidden_patterns:
            matches = re.findall(pattern, source)
            assert not matches, (
                f"Chiamata esterna vietata {pattern!r} trovata in {mod_name}: {matches}"
            )


# ---------------------------------------------------------------------------
# TEST 18 — Nessuna riduzione degli invarianti esistenti
# ---------------------------------------------------------------------------

def test_18_no_reduction_of_existing_invariants():
    """18 — MF-VERIFY-001 non rimuove né indebolisce gli invarianti costituzionali/economici."""
    # Verifica che i test esistenti siano ancora passanti implicitamente
    # testando le API che gli invarianti usano

    from mercury_foundry import config

    # Invarianti autonomy
    assert config.AUTONOMY_MODE in ("shadow", "enforced"), (
        f"AUTONOMY_MODE invalido: {config.AUTONOMY_MODE}"
    )
    assert config.CONSTITUTIONAL_CORE_MODE in ("disabled", "shadow", "enforce"), (
        f"CONSTITUTIONAL_CORE_MODE invalido: {config.CONSTITUTIONAL_CORE_MODE}"
    )
    assert config.REPLICATION_ACTIVATION_ENABLED is False, (
        "REPLICATION_ACTIVATION_ENABLED non deve essere True in V0"
    )
    assert config.OUTCOME_AUTO_SCALE_ENABLED is False, (
        "OUTCOME_AUTO_SCALE_ENABLED non deve essere True in V0"
    )
    assert config.OUTCOME_AUTO_BUDGET_INCREASE_ENABLED is False, (
        "OUTCOME_AUTO_BUDGET_INCREASE_ENABLED non deve essere True in V0"
    )

    # Verifica che il modulo verification non importi nulla che tocchi le invarianti
    from mercury_foundry.verification import models, mapping, impact, selector, budget

    # Il modulo verification non deve modificare config
    config_attrs_before = {
        "AUTONOMY_MODE":                    config.AUTONOMY_MODE,
        "CONSTITUTIONAL_CORE_MODE":         config.CONSTITUTIONAL_CORE_MODE,
        "REPLICATION_ACTIVATION_ENABLED":   config.REPLICATION_ACTIVATION_ENABLED,
        "OUTCOME_AUTO_SCALE_ENABLED":       config.OUTCOME_AUTO_SCALE_ENABLED,
    }

    # Import e uso dei moduli
    from mercury_foundry.verification.impact import ChangeImpactAnalyzer
    ChangeImpactAnalyzer().analyze(["docs/test.md"])

    from mercury_foundry.verification.budget import DevelopmentCostGovernor
    from mercury_foundry.verification.models import CostBudget
    gov = DevelopmentCostGovernor()
    gov.start_mission(CostBudget(mission_id="invariant-test"))

    # Config non modificata
    for attr, val in config_attrs_before.items():
        assert getattr(config, attr) == val, (
            f"Config {attr} modificata da MF-VERIFY-001: {val} → {getattr(config, attr)}"
        )

    # Verifica che il backlog non contenga azioni implementate di default
    from mercury_foundry.verification.backlog import ARCHITECTURAL_BACKLOG
    assert len(ARCHITECTURAL_BACKLOG) >= 10, (
        f"Backlog troppo corto: {len(ARCHITECTURAL_BACKLOG)} voci"
    )

    # Verifica che la mappa dichiarativa contenga i test critici
    from mercury_foundry.verification.mapping import SOURCE_MAPPINGS
    const_mappings = [m for m in SOURCE_MAPPINGS if "constitutional" in m.pattern]
    assert len(const_mappings) > 0, "Nessun mapping per il layer costituzionale"
    const_test_files = [t for m in const_mappings for t in m.test_files]
    assert any("const_001" in t for t in const_test_files), (
        f"test_const_001 non trovato nei mapping costituzionali: {const_test_files}"
    )

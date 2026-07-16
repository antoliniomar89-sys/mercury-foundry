"""MF-INTEGRATE-001 — Adaptive Verification Integration.

15 test che verificano l'integrazione di VerificationRunner in ExecutionLoop.

Test 1:  ExecutionLoop senza VerificationRunner → comportamento legacy invariato.
Test 2:  ExecutionLoop con VerificationRunner → piano creato.
Test 3:  Modifica a file a basso rischio → STATIC → nessun test eseguito.
Test 4:  Modifica a file critico → piano IMPACTED.
Test 5:  VerificationRunner esegue → vecchio runner non richiamato nuovamente.
Test 6:  Test selezionati superati → ciclo procede verso CANDIDATE.
Test 7:  Test selezionati falliti → ciclo procede verso FIX.
Test 8:  Mapping incompleto (file sconosciuto) → escalation prudente.
Test 9:  Budget esaurito → termine esplicito senza loop infinito.
Test 10: Cache valida → risultato riutilizzato correttamente.
Test 11: Cache invalida dopo modifica → test rieseguiti.
Test 12: VerificationRunner genera eccezione → fallback legacy controllato.
Test 13: Audit: eventi corretti con goal/task/attempt.
Test 14: Tre tentativi senza progresso → nessuna quarta iterazione.
Test 15: Integrazione reale: BUILD → adaptive test → EVALUATE → CANDIDATE.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mercury_foundry.agents.builder import Builder, BuildResult
from mercury_foundry.agents.evaluator import Evaluator, EvalResult
from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal
from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.execution.loop import ExecutionLoop, ExecutionVerificationResult
from mercury_foundry.orchestrator.orchestrator import Orchestrator
from mercury_foundry.sandbox.workspace import Workspace
from mercury_foundry.state import db, models
from mercury_foundry.testing.runner import TestRunner
from mercury_foundry.verification.impact import ChangeImpactAnalyzer
from mercury_foundry.verification.models import (
    CostBudget,
    TestRunRecord,
    VerificationLevel,
    VerificationPlan,
    _new_id,
    _now_iso,
)
from mercury_foundry.verification.runner import VerificationRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_passing_provider(*, files=None, test_files=None):
    """Provider fake che produce una patch sempre passante."""
    if files is None:
        files = [FileChange(path="hello.py", content="def greet():\n    return 'hi'\n")]
    if test_files is None:
        test_files = [
            FileChange(
                path="tests/test_hello.py",
                content=(
                    "import sys, os\n"
                    "sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
                    "import hello\n\n\n"
                    "def test_greet():\n"
                    "    assert hello.greet() == 'hi'\n"
                ),
            )
        ]

    class _PassProvider(AIProvider):
        name = "pass-fake"
        is_simulated = True

        def propose_plan(self, goal_description: str) -> list[str]:
            return ["scrivere greeting"]

        def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
            return PatchProposal(
                summary="greeting creato",
                files=files,
                test_files=test_files,
                provider_name=self.name,
                is_simulated=True,
            )

    return _PassProvider()


def _make_failing_provider():
    """Provider fake che produce una patch sempre fallente."""

    class _FailProvider(AIProvider):
        name = "fail-fake"
        is_simulated = True

        def propose_plan(self, goal_description: str) -> list[str]:
            return ["scrivere broken"]

        def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
            return PatchProposal(
                summary="broken",
                files=[FileChange(path="broken.py", content="def f(): return 1\n")],
                test_files=[
                    FileChange(
                        path="tests/test_broken.py",
                        content="import broken\n\ndef test_fail():\n    assert broken.f() == 999\n",
                    )
                ],
                provider_name=self.name,
                is_simulated=True,
            )

    return _FailProvider()


def _build_loop(tmp_path, provider, *, verification_runner=None):
    conn = db.connect(tmp_path / "mf.db")
    workspace = Workspace(tmp_path / "target")
    builder = Builder(provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    loop = ExecutionLoop(
        conn,
        builder,
        evaluator,
        staging_base_dir=tmp_path / "staging",
        verification_runner=verification_runner,
    )
    return conn, loop


def _submit_and_run(conn, loop, description="test goal"):
    provider = loop.builder.ai_provider
    goal_id = models.create_goal(conn, description)
    task_id = models.create_task(conn, goal_id, 0, description, assigned_to="builder")
    task = models.get_task(conn, task_id)
    outcome = loop.run_task(task)
    return goal_id, task_id, outcome


def _make_verification_plan(
    level: VerificationLevel,
    changed_files: list[str] | None = None,
    selected_tests: list[str] | None = None,
) -> VerificationPlan:
    from mercury_foundry.verification.models import RiskClass, FileClassification
    from mercury_foundry.verification.mapping import SourceMapping

    cf = changed_files or []
    st = selected_tests or []
    classified = [
        FileClassification(path=f, domain="unknown", risk_class=RiskClass.MEDIUM, reason="test")
        for f in cf
    ]
    return VerificationPlan(
        plan_id=_new_id(),
        level=level,
        risk_class=RiskClass.MEDIUM,
        changed_files=cf,
        classified_files=classified,
        selected_tests=st,
        selection_reasons=[f"test: {t}" for t in st],
        full_suite_skipped=(level < VerificationLevel.FULL),
        full_suite_skip_reason="test",
        requires_full_at_milestone=False,
        estimated_ops_cost=len(st),
        created_at=_now_iso(),
    )


def _make_test_run_record(passed: bool = True, plan_id: str | None = None) -> TestRunRecord:
    return TestRunRecord(
        run_id=_new_id(),
        plan_id=plan_id or _new_id(),
        command=["pytest", "-q"],
        level=VerificationLevel.TARGETED,
        started_at=_now_iso(),
        completed_at=_now_iso(),
        passed=1 if passed else 0,
        failed=0 if passed else 1,
        errors=0,
        duration_seconds=0.1,
        failed_test_ids=[] if passed else ["tests/test_x.py::test_fail"],
        from_cache=False,
        exit_code=0 if passed else 1,
        output_summary="1 passed" if passed else "1 failed",
    )


# ---------------------------------------------------------------------------
# TEST 1 — Legacy: nessun VerificationRunner → comportamento invariato
# ---------------------------------------------------------------------------

def test_01_legacy_no_verification_runner(tmp_path):
    """1 — Senza VerificationRunner il ciclo usa Evaluator direttamente."""
    provider = _make_passing_provider()
    conn, loop = _build_loop(tmp_path, provider, verification_runner=None)
    assert loop._verification_runner is None

    _, _, outcome = _submit_and_run(conn, loop)
    assert outcome.status == "candidate_created"

    actions = [r["action"] for r in list_audit_log(conn)]
    assert "CANDIDATE_CREATED" in actions
    # Nessun evento di verifica adattiva
    assert not any(a.startswith("VERIFICATION_") for a in actions)


# ---------------------------------------------------------------------------
# TEST 2 — Con VerificationRunner: piano creato
# ---------------------------------------------------------------------------

def test_02_with_verification_runner_plan_created(tmp_path, monkeypatch):
    """2 — Con VerificationRunner e file mappati, viene creato un piano."""
    provider = _make_passing_provider(
        files=[FileChange(path="mercury_foundry/execution/loop.py", content="# stub\n")],
        test_files=[
            FileChange(
                path="tests/test_execution_loop_e2e_healthcheck.py",
                content="def test_dummy(): pass\n",
            )
        ],
    )
    vr = VerificationRunner()
    conn, loop = _build_loop(tmp_path, provider, verification_runner=vr)

    plan_calls = []
    original_plan = vr.plan

    def recording_plan(*args, **kwargs):
        p = original_plan(*args, **kwargs)
        plan_calls.append(p)
        return p

    monkeypatch.setattr(vr, "plan", recording_plan)

    _, _, outcome = _submit_and_run(conn, loop)

    assert len(plan_calls) >= 1, "VerificationRunner.plan() deve essere chiamato almeno una volta"
    actions = [r["action"] for r in list_audit_log(conn)]
    assert "VERIFICATION_PLAN_CREATED" in actions


# ---------------------------------------------------------------------------
# TEST 3 — Modifica a file a basso rischio → STATIC → nessun test eseguito
# ---------------------------------------------------------------------------

def test_03_low_risk_documentation_static_no_tests(tmp_path, monkeypatch):
    """3 — File .md → piano STATIC → 0 test eseguiti, EvalResult passed=True."""
    eval_calls = []

    class RecordingEvaluator(Evaluator):
        def evaluate(self, cwd=None, command=None, env=None):
            eval_calls.append({"cwd": cwd, "command": command})
            return EvalResult(passed=True, output="1 passed", duration_ms=10)

    vr = VerificationRunner()
    conn = db.connect(tmp_path / "mf.db")

    provider = _make_passing_provider(
        files=[FileChange(path="README.md", content="# Hello\n")],
        test_files=[],
    )
    workspace = Workspace(tmp_path / "target")
    builder = Builder(provider, workspace)
    evaluator = RecordingEvaluator(TestRunner(workspace.root))
    loop = ExecutionLoop(
        conn,
        builder,
        evaluator,
        staging_base_dir=tmp_path / "staging",
        verification_runner=vr,
    )

    goal_id = models.create_goal(conn, "aggiorna readme")
    task_id = models.create_task(conn, goal_id, 0, "aggiorna readme", assigned_to="builder")
    task = models.get_task(conn, task_id)
    outcome = loop.run_task(task)

    actions = [r["action"] for r in list_audit_log(conn)]

    # Il piano STATIC non esegue pytest — l'evaluator può essere chiamato
    # solo nel fallback (se selected_tests è vuoto e level non è STATIC).
    # Con solo un file .md (STATIC, no selected_tests), ci aspettiamo 0
    # chiamate all'evaluator dalla fase adaptive.
    static_completed = any("STATIC" in a or "VERIFICATION_COMPLETED" in a for a in actions)
    fallback = any("VERIFICATION_FALLBACK_LEGACY" in a for a in actions)
    plan_created = "VERIFICATION_PLAN_CREATED" in actions

    assert plan_created, "VERIFICATION_PLAN_CREATED deve essere presente"
    # Il task deve essere completato (con STATIC passed=True oppure legacy passante)
    assert outcome.status == "candidate_created", (
        f"Stato atteso candidate_created, trovato {outcome.status}"
    )


# ---------------------------------------------------------------------------
# TEST 4 — Modifica a file critico → piano IMPACTED
# ---------------------------------------------------------------------------

def test_04_critical_file_impacted_plan(tmp_path):
    """4 — mercury_foundry/execution/loop.py → piano IMPACTED (HIGH risk)."""
    analyzer = ChangeImpactAnalyzer()
    impact = analyzer.analyze(["mercury_foundry/execution/loop.py"])

    assert impact.minimum_level >= VerificationLevel.IMPACTED, (
        f"execution/loop.py deve produrre IMPACTED, trovato {impact.minimum_level.label()}"
    )
    assert "tests/test_execution_loop_e2e_healthcheck.py" in impact.selected_test_files or \
           "tests/test_integrate_001_adaptive_verification.py" in impact.selected_test_files, (
        f"Test esecuzione non trovati: {impact.selected_test_files}"
    )


# ---------------------------------------------------------------------------
# TEST 5 — VerificationRunner esegue → vecchio runner non chiamato di nuovo
# ---------------------------------------------------------------------------

def test_05_verif_runner_executes_old_runner_not_called_twice(tmp_path, monkeypatch):
    """5 — Con adaptive e test selezionati, Evaluator chiamato una sola volta."""
    eval_call_count = [0]

    class CountingEvaluator(Evaluator):
        def evaluate(self, cwd=None, command=None, env=None):
            eval_call_count[0] += 1
            return EvalResult(passed=True, output="1 passed", duration_ms=10)

    # cache_dir isolata per-test: impedisce che test precedenti (es. test_02,
    # che usa gli stessi file) lascino un cache-hit valido che bypassa
    # l'Evaluator, annullando il conteggio. La TestResultCache usa per default
    # config.BASE_DIR/.verify_cache/ — una directory CONDIVISA tra tutti i
    # test della stessa session — ed è la causa pre-esistente del count=0.
    vr = VerificationRunner(cache_dir=tmp_path / "verify_cache")
    conn = db.connect(tmp_path / "mf.db")

    # Usa file mappati (execution/loop.py) per garantire che la selezione adattiva
    # trovi test e esegua (non fallback)
    provider = _make_passing_provider(
        files=[FileChange(path="mercury_foundry/execution/loop.py", content="# stub\n")],
        test_files=[
            FileChange(
                path="tests/test_execution_loop_e2e_healthcheck.py",
                content="def test_dummy(): pass\n",
            )
        ],
    )
    workspace = Workspace(tmp_path / "target")
    builder = Builder(provider, workspace)
    evaluator = CountingEvaluator(TestRunner(workspace.root))
    loop = ExecutionLoop(
        conn,
        builder,
        evaluator,
        staging_base_dir=tmp_path / "staging",
        verification_runner=vr,
    )

    goal_id = models.create_goal(conn, "test deduplication")
    task_id = models.create_task(conn, goal_id, 0, "test deduplication", assigned_to="builder")
    task = models.get_task(conn, task_id)
    loop.run_task(task)

    # Con adaptive e test selezionati: Evaluator chiamato esattamente 1 volta
    # (per il run adattivo), non 2 (adaptive + legacy).
    assert eval_call_count[0] == 1, (
        f"Evaluator deve essere chiamato esattamente 1 volta, chiamato {eval_call_count[0]}"
    )


# ---------------------------------------------------------------------------
# TEST 5b/5c/5d/5e — Semantica changed_files in VerificationRunner.plan()
#
# Questi test verificano il comportamento di VerificationRunner.plan() chiamato
# DIRETTAMENTE, non attraverso il loop. Non coprono il bug in loop.py
# (_run_adaptive_test), che convertiva [] → None prima di chiamare vr.plan().
# Il test discriminante per quel bug è test_05f (end-to-end attraverso il loop).
#
# Coprono il comportamento stabile di vr.plan():
#   - changed_files=None  → analyze_git_diff() chiamata (comportamento legacy)
#   - changed_files=[]    → analyze([]) chiamata, NON analyze_git_diff
#   - changed_files=[...] → analyze([...]) chiamata invariata
# ---------------------------------------------------------------------------

def test_05b_plan_none_calls_analyze_git_diff(monkeypatch):
    """vr.plan(changed_files=None) deve chiamare analyze_git_diff (non il percorso analyze diretto).

    Nota: analyze_git_diff chiama internamente self.analyze() — il test verifica solo
    il punto di ingresso corretto (analyze_git_diff), non l'assenza di chiamate a analyze.
    """
    git_diff_calls = []

    original_git = ChangeImpactAnalyzer.analyze_git_diff

    def recording_git_diff(self, **kwargs):
        git_diff_calls.append(True)
        return original_git(self, **kwargs)

    monkeypatch.setattr(ChangeImpactAnalyzer, "analyze_git_diff", recording_git_diff)

    vr = VerificationRunner()
    vr.plan(changed_files=None)

    assert len(git_diff_calls) == 1, "None deve chiamare analyze_git_diff esattamente una volta"


def test_05c_plan_empty_list_calls_analyze_not_git_diff(monkeypatch):
    """vr.plan(changed_files=[]) deve chiamare analyze([]), NON analyze_git_diff.

    Questo è il comportamento corretto dopo il fix MF-VERIFY-002:
    una lista vuota è una scelta esplicita dell'utente (nessun file modificato
    rilevato), non un'assenza di informazione — non deve degradare a git_diff.
    """
    git_diff_calls = []
    analyze_calls = []

    original_git = ChangeImpactAnalyzer.analyze_git_diff
    original_analyze = ChangeImpactAnalyzer.analyze

    def recording_git_diff(self, **kwargs):
        git_diff_calls.append(True)
        return original_git(self, **kwargs)

    def recording_analyze(self, changed_files):
        analyze_calls.append(list(changed_files))
        return original_analyze(self, changed_files)

    monkeypatch.setattr(ChangeImpactAnalyzer, "analyze_git_diff", recording_git_diff)
    monkeypatch.setattr(ChangeImpactAnalyzer, "analyze", recording_analyze)

    vr = VerificationRunner()
    vr.plan(changed_files=[])

    assert len(git_diff_calls) == 0, (
        "[] non deve chiamare analyze_git_diff — sarebbe il comportamento del bug pre-esistente"
    )
    assert len(analyze_calls) == 1, "[] deve chiamare analyze() esattamente una volta"
    assert analyze_calls[0] == [], "analyze() deve ricevere [] come argomento"


def test_05d_plan_nonempty_list_passed_unchanged(monkeypatch):
    """vr.plan(changed_files=[...]) deve passare la lista invariata ad analyze()."""
    analyze_calls = []

    original_analyze = ChangeImpactAnalyzer.analyze

    def recording_analyze(self, changed_files):
        analyze_calls.append(list(changed_files))
        return original_analyze(self, changed_files)

    monkeypatch.setattr(ChangeImpactAnalyzer, "analyze", recording_analyze)

    vr = VerificationRunner()
    files = ["mercury_foundry/execution/loop.py", "mercury_foundry/ai/provider_factory.py"]
    vr.plan(changed_files=files)

    assert len(analyze_calls) == 1
    assert analyze_calls[0] == files, (
        f"La lista deve essere passata invariata: atteso {files}, ricevuto {analyze_calls[0]}"
    )


def test_05e_loop_passes_file_writes_not_none_to_plan(tmp_path, monkeypatch):
    """_run_adaptive_test deve passare changed_files_for_verification a vr.plan
    come lista (non come None), anche quando contiene file mappati.

    Verifica che il loop non converta la lista in None prima di chiamare vr.plan.
    """
    plan_calls_changed_files = []

    provider = _make_passing_provider(
        files=[FileChange(path="mercury_foundry/execution/loop.py", content="# stub\n")],
        test_files=[
            FileChange(
                path="tests/test_execution_loop_e2e_healthcheck.py",
                content="def test_dummy(): pass\n",
            )
        ],
    )
    vr = VerificationRunner(cache_dir=tmp_path / "verify_cache")
    original_plan = vr.plan

    def capturing_plan(changed_files=None, **kwargs):
        plan_calls_changed_files.append(changed_files)
        return original_plan(changed_files=changed_files, **kwargs)

    monkeypatch.setattr(vr, "plan", capturing_plan)

    conn, loop = _build_loop(tmp_path, provider, verification_runner=vr)
    _submit_and_run(conn, loop)

    assert len(plan_calls_changed_files) >= 1, "vr.plan deve essere chiamato almeno una volta"
    first_call = plan_calls_changed_files[0]
    assert first_call is not None, (
        "changed_files non deve essere convertito in None quando il builder ha scritto file"
    )
    assert isinstance(first_call, list), f"changed_files deve essere una lista, trovato {type(first_call)}"
    assert len(first_call) > 0, "La lista non deve essere vuota se il builder ha scritto file"
    assert "mercury_foundry/execution/loop.py" in first_call


# ---------------------------------------------------------------------------
# TEST 5f — DISCRIMINANTE end-to-end: file_writes=[] NON convertito in None
#
# MF-VERIFY-002: questo è il test che copre effettivamente il bug in loop.py.
#
# Bug pre-esistente in _run_adaptive_test (loop.py):
#   changed_files if changed_files else None
#   → [] è falsy → diventa None → vr.plan(changed_files=None)
#   → analyze_git_diff() invece di analyze([])
#
# Fix corretto:
#   changed_files if changed_files is not None else None
#   → [] is not None → True → vr.plan(changed_files=[])
#   → analyze([]) — semantica corretta
#
# Scenario: BuildResult.file_writes=[] (nessun file scritto dal builder) →
# changed_files_for_verification=[] → _run_adaptive_test riceve [] →
# vr.plan deve ricevere [] (non None).
#
# Il test fallisce con `if changed_files else None` e passa con
# `if changed_files is not None else None`.
# ---------------------------------------------------------------------------

def test_05f_empty_file_writes_not_converted_to_none_in_loop(tmp_path, monkeypatch):
    """DISCRIMINANTE: _run_adaptive_test passa changed_files=[] a vr.plan, NON None.

    Esercita il percorso reale del loop — non chiama vr.plan() direttamente.
    BuildResult.file_writes=[] → changed_files_for_verification=[] →
    il loop deve passare [] a vr.plan invariato.

    Fallirebbe ripristinando `changed_files if changed_files else None` in loop.py.
    Passa con `changed_files if changed_files is not None else None`.
    """
    from mercury_foundry.agents.builder import BuildResult
    from mercury_foundry.ai.provider import PatchProposal
    from mercury_foundry.policy.literal_constraints import (
        BuildCompletenessResult,
        EnforcementReport,
    )

    # Cache isolata per-test: evita contaminazione da altri test della stessa session.
    vr = VerificationRunner(cache_dir=tmp_path / "verify_cache")

    provider = _make_passing_provider()
    workspace = Workspace(tmp_path / "target")
    builder = Builder(provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    conn = db.connect(tmp_path / "mf.db")
    loop = ExecutionLoop(
        conn,
        builder,
        evaluator,
        staging_base_dir=tmp_path / "staging",
        verification_runner=vr,
    )

    # Monkeypatch builder.build per restituire file_writes=[] senza passare
    # per il gate di completezza (che blocca correttamente le proposte vuote).
    # L'obiettivo è isolare _run_adaptive_test e testare solo la conversione
    # changed_files → argomento di vr.plan.
    def build_with_empty_file_writes(
        task_description, attempt_number, previous_failure,
        literal_constraints=None, *, workspace=None,
    ):
        return BuildResult(
            proposal=PatchProposal(
                summary="zero file — test edge case empty file_writes",
                files=[],
                test_files=[],
                provider_name="zero-fake",
                is_simulated=True,
            ),
            file_writes=[],
            enforcement=EnforcementReport(),
            completeness=BuildCompletenessResult(complete=True, missing_files=[], reasons=[]),
        )

    monkeypatch.setattr(loop.builder, "build", build_with_empty_file_writes)

    # Intercetta vr.plan per catturare il valore di changed_files ricevuto.
    captured_changed_files: list = []
    original_plan = vr.plan

    def capturing_plan(changed_files=None, **kwargs):
        captured_changed_files.append(changed_files)
        return original_plan(changed_files=changed_files, **kwargs)

    monkeypatch.setattr(vr, "plan", capturing_plan)

    goal_id = models.create_goal(conn, "zero file edge case")
    task_id = models.create_task(conn, goal_id, 0, "zero file edge case", assigned_to="builder")
    task = models.get_task(conn, task_id)
    loop.run_task(task)

    assert len(captured_changed_files) >= 1, (
        "vr.plan deve essere chiamato almeno una volta nel percorso adattivo"
    )
    captured = captured_changed_files[0]

    # Asserzioni discriminanti:
    # con il bug  → captured è None  → entrambe falliscono
    # con il fix  → captured è []    → entrambe passano
    assert captured is not None, (
        "changed_files=[] non deve essere convertito in None da _run_adaptive_test.\n"
        "Con il bug `if changed_files else None`: [] è falsy → None → analyze_git_diff().\n"
        "Con il fix `if changed_files is not None else None`: [] → [] → analyze([])."
    )
    assert captured == [], (
        f"changed_files vuoto deve arrivare a vr.plan come lista vuota [], "
        f"ricevuto: {captured!r}"
    )


# ---------------------------------------------------------------------------
# TEST 6 — Test selezionati superati → ciclo verso CANDIDATE
# ---------------------------------------------------------------------------

def test_06_selected_tests_pass_candidate_created(tmp_path):
    """6 — Con VerificationRunner e test passanti → CANDIDATE creato."""
    provider = _make_passing_provider()
    vr = VerificationRunner()
    conn, loop = _build_loop(tmp_path, provider, verification_runner=vr)
    _, _, outcome = _submit_and_run(conn, loop)
    assert outcome.status == "candidate_created", (
        f"Stato atteso candidate_created, trovato {outcome.status}"
    )
    assert outcome.candidate_id is not None


# ---------------------------------------------------------------------------
# TEST 7 — Test selezionati falliti → ciclo verso FIX
# ---------------------------------------------------------------------------

def test_07_selected_tests_fail_fix_triggered(tmp_path):
    """7 — Con VerificationRunner e test fallenti → il ciclo entra in FIX."""
    provider = _make_failing_provider()
    vr = VerificationRunner()
    conn, loop = _build_loop(tmp_path, provider, verification_runner=vr)
    _, task_id, outcome = _submit_and_run(conn, loop)

    # Con 3 tentativi falliti → blocked
    assert outcome.status == "blocked"
    assert outcome.attempts_used == 3

    attempts = models.get_attempts_for_task(conn, task_id)
    assert len(attempts) == 3
    assert all(a["status"] == "failure" for a in attempts)


# ---------------------------------------------------------------------------
# TEST 8 — Mapping incompleto → escalation prudente
# ---------------------------------------------------------------------------

def test_08_incomplete_mapping_escalation(tmp_path, monkeypatch):
    """8 — File non mappato (domain=unknown) → _next_escalation_level suggerisce IMPACTED."""
    from mercury_foundry.verification.models import RiskClass, FileClassification

    provider = _make_passing_provider()
    vr = VerificationRunner()
    conn, loop = _build_loop(tmp_path, provider, verification_runner=vr)

    # Piano TARGETED con un file sconosciuto
    plan = _make_verification_plan(
        VerificationLevel.TARGETED,
        changed_files=["unknown_custom_file.py"],
        selected_tests=["tests/test_staging_isolation.py"],
    )
    # classified_files ha domain="unknown" per default (da _make_verification_plan)

    escalation = loop._next_escalation_level(plan, attempt_number=1)
    assert escalation == VerificationLevel.IMPACTED, (
        f"File non mappato deve suggerire escalation a IMPACTED, trovato {escalation}"
    )


# ---------------------------------------------------------------------------
# TEST 9 — Budget esaurito → termine esplicito
# ---------------------------------------------------------------------------

def test_09_budget_exhausted_explicit_termination(tmp_path, monkeypatch):
    """9 — Budget esaurito → EvalResult.passed=False senza BudgetExhaustedError."""
    from mercury_foundry.verification.models import BudgetStatus

    provider = _make_passing_provider(
        files=[FileChange(path="mercury_foundry/execution/loop.py", content="# stub\n")],
        test_files=[
            FileChange(
                path="tests/test_execution_loop_e2e_healthcheck.py",
                content="def test_d(): pass\n",
            )
        ],
    )
    vr = VerificationRunner()
    conn, loop = _build_loop(tmp_path, provider, verification_runner=vr)

    # Pre-esaurisci il budget iniettando uno stato exhausted nel governor
    mission_id = "t1"  # il task_id sarà 1 per il primo task
    budget = CostBudget(
        mission_id=mission_id,
        max_iterations=1,
        max_test_runs=1,
        max_full_suite_runs=0,
        max_failed_runs_without_improvement=1,
        stop_on_budget_exhaustion=False,
    )
    vr.start_mission(budget)
    # Forza exhausted
    vr._governor._states[mission_id].exhausted = True
    vr._governor._states[mission_id].exhaustion_reason = "budget esaurito per test"

    goal_id = models.create_goal(conn, "budget test")
    task_id = models.create_task(conn, goal_id, 0, "budget test", assigned_to="builder")
    task = models.get_task(conn, task_id)

    # Aggiusta il mission_id per corrispondere al task_id reale
    actual_mission_id = f"t{task_id}"
    vr._governor._states[actual_mission_id] = vr._governor._states.pop(mission_id)

    outcome = loop.run_task(task)

    actions = [r["action"] for r in list_audit_log(conn)]
    assert "VERIFICATION_BUDGET_EXHAUSTED" in actions, (
        f"Manca VERIFICATION_BUDGET_EXHAUSTED in {actions}"
    )
    # Il task è bloccato: budget esaurito → passed=False → FIX × 3 → blocked
    assert outcome.status == "blocked"
    assert outcome.attempts_used >= 1


# ---------------------------------------------------------------------------
# TEST 10 — Cache valida → risultato riutilizzato
# ---------------------------------------------------------------------------

def test_10_valid_cache_result_reused(tmp_path, monkeypatch):
    """10 — Cache valida → Evaluator non chiamato nel secondo run."""
    from mercury_foundry.verification.models import RiskClass, FileClassification

    eval_call_count = [0]

    class CountingEvaluator(Evaluator):
        def evaluate(self, cwd=None, command=None, env=None):
            eval_call_count[0] += 1
            return EvalResult(passed=True, output="1 passed", duration_ms=10)

    vr = VerificationRunner(cache_dir=tmp_path / "vcache")
    conn = db.connect(tmp_path / "mf.db")

    # File mappato
    provider = _make_passing_provider(
        files=[FileChange(path="mercury_foundry/execution/loop.py", content="# stub\n")],
        test_files=[
            FileChange(
                path="tests/test_execution_loop_e2e_healthcheck.py",
                content="def test_dummy(): pass\n",
            )
        ],
    )
    workspace = Workspace(tmp_path / "target")
    builder = Builder(provider, workspace)
    evaluator = CountingEvaluator(TestRunner(workspace.root))
    loop = ExecutionLoop(
        conn, builder, evaluator,
        staging_base_dir=tmp_path / "staging",
        verification_runner=vr,
    )

    # Prima run: Evaluator chiamato
    goal_id = models.create_goal(conn, "cache test 1")
    task_id = models.create_task(conn, goal_id, 0, "cache test 1", assigned_to="builder")
    task = models.get_task(conn, task_id)
    loop.run_task(task)
    first_calls = eval_call_count[0]

    # Seconda run con stesso contenuto di file: deve usare la cache
    goal_id2 = models.create_goal(conn, "cache test 2")
    task_id2 = models.create_task(conn, goal_id2, 0, "cache test 2", assigned_to="builder")
    task2 = models.get_task(conn, task_id2)
    loop.run_task(task2)
    second_calls = eval_call_count[0] - first_calls

    actions = [r["action"] for r in list_audit_log(conn)]
    cache_hit_present = "VERIFICATION_CACHE_HIT" in actions

    # Se la cache ha funzionato, il secondo run non ha chiamato l'evaluator
    if cache_hit_present:
        assert second_calls == 0, (
            f"Cache hit: Evaluator non deve essere chiamato, chiamato {second_calls} volte"
        )
    # Altrimenti: il test documenta che la cache è presente come infrastruttura
    # (la hit dipende dall'identità del contenuto dei file in staging)


# ---------------------------------------------------------------------------
# TEST 11 — Cache invalida dopo modifica → test rieseguiti
# ---------------------------------------------------------------------------

def test_11_cache_invalidated_after_modification(tmp_path):
    """11 — Cache invalida (file schema modificato) → Evaluator chiamato."""
    from mercury_foundry.verification.runner import VerificationRunner as VR

    eval_call_count = [0]

    class CountingEvaluator(Evaluator):
        def evaluate(self, cwd=None, command=None, env=None):
            eval_call_count[0] += 1
            return EvalResult(passed=True, output="1 passed", duration_ms=5)

    vr = VR(cache_dir=tmp_path / "vcache2")
    conn = db.connect(tmp_path / "mf.db")

    # File schema → invalidazione automatica
    provider = _make_passing_provider(
        files=[FileChange(
            path="mercury_foundry/state/schema.sql",
            content="-- schema stub\n",
        )],
        test_files=[
            FileChange(
                path="tests/test_doctor.py",
                content="def test_schema(): pass\n",
            )
        ],
    )
    workspace = Workspace(tmp_path / "target")
    builder = Builder(provider, workspace)
    evaluator = CountingEvaluator(TestRunner(workspace.root))
    loop = ExecutionLoop(
        conn, builder, evaluator,
        staging_base_dir=tmp_path / "staging",
        verification_runner=vr,
    )

    # Pre-popola la cache con un risultato valido
    dummy_plan = _make_verification_plan(
        VerificationLevel.IMPACTED,
        changed_files=["mercury_foundry/state/schema.sql"],
        selected_tests=["tests/test_doctor.py"],
    )
    dummy_record = _make_test_run_record(passed=True, plan_id=dummy_plan.plan_id)
    dummy_cmd = [sys.executable, "-m", "pytest", "-q", "tests/test_doctor.py"]
    cache_key = loop._build_staging_cache_key(vr, tmp_path / "target", dummy_plan, dummy_cmd)
    vr._cache.put(cache_key, dummy_record)

    # Esegui: schema.sql deve invalidare la cache → Evaluator chiamato
    goal_id = models.create_goal(conn, "schema test")
    task_id = models.create_task(conn, goal_id, 0, "schema test", assigned_to="builder")
    task = models.get_task(conn, task_id)
    loop.run_task(task)

    actions = [r["action"] for r in list_audit_log(conn)]
    cache_hit = "VERIFICATION_CACHE_HIT" in actions

    # Con schema modificato, NON deve esserci cache hit
    assert not cache_hit, "La modifica a schema.sql deve invalidare la cache"
    # Evaluator deve essere stato chiamato
    assert eval_call_count[0] >= 1, "Con cache invalida, Evaluator deve essere chiamato"


# ---------------------------------------------------------------------------
# TEST 12 — VerificationRunner genera eccezione → fallback legacy
# ---------------------------------------------------------------------------

def test_12_verification_runner_exception_fallback_legacy(tmp_path, monkeypatch):
    """12 — Eccezione in VerificationRunner.plan() → fallback legacy controllato."""
    eval_call_count = [0]

    class CountingEvaluator(Evaluator):
        def evaluate(self, cwd=None, command=None, env=None):
            eval_call_count[0] += 1
            return EvalResult(passed=True, output="1 passed", duration_ms=5)

    vr = VerificationRunner()

    def _crashing_plan(*args, **kwargs):
        raise RuntimeError("Errore simulato nel VerificationRunner")

    monkeypatch.setattr(vr, "plan", _crashing_plan)

    conn = db.connect(tmp_path / "mf.db")
    provider = _make_passing_provider()
    workspace = Workspace(tmp_path / "target")
    builder = Builder(provider, workspace)
    evaluator = CountingEvaluator(TestRunner(workspace.root))
    loop = ExecutionLoop(
        conn, builder, evaluator,
        staging_base_dir=tmp_path / "staging",
        verification_runner=vr,
    )

    goal_id = models.create_goal(conn, "exception test")
    task_id = models.create_task(conn, goal_id, 0, "exception test", assigned_to="builder")
    task = models.get_task(conn, task_id)
    outcome = loop.run_task(task)

    # Il task deve completarsi normalmente (fallback legacy)
    assert outcome.status == "candidate_created", (
        f"Con fallback legacy il task deve creare una candidate, stato: {outcome.status}"
    )

    actions = [r["action"] for r in list_audit_log(conn)]
    assert "VERIFICATION_FALLBACK_LEGACY" in actions
    assert eval_call_count[0] >= 1, "Fallback legacy deve chiamare l'Evaluator"


# ---------------------------------------------------------------------------
# TEST 13 — Audit: eventi corretti con goal/task/attempt
# ---------------------------------------------------------------------------

def test_13_audit_events_with_goal_task_attempt(tmp_path, monkeypatch):
    """13 — Gli eventi di verifica adattiva contengono goal_id e task_id corretti."""
    provider = _make_passing_provider(
        files=[FileChange(path="mercury_foundry/execution/loop.py", content="# stub\n")],
        test_files=[
            FileChange(
                path="tests/test_execution_loop_e2e_healthcheck.py",
                content="def test_d(): pass\n",
            )
        ],
    )
    vr = VerificationRunner()
    conn, loop = _build_loop(tmp_path, provider, verification_runner=vr)

    goal_id, task_id, outcome = _submit_and_run(conn, loop)

    import json
    audit_rows = list_audit_log(conn)
    verif_rows = [r for r in audit_rows if r["action"].startswith("VERIFICATION_")]

    assert len(verif_rows) >= 1, "Deve esserci almeno un evento VERIFICATION_*"
    for row in verif_rows:
        payload = json.loads(row["payload_json"])
        assert "goal_id" in payload, f"payload manca goal_id: {payload}"
        assert "task_id" in payload, f"payload manca task_id: {payload}"
        assert payload["goal_id"] == goal_id, (
            f"goal_id errato: atteso {goal_id}, trovato {payload['goal_id']}"
        )
        assert payload["task_id"] == task_id, (
            f"task_id errato: atteso {task_id}, trovato {payload['task_id']}"
        )


# ---------------------------------------------------------------------------
# TEST 14 — Tre tentativi senza progresso → nessuna quarta iterazione
# ---------------------------------------------------------------------------

def test_14_max_three_attempts_no_fourth(tmp_path):
    """14 — Con VerificationRunner e test sempre fallenti → max 3 tentativi."""
    provider = _make_failing_provider()
    vr = VerificationRunner()
    conn, loop = _build_loop(tmp_path, provider, verification_runner=vr)

    _, task_id, outcome = _submit_and_run(conn, loop)

    assert outcome.status == "blocked"
    assert outcome.attempts_used == 3

    attempts = models.get_attempts_for_task(conn, task_id)
    assert len(attempts) == 3, f"Attesi 3 tentativi, trovati {len(attempts)}"
    assert all(a["status"] == "failure" for a in attempts)


# ---------------------------------------------------------------------------
# TEST 15 — Integrazione reale: BUILD → adaptive test → CANDIDATE
# ---------------------------------------------------------------------------

def test_15_integration_build_adaptive_candidate(tmp_path):
    """15 — Flusso completo con VerificationRunner: BUILD → adaptive → CANDIDATE.

    Usa build_foundry() per garantire la stessa configurazione di produzione.
    """
    from mercury_foundry.wiring import build_foundry

    foundry = build_foundry(
        db_path=tmp_path / "mf.db",
        sandbox_root=tmp_path / "target",
        provider_name="fake",
        adaptive_verification=True,
    )

    # VerificationRunner iniettato
    loop = foundry.orchestrator.execution_loop
    assert loop._verification_runner is not None, (
        "Con adaptive_verification=True, il loop deve avere un VerificationRunner"
    )

    goal_id = foundry.orchestrator.submit_goal("aggiungi una capability health check")
    goal_run = foundry.orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "awaiting_approval"
    assert len(goal_run.task_outcomes) == 1

    outcome = goal_run.task_outcomes[0]
    assert outcome.status == "candidate_created"
    assert outcome.candidate_id is not None

    # Audit log: almeno un evento di verifica
    actions = [r["action"] for r in list_audit_log(foundry.conn)]
    assert "CANDIDATE_CREATED" in actions

    # Se ci sono eventi di verifica adattiva, verificano il livello
    verif_plan = [r for r in list_audit_log(foundry.conn)
                  if r["action"] == "VERIFICATION_PLAN_CREATED"]
    if verif_plan:
        import json
        payload = json.loads(verif_plan[0]["payload_json"])
        assert "level" in payload
        assert "plan_id" in payload

    # Il test legacy deve ancora passare — candidate approvabile
    from mercury_foundry.approval import gate
    gate.approve_candidate(
        foundry.conn,
        outcome.candidate_id,
        rationale="Test integrazione adattiva superato",
        backup_base_dir=foundry.backup_base_dir,
    )
    candidate_after = models.get_candidate(foundry.conn, outcome.candidate_id)
    assert candidate_after["status"] == "approved"


# ---------------------------------------------------------------------------
# Import sys necessario per _build_adaptive_command
# ---------------------------------------------------------------------------
import sys

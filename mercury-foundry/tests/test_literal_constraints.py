"""Test del meccanismo GENERALE di enforcement dei vincoli letterali
(`mercury_foundry.policy.literal_constraints`).

Nessuno di questi test fa chiamate reali al provider AI: usano provider fake
deterministici (istanze locali di `AIProvider`, mai `OpenAICompatibleProvider`)
per simulare un provider che diverge, parafrasa o ignora un'istruzione
letterale. Nessuno di questi test scrive in `target_project/` del progetto
reale: usano sempre `tmp_path`.
"""

from __future__ import annotations

import json

import pytest

from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal
from mercury_foundry.policy.errors import LiteralConstraintViolationError
from mercury_foundry.policy.literal_constraints import (
    LiteralConstraints,
    enforce_patch_proposal,
    verify_literal_constraints,
)


def _proposal(files=None, test_files=None) -> PatchProposal:
    return PatchProposal(
        summary="proposta di test",
        files=files or [],
        test_files=test_files or [],
        provider_name="fake-for-test",
        is_simulated=True,
    )


# --- enforce_patch_proposal: correzione deterministica (path+content completi) ----


def test_paraphrased_content_is_corrected_when_path_and_content_fully_specified():
    """Il caso reale osservato in MF-RUN-001B: il provider "parafrasa" il
    contenuto letterale richiesto. Se il vincolo fornisce sia il percorso sia
    il contenuto esatti, il motore corregge deterministicamente, senza
    chiedere nulla al provider."""
    constraints = LiteralConstraints(
        exact_file_path="PROBE.md",
        exact_file_content="# Probe\n\nContenuto letterale esatto richiesto.\n",
    )
    proposal = _proposal(
        files=[FileChange(path="PROBE.md", content="# Probe\n\nQuesto è un riassunto del contenuto.\n")]
    )

    corrected, report = enforce_patch_proposal(proposal, constraints)

    assert report.corrected is True
    assert report.blocked is False
    assert len(corrected.files) == 1
    assert corrected.files[0].path == "PROBE.md"
    assert corrected.files[0].content == constraints.exact_file_content


def test_content_preserved_unchanged_when_provider_already_matches_exactly():
    """Se il provider produce ESATTAMENTE il contenuto richiesto, l'enforcement
    non deve segnalare una correzione superflua (nessuna modifica reale)."""
    constraints = LiteralConstraints(exact_file_path="PROBE.md", exact_file_content="contenuto esatto\n")
    proposal = _proposal(files=[FileChange(path="PROBE.md", content="contenuto esatto\n")])

    corrected, report = enforce_patch_proposal(proposal, constraints)

    assert report.corrected is False
    assert corrected.files[0].content == "contenuto esatto\n"


def test_missing_file_is_injected_when_path_and_content_fully_specified():
    """Se il provider non propone affatto il file protetto, il motore lo
    crea comunque in modo deterministico (mai lasciarlo mancante)."""
    constraints = LiteralConstraints(exact_file_path="PROBE.md", exact_file_content="testo esatto\n")
    proposal = _proposal(files=[FileChange(path="altro.py", content="x = 1\n")])

    corrected, report = enforce_patch_proposal(proposal, constraints)

    assert report.corrected is True
    paths = {c.path for c in corrected.files}
    assert "PROBE.md" in paths
    assert next(c for c in corrected.files if c.path == "PROBE.md").content == "testo esatto\n"
    # Il file non correlato proposto dal provider non viene toccato.
    assert any(c.path == "altro.py" for c in corrected.files)


# --- enforce_patch_proposal: blocco fail-closed (vincolo non pienamente specificato) --


def test_blocks_fail_closed_when_content_diverges_and_path_not_specified():
    """Il vincolo conosce solo il contenuto letterale, non il percorso: se il
    provider non lo riproduce da nessuna parte, il motore non può indovinare
    dove scriverlo, quindi blocca invece di correggere alla cieca."""
    constraints = LiteralConstraints(exact_file_content="testo letterale richiesto\n")
    proposal = _proposal(files=[FileChange(path="qualcosa.md", content="testo diverso\n")])

    corrected, report = enforce_patch_proposal(proposal, constraints)

    assert report.blocked is True
    assert report.block_reason is not None
    assert "exact_file_path" in report.block_reason


def test_no_block_when_content_only_constraint_already_satisfied_somewhere():
    """Se il contenuto richiesto (senza vincolo di percorso) è già presente
    esattamente in uno dei file proposti, non c'è divergenza da correggere."""
    constraints = LiteralConstraints(exact_file_content="testo letterale richiesto\n")
    proposal = _proposal(files=[FileChange(path="qualsiasi.md", content="testo letterale richiesto\n")])

    corrected, report = enforce_patch_proposal(proposal, constraints)

    assert report.blocked is False
    assert corrected.files[0].content == "testo letterale richiesto\n"


# --- enforce_patch_proposal: file extra / allowed_files -----------------------------


def test_forbidden_extra_files_are_dropped_deterministically():
    constraints = LiteralConstraints(
        exact_file_path="PROBE.md",
        exact_file_content="contenuto\n",
        allowed_files=("PROBE.md",),
        forbidden_extra_files=True,
    )
    proposal = _proposal(
        files=[FileChange(path="PROBE.md", content="contenuto\n")],
        test_files=[FileChange(path="tests/test_extra.py", content="def test_x():\n    assert True\n")],
    )

    corrected, report = enforce_patch_proposal(proposal, constraints)

    assert report.blocked is False
    assert "tests/test_extra.py" in report.dropped_files
    assert corrected.test_files == []
    assert len(corrected.files) == 1


def test_allowed_files_list_permits_declared_extra_files():
    constraints = LiteralConstraints(
        exact_file_path="PROBE.md",
        exact_file_content="contenuto\n",
        allowed_files=("PROBE.md", "tests/test_probe.py"),
    )
    proposal = _proposal(
        files=[FileChange(path="PROBE.md", content="contenuto\n")],
        test_files=[FileChange(path="tests/test_probe.py", content="def test_x():\n    assert True\n")],
    )

    corrected, report = enforce_patch_proposal(proposal, constraints)

    assert report.dropped_files == []
    assert len(corrected.test_files) == 1


# --- verify_literal_constraints: post-scrittura --------------------------------------


def test_verify_passes_on_byte_exact_content(tmp_path):
    constraints = LiteralConstraints(exact_file_path="PROBE.md", exact_file_content="riga1\nriga2\n")
    (tmp_path / "PROBE.md").write_text("riga1\nriga2\n", encoding="utf-8")

    result = verify_literal_constraints(tmp_path, constraints)

    assert result.passed is True
    assert result.reasons == []


def test_verify_fails_on_missing_file(tmp_path):
    constraints = LiteralConstraints(exact_file_path="PROBE.md", exact_file_content="testo\n")

    result = verify_literal_constraints(tmp_path, constraints)

    assert result.passed is False
    assert any("mancante" in reason for reason in result.reasons)


def test_verify_flags_stray_quotes_around_content(tmp_path):
    constraints = LiteralConstraints(exact_file_path="PROBE.md", exact_file_content="testo esatto\n")
    (tmp_path / "PROBE.md").write_text('"testo esatto\n"', encoding="utf-8")

    result = verify_literal_constraints(tmp_path, constraints)

    assert result.passed is False
    assert any("virgolette" in reason for reason in result.reasons)


def test_verify_flags_trailing_newline_mismatch(tmp_path):
    constraints = LiteralConstraints(exact_file_path="PROBE.md", exact_file_content="testo esatto\n")
    (tmp_path / "PROBE.md").write_text("testo esatto", encoding="utf-8")  # manca il newline finale

    result = verify_literal_constraints(tmp_path, constraints)

    assert result.passed is False
    assert any("newline" in reason for reason in result.reasons)


def test_verify_fails_on_persistent_extra_file(tmp_path):
    constraints = LiteralConstraints(
        exact_file_path="PROBE.md",
        exact_file_content="testo\n",
        forbidden_extra_files=True,
    )
    (tmp_path / "PROBE.md").write_text("testo\n", encoding="utf-8")
    (tmp_path / "extra.txt").write_text("non dovrebbe esistere\n", encoding="utf-8")

    result = verify_literal_constraints(tmp_path, constraints)

    assert result.passed is False
    assert any("extra.txt" in reason for reason in result.reasons)


def test_verify_ignores_pycache_and_pytest_cache_artifacts(tmp_path):
    constraints = LiteralConstraints(
        exact_file_path="PROBE.md",
        exact_file_content="testo\n",
        forbidden_extra_files=True,
    )
    (tmp_path / "PROBE.md").write_text("testo\n", encoding="utf-8")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "mod.pyc").write_bytes(b"\x00")

    result = verify_literal_constraints(tmp_path, constraints)

    assert result.passed is True


# --- JSON (de)serialization ----------------------------------------------------------


def test_literal_constraints_json_roundtrip():
    original = LiteralConstraints(
        exact_file_path="a/b.md",
        exact_file_content="ciao\n",
        allowed_files=("a/b.md", "tests/test_b.py"),
        forbidden_extra_files=True,
        exact_test_command="python3 -m pytest tests/test_b.py -q",
        byte_exact_required=True,
    )

    restored = LiteralConstraints.from_json(original.to_json())

    assert restored == original


def test_literal_constraints_from_json_none_or_empty_is_none():
    assert LiteralConstraints.from_json(None) is None
    assert LiteralConstraints.from_json("") is None


# --- exact_test_command con variabili d'ambiente anteposte ("NAME=VALUE cmd ...") ---


def test_parsed_test_command_splits_leading_env_assignments():
    """Sintassi comune 'NAME=VALUE comando args...': il motore non usa mai
    una shell, quindi le assegnazioni d'ambiente anteposte vanno riconosciute
    ed estratte esplicitamente invece di essere passate come argv[0] letterale
    (che fallirebbe con FileNotFoundError)."""
    constraints = LiteralConstraints(
        exact_test_command="PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider"
    )

    env, argv = constraints.parsed_test_command()

    assert env == {"PYTHONDONTWRITEBYTECODE": "1"}
    assert argv == ["pytest", "-q", "-p", "no:cacheprovider"]


def test_parsed_test_command_multiple_env_assignments():
    constraints = LiteralConstraints(exact_test_command="A=1 B=2 python3 -m pytest -q")

    env, argv = constraints.parsed_test_command()

    assert env == {"A": "1", "B": "2"}
    assert argv == ["python3", "-m", "pytest", "-q"]


def test_parsed_test_command_without_env_assignments():
    constraints = LiteralConstraints(exact_test_command="pytest -q")

    env, argv = constraints.parsed_test_command()

    assert env == {}
    assert argv == ["pytest", "-q"]


def test_parsed_test_command_none_when_unset():
    constraints = LiteralConstraints()
    assert constraints.parsed_test_command() is None


def test_engine_executes_env_prefixed_exact_test_command_without_shell(tmp_path):
    """Test end-to-end: un `exact_test_command` con variabile d'ambiente
    anteposta (come previsto da MF-PREP-002) deve eseguire realmente pytest
    con quell'override, non fallire con FileNotFoundError."""
    constraints = LiteralConstraints(
        exact_file_path="PROBE.md",
        exact_file_content="contenuto esatto\n",
        allowed_files=("PROBE.md", "test_probe.py"),
        forbidden_extra_files=True,
        exact_test_command="PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider",
    )

    class ExactProviderWithRealTest(AIProvider):
        name = "exact-env-fake"
        is_simulated = True

        def propose_plan(self, goal_description):
            return ["task"]

        def propose_patch(self, task_description, context):
            return PatchProposal(
                summary="ok",
                files=[FileChange(path="PROBE.md", content="contenuto esatto\n")],
                test_files=[
                    FileChange(
                        path="test_probe.py",
                        content=(
                            "from pathlib import Path\n\n"
                            "def test_probe():\n"
                            "    p = Path(__file__).parent / 'PROBE.md'\n"
                            "    assert p.read_text(encoding='utf-8') == 'contenuto esatto\\n'\n"
                        ),
                    )
                ],
                provider_name=self.name,
                is_simulated=True,
            )

    conn, workspace, orchestrator = _build_foundry_with_provider(tmp_path, ExactProviderWithRealTest())
    goal_id = orchestrator.submit_goal("task con env prefix", literal_constraints=constraints)
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "awaiting_approval"
    outcome = goal_run.task_outcomes[0]
    assert outcome.status == "candidate_created"


# --- integrazione con ExecutionLoop / Builder (fail-closed end-to-end) --------------


class _ParaphrasingProvider(AIProvider):
    """Simula esattamente il problema osservato in MF-RUN-001B: propone un
    piano in un solo task e, alla patch, PARAFRASA il contenuto letterale
    richiesto invece di riprodurlo esattamente, e genera un test "sempre
    vero" invece del comando di verifica reale."""

    name = "paraphrasing-fake"
    is_simulated = True

    def propose_plan(self, goal_description: str) -> list[str]:
        return ["crea il file letterale richiesto"]

    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        return PatchProposal(
            summary="Ho creato un file che riassume il contenuto richiesto",
            files=[FileChange(path="PROBE.md", content="Ecco un riassunto di quanto richiesto.\n")],
            test_files=[
                FileChange(
                    path="tests/test_always_true.py",
                    content="def test_always_true():\n    assert 1 + 1 == 2\n",
                )
            ],
            provider_name=self.name,
            is_simulated=True,
        )


def _build_foundry_with_provider(tmp_path, provider):
    from mercury_foundry.agents.builder import Builder
    from mercury_foundry.agents.evaluator import Evaluator
    from mercury_foundry.execution.loop import ExecutionLoop
    from mercury_foundry.orchestrator.orchestrator import Orchestrator
    from mercury_foundry.sandbox.workspace import Workspace
    from mercury_foundry.state import db
    from mercury_foundry.testing.runner import TestRunner

    conn = db.connect(tmp_path / "mercury_foundry.db")
    workspace = Workspace(tmp_path / "target_project")
    builder = Builder(provider, workspace)
    evaluator = Evaluator(TestRunner(workspace.root))
    execution_loop = ExecutionLoop(conn, builder, evaluator, staging_base_dir=tmp_path / "mf_staging")
    orchestrator = Orchestrator(conn, provider, execution_loop)
    return conn, workspace, orchestrator


def test_execution_loop_deterministically_corrects_paraphrased_content(tmp_path):
    """Test end-to-end (nessuna chiamata reale, nessuna scrittura fuori tmp_path):
    con literal_constraints pienamente specificato, la Foundry produce una
    candidate il cui file corrisponde ESATTAMENTE al testo richiesto, anche se
    il provider aveva parafrasato — senza mai chiedere nulla al provider."""
    constraints = LiteralConstraints(
        exact_file_path="PROBE.md",
        exact_file_content="Contenuto letterale esatto richiesto dal test.\n",
        allowed_files=("PROBE.md", "tests/test_always_true.py"),
        forbidden_extra_files=True,
    )
    provider = _ParaphrasingProvider()
    conn, workspace, orchestrator = _build_foundry_with_provider(tmp_path, provider)

    goal_id = orchestrator.submit_goal("crea il probe letterale", literal_constraints=constraints)
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "awaiting_approval"
    outcome = goal_run.task_outcomes[0]
    assert outcome.status == "candidate_created"

    # Da MF-FIX-004: la correzione deterministica vive nello staging isolato
    # della candidate finché non arriva un'approvazione umana esplicita — il
    # target reale resta intatto fino a quel momento.
    from pathlib import Path

    from mercury_foundry.approval import gate
    from mercury_foundry.state import models

    candidate = models.get_candidate(conn, outcome.candidate_id)
    staged = (Path(candidate["staging_root"]) / "PROBE.md").read_text(encoding="utf-8")
    assert staged == constraints.exact_file_content
    assert not (workspace.root / "PROBE.md").exists()

    gate.approve_candidate(conn, outcome.candidate_id, backup_base_dir=tmp_path / "mf_backups")
    written = (workspace.root / "PROBE.md").read_text(encoding="utf-8")
    assert written == constraints.exact_file_content

    from mercury_foundry.audit.logger import list_audit_log

    actions = [row["action"] for row in list_audit_log(conn)]
    assert "LITERAL_CONSTRAINTS_ENFORCED" in actions
    assert "LITERAL_VERIFICATION_COMPLETED" in actions


class _NoPathKnowsWrongContentProvider(AIProvider):
    """Propone un file con un contenuto che NON è quello letterale richiesto,
    per un vincolo che conosce solo il contenuto (non il percorso): non
    correggibile deterministicamente, deve bloccare fail-closed."""

    name = "wrong-content-fake"
    is_simulated = True

    def propose_plan(self, goal_description: str) -> list[str]:
        return ["task singolo"]

    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        return PatchProposal(
            summary="patch che non contiene il contenuto letterale richiesto",
            files=[FileChange(path="qualsiasi.md", content="contenuto completamente diverso\n")],
            provider_name=self.name,
            is_simulated=True,
        )


def test_execution_loop_blocks_fail_closed_when_correction_impossible(tmp_path):
    """Se il vincolo non è pienamente specificato e la proposta diverge, il
    task deve bloccarsi SUBITO (nessun retry automatico, nessuna scrittura in
    sandbox), con la chiamata comunque registrata in provider_calls perché è
    realmente avvenuta (qui il fake non produce ProviderCallRecord, quindi la
    tabella resta vuota, ma il blocco deve avvenire comunque)."""
    constraints = LiteralConstraints(exact_file_content="testo letterale mai riprodotto\n")
    provider = _NoPathKnowsWrongContentProvider()
    conn, workspace, orchestrator = _build_foundry_with_provider(tmp_path, provider)

    goal_id = orchestrator.submit_goal("task che divergerà", literal_constraints=constraints)
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "blocked"
    outcome = goal_run.task_outcomes[0]
    assert outcome.status == "blocked"
    assert outcome.attempts_used == 1  # blocco immediato, nessun retry consumato

    # Fail-closed reale: nessun file scritto nella sandbox.
    assert list(workspace.root.glob("**/*")) == []

    from mercury_foundry.audit.logger import list_audit_log

    actions = [row["action"] for row in list_audit_log(conn)]
    assert "LITERAL_CONSTRAINT_BLOCKED" in actions
    assert "TASK_BLOCKED" in actions


def test_engine_runs_exact_test_command_not_the_providers_generic_test(tmp_path):
    """Regressione diretta del falso positivo osservato in MF-RUN-001B: un
    test "sempre vero" scritto dal provider non deve poter far passare la
    pipeline quando il goal specifica un exact_test_command che verifica
    realmente il contenuto del file."""
    exact_check_test = (
        "from pathlib import Path\n\n\n"
        "def test_probe_has_exact_content():\n"
        "    content = Path(__file__).parent.parent / 'PROBE.md'\n"
        "    assert content.read_text(encoding='utf-8') == "
        "'Contenuto letterale esatto richiesto dal test.\\n'\n"
    )
    constraints = LiteralConstraints(
        exact_file_path="PROBE.md",
        exact_file_content="Contenuto letterale esatto richiesto dal test.\n",
        allowed_files=("PROBE.md", "tests/test_always_true.py", "tests/test_exact_check.py"),
        forbidden_extra_files=True,
        exact_test_command="python3 -m pytest tests/test_exact_check.py -q",
    )

    class ProviderWithoutRealCheck(AIProvider):
        name = "no-real-check-fake"
        is_simulated = True

        def propose_plan(self, goal_description: str) -> list[str]:
            return ["crea probe e test sempre vero"]

        def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
            return PatchProposal(
                summary="patch con test always-true (non verifica nulla di reale)",
                files=[FileChange(path="PROBE.md", content="riassunto diverso dal richiesto\n")],
                test_files=[
                    FileChange(
                        path="tests/test_always_true.py",
                        content="def test_always_true():\n    assert 1 + 1 == 2\n",
                    ),
                    FileChange(path="tests/test_exact_check.py", content=exact_check_test),
                ],
                provider_name=self.name,
                is_simulated=True,
            )

    provider = ProviderWithoutRealCheck()
    conn, workspace, orchestrator = _build_foundry_with_provider(tmp_path, provider)

    goal_id = orchestrator.submit_goal("crea probe con verifica reale", literal_constraints=constraints)
    goal_run = orchestrator.run_goal(goal_id)

    # Il motore corregge il contenuto di PROBE.md a quello esatto PRIMA di
    # eseguire il comando di test esatto, quindi il comando reale passa e la
    # candidate viene creata correttamente (non per merito del test always-true).
    assert goal_run.final_status == "awaiting_approval"
    outcome = goal_run.task_outcomes[0]

    from mercury_foundry.approval import gate
    from mercury_foundry.state import models

    gate.approve_candidate(conn, outcome.candidate_id, backup_base_dir=tmp_path / "mf_backups")
    assert (workspace.root / "PROBE.md").read_text(encoding="utf-8") == constraints.exact_file_content


# --- regressione: il gate umano resta bloccante anche con literal_constraints -------


def test_candidate_still_requires_human_approval_with_literal_constraints(tmp_path):
    constraints = LiteralConstraints(exact_file_path="PROBE.md", exact_file_content="ok\n")
    provider = _ParaphrasingProvider()

    class ExactProvider(AIProvider):
        name = "exact-fake"
        is_simulated = True

        def propose_plan(self, goal_description):
            return ["task"]

        def propose_patch(self, task_description, context):
            return PatchProposal(
                summary="ok",
                files=[FileChange(path="PROBE.md", content="ok\n")],
                test_files=[
                    FileChange(path="tests/test_trivial.py", content="def test_trivial():\n    assert True\n")
                ],
                provider_name=self.name,
                is_simulated=True,
            )

    conn, workspace, orchestrator = _build_foundry_with_provider(tmp_path, ExactProvider())
    goal_id = orchestrator.submit_goal("task semplice", literal_constraints=constraints)
    goal_run = orchestrator.run_goal(goal_id)

    outcome = goal_run.task_outcomes[0]
    from mercury_foundry.state import models

    candidate = models.get_candidate(conn, outcome.candidate_id)
    assert candidate["status"] == "pending_review"  # nessuna auto-approvazione

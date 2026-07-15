"""Test dedicati a MF-FIX-004: candidate staging, rollback completo e
isolamento sicuro dell'esecuzione.

Nessuna chiamata reale al provider AI (solo `AIProvider` fake locali), nessun
uso del provider reale, e nessuna scrittura fuori da `tmp_path`. Copre:
staging-only writes; target byte-identico durante e dopo un fallimento;
non-mutazione del target mentre una candidate è `pending_review`; promozione
atomica; rollback su fallimento di promozione; pulizia post-reject; conflitto
fail-closed; isolamento tra candidate concorrenti; manifest completo;
ambiente di test sanitizzato; redazione dei segreti nell'output; linkage
append-only candidate<->provider_calls.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mercury_foundry.agents.builder import Builder
from mercury_foundry.agents.evaluator import Evaluator
from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal
from mercury_foundry.approval import gate
from mercury_foundry.execution.loop import ExecutionLoop
from mercury_foundry.orchestrator.orchestrator import Orchestrator
from mercury_foundry.policy.errors import TargetConflictError
from mercury_foundry.policy.literal_constraints import LiteralConstraints
from mercury_foundry.sandbox import staging as staging_mod
from mercury_foundry.sandbox.test_env import (
    build_sanitized_test_env,
    collect_secret_values_to_redact,
    is_sensitive_name,
    redact_secrets,
    sanitize_test_output,
)
from mercury_foundry.sandbox.workspace import Workspace
from mercury_foundry.state import db, models


def _tree_hash(root: Path) -> str:
    return staging_mod.compute_snapshot_hash(staging_mod.compute_tree_snapshot(root))


def _build_foundry(tmp_path, provider):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    workspace = Workspace(tmp_path / "target_project")
    builder = Builder(provider, workspace)
    evaluator = Evaluator(_TestRunnerStub())
    execution_loop = ExecutionLoop(conn, builder, evaluator, staging_base_dir=tmp_path / "mf_staging")
    orchestrator = Orchestrator(conn, provider, execution_loop)
    return conn, workspace, orchestrator


class _TestRunnerStub:
    """Evita di lanciare un vero sottoprocesso pytest in ogni test di questo
    file (già coperto altrove): simula un TestRunner reale ma deterministico,
    sempre passante, così questi test restano concentrati sull'isolamento
    dello staging, non sull'esecuzione dei test."""

    def run(self, command=None, env=None, cwd=None):
        from mercury_foundry.testing.runner import TestRunResult

        return TestRunResult(passed=True, output="ok", returncode=0, duration_ms=1)


class _HealthyProvider(AIProvider):
    name = "staging-fake"
    is_simulated = True

    def propose_plan(self, goal_description: str) -> list[str]:
        return ["crea capability.py"]

    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        return PatchProposal(
            summary="capability creata",
            files=[FileChange(path="capability.py", content="def get_value():\n    return 42\n")],
            test_files=[
                FileChange(
                    path="tests/test_capability.py",
                    content="import capability\n\n\ndef test_get_value():\n    assert capability.get_value() == 42\n",
                )
            ],
            provider_name=self.name,
            is_simulated=True,
        )


class _AlwaysFailingProvider(AIProvider):
    name = "always-failing-fake"
    is_simulated = True

    def propose_plan(self, goal_description: str) -> list[str]:
        return ["task che fallisce sempre"]

    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        return PatchProposal(
            summary="patch che fallisce sempre",
            files=[FileChange(path="broken.py", content="x = 1\n")],
            test_files=[FileChange(path="tests/test_broken.py", content="def test_x():\n    assert False\n")],
            provider_name=self.name,
            is_simulated=True,
        )


class _AlwaysFailingTestRunnerStub:
    def run(self, command=None, env=None, cwd=None):
        from mercury_foundry.testing.runner import TestRunResult

        return TestRunResult(passed=False, output="assert False\nFAILED", returncode=1, duration_ms=1)


def _build_foundry_always_failing(tmp_path, provider):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    workspace = Workspace(tmp_path / "target_project")
    builder = Builder(provider, workspace)
    evaluator = Evaluator(_AlwaysFailingTestRunnerStub())
    execution_loop = ExecutionLoop(conn, builder, evaluator, staging_base_dir=tmp_path / "mf_staging")
    orchestrator = Orchestrator(conn, provider, execution_loop)
    return conn, workspace, orchestrator


# --- 1. staging-only writes; target intatto durante BUILD/TEST/VERIFY --------------


def test_target_is_byte_identical_before_and_during_the_attempt(tmp_path):
    """Il target esiste già con del contenuto proprio: dopo un intero ciclo
    BUILD->TEST->VERIFY che produce una candidate, il target non deve avere
    guadagnato o perso un solo byte — tutto è successo nello staging."""
    conn, workspace, orchestrator = _build_foundry(tmp_path, _HealthyProvider())
    workspace.root.mkdir(parents=True, exist_ok=True)
    (workspace.root / "PRESENT.md").write_text("contenuto preesistente\n", encoding="utf-8")
    target_hash_before = _tree_hash(workspace.root)

    goal_id = orchestrator.submit_goal("crea la capability")
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "awaiting_approval"
    assert _tree_hash(workspace.root) == target_hash_before
    assert not (workspace.root / "capability.py").exists()


# --- 2. target byte-identico anche quando il budget di tentativi si esaurisce ------


def test_target_is_byte_identical_after_max_attempts_blocked(tmp_path):
    conn, workspace, orchestrator = _build_foundry_always_failing(tmp_path, _AlwaysFailingProvider())
    workspace.root.mkdir(parents=True, exist_ok=True)
    (workspace.root / "PRESENT.md").write_text("contenuto preesistente\n", encoding="utf-8")
    target_hash_before = _tree_hash(workspace.root)

    goal_id = orchestrator.submit_goal("task che fallisce sempre")
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "blocked"
    assert _tree_hash(workspace.root) == target_hash_before
    # Nessuno staging del tentativo bloccato resta su disco: ogni fallimento
    # scarta subito il proprio staging.
    assert not (tmp_path / "mf_staging" / str(goal_id)).exists() or all(
        not any(p.iterdir()) for p in (tmp_path / "mf_staging" / str(goal_id)).iterdir() if p.is_dir()
    )


# --- 3. pending_review non muta mai il target ---------------------------------------


def test_pending_review_candidate_never_mutates_the_target(tmp_path):
    conn, workspace, orchestrator = _build_foundry(tmp_path, _HealthyProvider())
    goal_id = orchestrator.submit_goal("crea la capability")
    goal_run = orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]

    candidate = models.get_candidate(conn, outcome.candidate_id)
    assert candidate["status"] == "pending_review"
    assert list(workspace.root.glob("**/*")) == []


# --- 4. promozione atomica al momento dell'approvazione ------------------------------


def test_approve_candidate_promotes_staging_diff_atomically(tmp_path):
    conn, workspace, orchestrator = _build_foundry(tmp_path, _HealthyProvider())
    goal_id = orchestrator.submit_goal("crea la capability")
    goal_run = orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]

    gate.approve_candidate(conn, outcome.candidate_id, backup_base_dir=tmp_path / "mf_backups")

    assert (workspace.root / "capability.py").read_text(encoding="utf-8") == "def get_value():\n    return 42\n"
    assert (workspace.root / "tests" / "test_capability.py").exists()
    candidate = models.get_candidate(conn, outcome.candidate_id)
    assert candidate["status"] == "approved"
    # Lo staging, non più necessario dopo la promozione, viene eliminato.
    assert not Path(candidate["staging_root"]).exists()


# --- 5. rollback completo se la promozione fallisce a metà ---------------------------


def test_promote_staging_rolls_back_partial_writes_on_failure(tmp_path):
    """`promote_staging` non lascia mai il target a metà: se una scrittura
    del batch fallisce, tutto ciò che questa chiamata aveva già scritto nel
    target torna al suo stato precedente."""
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    target_root.mkdir()
    staging_root.mkdir()

    (target_root / "existing.txt").write_text("valore originale\n", encoding="utf-8")
    (staging_root / "existing.txt").write_text("valore nuovo\n", encoding="utf-8")
    (staging_root / "new_file.txt").write_text("contenuto nuovo\n", encoding="utf-8")
    # "broken.txt" è nel manifest come da creare, ma non esiste realmente in
    # staging: la sua scrittura fallirà a metà del batch.
    diff = staging_mod.DiffManifest(
        created=["new_file.txt", "broken.txt"],
        modified=["existing.txt"],
        deleted=[],
        final_hashes={},
        final_sizes={},
    )

    with pytest.raises(Exception):
        staging_mod.promote_staging(staging_root, target_root, diff)

    # Rollback completo: il target è tornato esattamente come prima.
    assert (target_root / "existing.txt").read_text(encoding="utf-8") == "valore originale\n"
    assert not (target_root / "new_file.txt").exists()
    assert not (target_root / "broken.txt").exists()


# --- 6. reject pulisce lo staging e non tocca mai il target --------------------------


def test_reject_candidate_discards_staging_and_never_touches_target(tmp_path):
    conn, workspace, orchestrator = _build_foundry(tmp_path, _HealthyProvider())
    goal_id = orchestrator.submit_goal("crea la capability")
    goal_run = orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]

    candidate_before = models.get_candidate(conn, outcome.candidate_id)
    staging_root = Path(candidate_before["staging_root"])
    assert staging_root.exists()

    gate.reject_candidate(conn, outcome.candidate_id, rationale="non conforme")

    assert not staging_root.exists()
    assert list(workspace.root.glob("**/*")) == []
    candidate_after = models.get_candidate(conn, outcome.candidate_id)
    assert candidate_after["status"] == "rejected"


# --- 7. conflitto fail-closed: il target è cambiato dopo la creazione della candidate --


def test_approve_candidate_blocks_fail_closed_on_target_conflict(tmp_path):
    conn, workspace, orchestrator = _build_foundry(tmp_path, _HealthyProvider())
    goal_id = orchestrator.submit_goal("crea la capability")
    goal_run = orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]

    # Il target cambia DOPO la creazione della candidate (es. un'altra
    # candidate è stata promossa nel frattempo, o una modifica manuale).
    workspace.root.mkdir(parents=True, exist_ok=True)
    (workspace.root / "changed_after_candidate.md").write_text("sorpresa\n", encoding="utf-8")

    with pytest.raises(TargetConflictError):
        gate.approve_candidate(conn, outcome.candidate_id, backup_base_dir=tmp_path / "mf_backups")

    # Fail-closed: nessuna scrittura, la candidate resta pending_review, e il
    # cambiamento "sorpresa" del target non è stato toccato né sovrascritto.
    candidate = models.get_candidate(conn, outcome.candidate_id)
    assert candidate["status"] == "pending_review"
    assert (workspace.root / "changed_after_candidate.md").exists()
    assert not (workspace.root / "capability.py").exists()


# --- 8. nessuna sovrascrittura silenziosa tra candidate concorrenti -----------------


def test_two_candidates_from_different_tasks_have_independent_staging(tmp_path):
    """Due goal distinti (quindi due `run_id` distinti) non condividono mai
    lo stesso staging: promuovere l'uno non deve poter interferire con lo
    staging, ancora `pending_review`, dell'altro."""
    conn, workspace, orchestrator = _build_foundry(tmp_path, _HealthyProvider())

    goal_id_a = orchestrator.submit_goal("crea la capability (goal A)")
    run_a = orchestrator.run_goal(goal_id_a)
    candidate_a = models.get_candidate(conn, run_a.task_outcomes[0].candidate_id)

    goal_id_b = orchestrator.submit_goal("crea la capability (goal B)")
    run_b = orchestrator.run_goal(goal_id_b)
    candidate_b = models.get_candidate(conn, run_b.task_outcomes[0].candidate_id)

    assert candidate_a["staging_root"] != candidate_b["staging_root"]
    assert Path(candidate_a["staging_root"]).exists()
    assert Path(candidate_b["staging_root"]).exists()

    gate.approve_candidate(conn, candidate_a["id"], backup_base_dir=tmp_path / "mf_backups")

    # Lo staging della candidate B (non ancora approvata) è ancora intatto.
    assert Path(candidate_b["staging_root"]).exists()
    assert (Path(candidate_b["staging_root"]) / "capability.py").exists()
    candidate_b_after = models.get_candidate(conn, candidate_b["id"])
    assert candidate_b_after["status"] == "pending_review"


# --- 9. manifest completo e verificabile --------------------------------------------


def test_candidate_manifest_is_complete_and_verifiable(tmp_path):
    conn, workspace, orchestrator = _build_foundry(tmp_path, _HealthyProvider())
    goal_id = orchestrator.submit_goal("crea la capability")
    goal_run = orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]

    candidate = models.get_candidate(conn, outcome.candidate_id)
    manifest = json.loads(candidate["manifest_json"])

    assert manifest["run_id"] == str(goal_id)
    assert manifest["task_id"] == outcome.task_id
    assert manifest["provider_name"] == "staging-fake"
    assert manifest["is_simulated"] is True
    assert manifest["target_snapshot_hash"] == candidate["target_snapshot_hash"]
    assert sorted(manifest["files"]["created"]) == sorted(["capability.py", "tests/test_capability.py"])
    assert manifest["test_result"]["passed"] is True
    assert manifest["verify_result"]["passed"] is True
    assert "created_at" in manifest


# --- 10. required_files vs allowed_files: BUILD incompleta non consuma staging -----


def test_build_incomplete_discards_staging_before_test(tmp_path):
    class _MissingRequiredFileProvider(AIProvider):
        name = "missing-required-fake"
        is_simulated = True

        def propose_plan(self, goal_description: str) -> list[str]:
            return ["task"]

        def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
            return PatchProposal(
                summary="manca il file richiesto",
                files=[FileChange(path="only_this.py", content="x = 1\n")],
                provider_name=self.name,
                is_simulated=True,
            )

    constraints = LiteralConstraints(required_files=("only_this.py", "tests/test_only_this.py"))
    conn, workspace, orchestrator = _build_foundry(tmp_path, _MissingRequiredFileProvider())
    goal_id = orchestrator.submit_goal("task incompleto", literal_constraints=constraints)
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "blocked"
    # Nessun staging lasciato su disco per questo tentativo bloccato.
    staging_dir = tmp_path / "mf_staging" / str(goal_id) / "1"
    assert not staging_dir.exists()


# --- 11. ambiente di test sanitizzato: nessun segreto reale filtra dentro ----------


def test_sanitized_test_env_never_includes_secret_names(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-appear")
    monkeypatch.setenv("SESSION_SECRET", "super-secret-value")
    monkeypatch.setenv("SOME_CUSTOM_TOKEN", "another-secret")

    env = build_sanitized_test_env(
        home_dir=tmp_path / "home",
        tmp_dir=tmp_path / "tmp",
        extra={"OPENAI_API_KEY": "sk-injected-anyway", "SAFE_VAR": "fine"},
    )

    assert "OPENAI_API_KEY" not in env
    assert "SESSION_SECRET" not in env
    assert "SOME_CUSTOM_TOKEN" not in env
    assert env.get("SAFE_VAR") == "fine"
    assert is_sensitive_name("OPENAI_API_KEY") is True
    assert is_sensitive_name("MY_REFRESH_TOKEN") is True
    assert is_sensitive_name("SAFE_VAR") is False


# --- 12. redazione dei segreti nell'output prima della persistenza -----------------


def test_output_redaction_removes_real_secret_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-me-not")
    secrets = collect_secret_values_to_redact()
    output = "Traceback: connecting with key sk-leak-me-not failed\n"

    redacted = redact_secrets(output, secrets)

    assert "sk-leak-me-not" not in redacted
    assert "[REDACTED]" in redacted

    safe_output, truncated = sanitize_test_output(output)
    assert "sk-leak-me-not" not in safe_output
    assert truncated is False


def test_output_truncation_is_flagged_not_silent():
    long_output = "x" * 50_000
    safe_output, truncated = sanitize_test_output(long_output, max_chars=100)
    assert truncated is True
    assert "TRUNCATED" in safe_output
    assert len(safe_output) < len(long_output)


# --- 13. linkage append-only candidate<->provider_calls -----------------------------


def test_candidate_provider_call_linkage_is_append_only_and_idempotent(tmp_path):
    """`_HealthyProvider` è un fake locale senza chiamata reale (nessun
    `ProviderCallRecord`), quindi qui si inserisce direttamente una riga
    `provider_calls` per il task, per testare l'associazione append-only in
    isolamento dal resto del ciclo BUILD->TEST->VERIFY."""
    conn, workspace, orchestrator = _build_foundry(tmp_path, _HealthyProvider())
    goal_id = orchestrator.submit_goal("crea la capability")
    goal_run = orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]

    conn.execute(
        """
        INSERT INTO provider_calls (
            run_id, goal_id, task_id, attempt_id, operation, provider_name, model,
            call_number, is_simulated, success, requested_at, created_at
        ) VALUES (?, ?, ?, NULL, 'PATCH', 'staging-fake', 'n/a', 1, 1, 1, ?, ?)
        """,
        (str(goal_id), goal_id, outcome.task_id, models._now(), models._now()),
    )
    conn.commit()

    run_id = str(goal_id)
    models.associate_candidate_provider_calls(conn, run_id, outcome.candidate_id)
    linked_calls = models.list_candidate_provider_calls(conn, outcome.candidate_id)
    assert len(linked_calls) >= 1

    rows_before = conn.execute("SELECT * FROM candidate_provider_calls ORDER BY id").fetchall()

    # Rilanciare l'associazione per lo stesso (run, candidate) non deve
    # produrre righe duplicate né modificare quelle esistenti.
    models.associate_candidate_provider_calls(conn, run_id, outcome.candidate_id)
    rows_after = conn.execute("SELECT * FROM candidate_provider_calls ORDER BY id").fetchall()
    assert len(rows_after) == len(rows_before)

    # provider_calls resta append-only: nessuna funzione di update/delete
    # esposta contro quella tabella.
    assert not hasattr(models, "attach_candidate_to_provider_calls")


def test_no_retroactive_update_of_provider_calls_table(tmp_path):
    """Il modulo non deve più contenere alcuna funzione che scriva
    (UPDATE) sulla tabella provider_calls: l'unica scrittura ammessa è
    l'INSERT append-only fatto al momento della chiamata reale."""
    import inspect

    from mercury_foundry.state import models as models_module

    source = inspect.getsource(models_module)
    assert "UPDATE provider_calls" not in source


# --- 14. end-to-end mockato: pending_review -> approved, target coerente ----------


def test_full_mocked_end_to_end_happy_path_to_approved(tmp_path):
    conn, workspace, orchestrator = _build_foundry(tmp_path, _HealthyProvider())
    goal_id = orchestrator.submit_goal("crea la capability end-to-end")
    goal_run = orchestrator.run_goal(goal_id)

    assert goal_run.final_status == "awaiting_approval"
    outcome = goal_run.task_outcomes[0]
    candidate = models.get_candidate(conn, outcome.candidate_id)
    assert candidate["status"] == "pending_review"
    assert not (workspace.root / "capability.py").exists()

    gate.approve_candidate(
        conn, outcome.candidate_id, rationale="approvato in test", backup_base_dir=tmp_path / "mf_backups"
    )

    candidate_after = models.get_candidate(conn, outcome.candidate_id)
    assert candidate_after["status"] == "approved"
    assert (workspace.root / "capability.py").exists()
    goal_after = models.get_goal(conn, goal_id)
    assert goal_after["status"] == "done"

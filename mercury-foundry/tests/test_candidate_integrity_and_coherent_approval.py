"""Test dedicati a MF-FIX-005: integrità immutabile della candidate e
approvazione coerente (filesystem + DB).

Copre i tre gap individuati nell'audit di MF-FIX-004:
1. riverifica dell'integrità dello STAGING (non solo del target) prima della
   promozione, con blocco fail-closed su qualunque alterazione;
2. procedura di approvazione coordinata (backup restorabile, promozione
   filesystem, poi UNA transazione DB) con rollback/`recovery_required`
   quando un passo dopo la scrittura sul filesystem fallisce;
3. rendicontazione completa run_id-based delle provider_calls (inclusa la
   chiamata PLAN, che ha `task_id` NULL).

Nessuna chiamata reale al provider AI (solo `AIProvider` fake locali), nessun
uso di MF-RUN-003, nessuna approvazione/promozione di una candidate reale
esistente, e nessuna scrittura fuori da `tmp_path`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury_foundry.agents.builder import Builder
from mercury_foundry.agents.evaluator import Evaluator
from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal
from mercury_foundry.approval import gate
from mercury_foundry.audit.logger import list_audit_log, log_action
from mercury_foundry.execution.loop import ExecutionLoop
from mercury_foundry.orchestrator.orchestrator import Orchestrator
from mercury_foundry.policy.errors import CandidateIntegrityError, CandidateRecoveryRequiredError, TargetConflictError
from mercury_foundry.sandbox import staging as staging_mod
from mercury_foundry.sandbox.workspace import Workspace
from mercury_foundry.state import db, models
from mercury_foundry.testing.runner import TestRunResult


class _StubRunner:
    def run(self, command=None, env=None, cwd=None):
        return TestRunResult(passed=True, output="ok", returncode=0, duration_ms=1)


class _HealthyProvider(AIProvider):
    name = "integrity-fake"
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


def _build_foundry(tmp_path, provider=None):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    workspace = Workspace(tmp_path / "target_project")
    provider = provider or _HealthyProvider()
    builder = Builder(provider, workspace)
    evaluator = Evaluator(_StubRunner())
    execution_loop = ExecutionLoop(conn, builder, evaluator, staging_base_dir=tmp_path / "mf_staging")
    orchestrator = Orchestrator(conn, provider, execution_loop)
    return conn, workspace, orchestrator


def _create_candidate(tmp_path, provider=None):
    conn, workspace, orchestrator = _build_foundry(tmp_path, provider)
    goal_id = orchestrator.submit_goal("crea la capability")
    goal_run = orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]
    candidate = models.get_candidate(conn, outcome.candidate_id)
    return conn, workspace, goal_id, candidate


class _FailingConnProxy:
    """Wrapper attorno a una connessione sqlite3 reale che fa fallire la
    PRIMA `execute` la cui SQL contiene `fail_when_sql_contains`, per
    simulare un guasto del DB a metà di una transazione già iniziata.
    Delega tutto il resto (incluso `commit`/`rollback`) alla connessione
    reale, così il rollback annulla davvero le scritture non commesse."""

    def __init__(self, real_conn, fail_when_sql_contains: str):
        self._real = real_conn
        self._fail_when = fail_when_sql_contains
        self._already_failed = False

    def execute(self, sql, params=()):
        if not self._already_failed and self._fail_when in sql:
            self._already_failed = True
            raise RuntimeError(f"guasto DB simulato su: {self._fail_when}")
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


BACKUP_DIR_NAME = "mf_backups"


# --- 1. integrità dello staging: caso positivo (nessuna alterazione) --------------


def test_verify_staging_integrity_passes_when_staging_is_unchanged(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    manifest = json.loads(candidate["manifest_json"])
    result = staging_mod.verify_staging_integrity(Path(candidate["staging_root"]), manifest["staging_manifest"])
    assert result.passed is True
    assert result.reasons == []

    # E l'approvazione, di conseguenza, riesce senza bloccarsi.
    gate.approve_candidate(conn, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)
    assert models.get_candidate(conn, candidate["id"])["status"] == "approved"


# --- 2. contenuto alterato nello staging dopo la creazione della candidate --------


def test_tampered_staging_content_blocks_approval(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    staging_root = Path(candidate["staging_root"])
    # Lo staging è read-only (difesa in profondità di `make_read_only`): un
    # "tamper" esterno che aggira quella protezione (es. un processo con
    # accesso diretto al disco) è esattamente ciò che `verify_staging_integrity`
    # deve comunque rilevare — da qui il `make_writable` esplicito nel test.
    staging_mod.make_writable(staging_root)
    (staging_root / "capability.py").write_text("def get_value():\n    return 999999\n", encoding="utf-8")

    with pytest.raises(CandidateIntegrityError):
        gate.approve_candidate(conn, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate_after = models.get_candidate(conn, candidate["id"])
    assert candidate_after["status"] == "pending_review"
    assert not (workspace.root / "capability.py").exists()
    # Lo staging (manomesso) resta su disco per diagnosi, NON viene scartato.
    assert staging_root.exists()
    actions = [row["action"] for row in list_audit_log(conn)]
    assert "CANDIDATE_INTEGRITY_VIOLATION" in actions


# --- 3. file extra aggiunto nello staging dopo la creazione della candidate -------


def test_extra_file_in_staging_blocks_approval(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    staging_root = Path(candidate["staging_root"])
    staging_mod.make_writable(staging_root)
    (staging_root / "unexpected_extra.txt").write_text("non doveva esserci\n", encoding="utf-8")

    with pytest.raises(CandidateIntegrityError):
        gate.approve_candidate(conn, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate_after = models.get_candidate(conn, candidate["id"])
    assert candidate_after["status"] == "pending_review"
    assert not (workspace.root / "unexpected_extra.txt").exists()


# --- 4. file rimosso dallo staging dopo la creazione della candidate -------------


def test_missing_file_removed_from_staging_blocks_approval(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    staging_root = Path(candidate["staging_root"])
    staging_mod.make_writable(staging_root)
    (staging_root / "tests" / "test_capability.py").unlink()

    with pytest.raises(CandidateIntegrityError):
        gate.approve_candidate(conn, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate_after = models.get_candidate(conn, candidate["id"])
    assert candidate_after["status"] == "pending_review"
    assert not (workspace.root / "capability.py").exists()


# --- 5. read-only best-effort dopo la creazione della candidate ------------------


def test_staging_permissions_are_made_read_only_after_candidate_created(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    staging_root = Path(candidate["staging_root"])
    capability_file = staging_root / "capability.py"

    mode = capability_file.stat().st_mode & 0o777
    # Nessun bit di scrittura per alcuna classe (owner/group/other): 0o444.
    assert mode & 0o222 == 0


# --- 6. fallimento della promozione: nessuna scrittura resta a metà -------------


def test_promotion_failure_leaves_candidate_pending_and_discards_backup(tmp_path, monkeypatch):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("guasto I/O simulato durante la promozione")

    monkeypatch.setattr(gate, "promote_staging", _boom)

    with pytest.raises(OSError):
        gate.approve_candidate(conn, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate_after = models.get_candidate(conn, candidate["id"])
    assert candidate_after["status"] == "pending_review"
    assert candidate_after["backup_root"] is None
    assert not (workspace.root / "capability.py").exists()
    actions = [row["action"] for row in list_audit_log(conn)]
    assert "CANDIDATE_PROMOTION_FAILED" in actions


# --- 7/9. guasto DB (decision o audit) DOPO la promozione: rollback completo ------


@pytest.mark.parametrize("fail_sql", ["INSERT INTO decisions", "INSERT INTO audit_log"])
def test_db_failure_after_promotion_restores_target_from_backup(tmp_path, fail_sql):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    staging_root = Path(candidate["staging_root"])
    proxy = _FailingConnProxy(conn, fail_sql)

    with pytest.raises(RuntimeError):
        gate.approve_candidate(proxy, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    # Rollback completo: né il target né lo stato della candidate mostrano
    # una promozione "a metà".
    candidate_after = models.get_candidate(conn, candidate["id"])
    assert candidate_after["status"] == "pending_review"
    assert not (workspace.root / "capability.py").exists()
    # Lo staging non viene scartato: la candidate resta approvabile di nuovo.
    assert staging_root.exists()
    actions = [row["action"] for row in list_audit_log(conn)]
    assert "CANDIDATE_PROMOTION_DB_FAILURE_ROLLED_BACK" in actions

    # Una seconda approvazione, senza il guasto simulato, riesce normalmente:
    # dimostra che il rollback non ha lasciato uno stato corrotto.
    gate.approve_candidate(conn, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)
    assert models.get_candidate(conn, candidate["id"])["status"] == "approved"
    assert (workspace.root / "capability.py").exists()


# --- 10. il ripristino stesso fallisce: recovery_required, nessuna pulizia --------


def test_failure_during_restore_yields_recovery_required_and_preserves_evidence(tmp_path, monkeypatch):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    staging_root = Path(candidate["staging_root"])
    proxy = _FailingConnProxy(conn, "INSERT INTO decisions")
    monkeypatch.setattr(
        gate, "restore_backup", lambda *a, **kw: (_ for _ in ()).throw(OSError("ripristino simulato fallito"))
    )

    with pytest.raises(CandidateRecoveryRequiredError):
        gate.approve_candidate(proxy, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate_after = models.get_candidate(conn, candidate["id"])
    assert candidate_after["status"] == "recovery_required"
    # Backup e staging preservati per la diagnosi: nessuna pulizia automatica.
    assert candidate_after["backup_root"] is not None
    assert Path(candidate_after["backup_root"]).exists()
    assert staging_root.exists()
    actions = [row["action"] for row in list_audit_log(conn)]
    assert "CANDIDATE_RECOVERY_REQUIRED" in actions

    # Una candidate recovery_required non è approvabile né rifiutabile fino a
    # una risoluzione manuale esplicita: nessun retry automatico.
    with pytest.raises(gate.InvalidCandidateStateError):
        gate.approve_candidate(conn, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)


# --- 11. approve idempotente -----------------------------------------------------


def test_approve_candidate_is_idempotent(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    backup_base_dir = tmp_path / BACKUP_DIR_NAME

    gate.approve_candidate(conn, candidate["id"], backup_base_dir=backup_base_dir)
    content_after_first = (workspace.root / "capability.py").read_text(encoding="utf-8")
    decisions_before = conn.execute("SELECT * FROM decisions").fetchall()
    audit_before = list_audit_log(conn, limit=1000)

    # Seconda chiamata: nessuna eccezione, nessuna riscrittura, nessun duplicato.
    gate.approve_candidate(conn, candidate["id"], backup_base_dir=backup_base_dir)

    assert (workspace.root / "capability.py").read_text(encoding="utf-8") == content_after_first
    decisions_after = conn.execute("SELECT * FROM decisions").fetchall()
    assert len(decisions_after) == len(decisions_before)
    audit_after = list_audit_log(conn, limit=1000)
    assert len(audit_after) == len(audit_before) + 1
    assert audit_after[-1]["action"] == "CANDIDATE_APPROVE_NOOP_ALREADY_APPROVED"


# --- 12. reject idempotente ------------------------------------------------------


def test_reject_candidate_is_idempotent(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)

    gate.reject_candidate(conn, candidate["id"], rationale="non conforme")
    decisions_before = conn.execute("SELECT * FROM decisions").fetchall()
    audit_before = list_audit_log(conn, limit=1000)

    gate.reject_candidate(conn, candidate["id"], rationale="non conforme di nuovo")

    decisions_after = conn.execute("SELECT * FROM decisions").fetchall()
    assert len(decisions_after) == len(decisions_before)
    audit_after = list_audit_log(conn, limit=1000)
    assert len(audit_after) == len(audit_before) + 1
    assert audit_after[-1]["action"] == "CANDIDATE_REJECT_NOOP_ALREADY_REJECTED"


# --- 13. una candidate rifiutata non può mai più essere approvata ---------------


def test_rejected_candidate_can_never_be_approved(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    gate.reject_candidate(conn, candidate["id"], rationale="non conforme")

    with pytest.raises(gate.InvalidCandidateStateError):
        gate.approve_candidate(conn, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    assert models.get_candidate(conn, candidate["id"])["status"] == "rejected"
    assert not (workspace.root / "capability.py").exists()


# --- 14/15. chiamata PLAN (task_id NULL) collegata via run_id, senza duplicati ----


def test_plan_call_with_null_task_id_is_linked_via_run_id_exactly_once(tmp_path):
    conn, workspace, orchestrator = _build_foundry(tmp_path)
    goal_id = orchestrator.submit_goal("crea la capability")
    run_id = str(goal_id)

    # Simula una chiamata PLAN reale (task_id NULL): l'Orchestrator/PLAN reale
    # persisterebbe una riga così PRIMA che un task esista.
    conn.execute(
        """
        INSERT INTO provider_calls (
            run_id, goal_id, task_id, attempt_id, operation, provider_name, model,
            call_number, is_simulated, success, usage_json, estimated_cost_usd,
            requested_at, created_at
        ) VALUES (?, ?, NULL, NULL, 'PLAN', 'integrity-fake', 'n/a', 1, 1, 1, ?, 0.01, ?, ?)
        """,
        (run_id, goal_id, json.dumps({"total_tokens": 100}), models._now(), models._now()),
    )
    conn.commit()

    goal_run = orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]
    candidate = models.get_candidate(conn, outcome.candidate_id)

    linked_calls = models.list_candidate_provider_calls(conn, candidate["id"])
    operations = [c["operation"] for c in linked_calls]
    assert "PLAN" in operations, "la chiamata PLAN (task_id NULL) deve restare collegata alla candidate"

    # Nessun duplicato: esattamente una riga di linkage per la chiamata PLAN.
    plan_links = conn.execute(
        """
        SELECT cpc.id FROM candidate_provider_calls cpc
        JOIN provider_calls pc ON pc.id = cpc.provider_call_id
        WHERE pc.operation = 'PLAN' AND cpc.candidate_id = ?
        """,
        (candidate["id"],),
    ).fetchall()
    assert len(plan_links) == 1

    # Rilanciare l'associazione non duplica nulla (idempotenza già testata
    # altrove per il caso generico; qui si conferma anche per PLAN).
    models.associate_candidate_provider_calls(conn, run_id, candidate["id"])
    plan_links_after = conn.execute(
        """
        SELECT cpc.id FROM candidate_provider_calls cpc
        JOIN provider_calls pc ON pc.id = cpc.provider_call_id
        WHERE pc.operation = 'PLAN' AND cpc.candidate_id = ?
        """,
        (candidate["id"],),
    ).fetchall()
    assert len(plan_links_after) == 1


# --- 16. token/costo totali corretti per un intero run ---------------------------


def test_candidate_manifest_totals_tokens_and_cost_across_plan_and_build(tmp_path):
    conn, workspace, orchestrator = _build_foundry(tmp_path)
    goal_id = orchestrator.submit_goal("crea la capability")
    run_id = str(goal_id)

    # PLAN: 100 token, $0.01. BUILD (task_id NOT NULL): 250 token, $0.02.
    conn.execute(
        """
        INSERT INTO provider_calls (
            run_id, goal_id, task_id, attempt_id, operation, provider_name, model,
            call_number, is_simulated, success, usage_json, estimated_cost_usd,
            requested_at, created_at
        ) VALUES (?, ?, NULL, NULL, 'PLAN', 'integrity-fake', 'n/a', 1, 1, 1, ?, 0.01, ?, ?)
        """,
        (run_id, goal_id, json.dumps({"total_tokens": 100}), models._now(), models._now()),
    )
    conn.commit()

    goal_run = orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]

    conn.execute(
        """
        INSERT INTO provider_calls (
            run_id, goal_id, task_id, attempt_id, operation, provider_name, model,
            call_number, is_simulated, success, usage_json, estimated_cost_usd,
            requested_at, created_at
        ) VALUES (?, ?, ?, NULL, 'PATCH', 'integrity-fake', 'n/a', 2, 1, 1, ?, 0.02, ?, ?)
        """,
        (run_id, goal_id, outcome.task_id, json.dumps({"total_tokens": 250}), models._now(), models._now()),
    )
    conn.commit()
    models.associate_candidate_provider_calls(conn, run_id, outcome.candidate_id)

    linked_calls = models.list_candidate_provider_calls(conn, outcome.candidate_id)
    total_tokens = sum((json.loads(c["usage_json"]) or {}).get("total_tokens", 0) for c in linked_calls if c["usage_json"])
    total_cost = sum(c["estimated_cost_usd"] for c in linked_calls if c["estimated_cost_usd"] is not None)

    assert total_tokens == 350
    assert round(total_cost, 2) == 0.03


# --- 17. due candidate dallo stesso snapshot iniziale: la seconda rileva il conflitto -


def test_second_of_two_candidates_from_same_initial_target_detects_conflict(tmp_path):
    conn, workspace, orchestrator = _build_foundry(tmp_path)
    workspace.root.mkdir(parents=True, exist_ok=True)
    (workspace.root / "PRESENT.md").write_text("stato iniziale condiviso\n", encoding="utf-8")

    goal_id_a = orchestrator.submit_goal("crea la capability (A)")
    run_a = orchestrator.run_goal(goal_id_a)
    candidate_a = models.get_candidate(conn, run_a.task_outcomes[0].candidate_id)

    goal_id_b = orchestrator.submit_goal("crea la capability (B)")
    run_b = orchestrator.run_goal(goal_id_b)
    candidate_b = models.get_candidate(conn, run_b.task_outcomes[0].candidate_id)

    # Entrambe le candidate sono state create dallo stesso stato del target.
    assert candidate_a["target_snapshot_hash"] == candidate_b["target_snapshot_hash"]

    gate.approve_candidate(conn, candidate_a["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    with pytest.raises(TargetConflictError):
        gate.approve_candidate(conn, candidate_b["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    assert models.get_candidate(conn, candidate_b["id"])["status"] == "pending_review"


# --- 18/19. pulizia dopo approvazione/rifiuto ------------------------------------


def test_cleanup_after_successful_approval_removes_backup_and_staging(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    staging_root = Path(candidate["staging_root"])
    backup_base_dir = tmp_path / BACKUP_DIR_NAME

    gate.approve_candidate(conn, candidate["id"], backup_base_dir=backup_base_dir)

    candidate_after = models.get_candidate(conn, candidate["id"])
    assert candidate_after["backup_root"] is None
    assert not staging_root.exists()
    # Nessun file di backup superstite (possono restare directory vuote del
    # run_id, non un problema: nessun dato residuo, nessuna scrittura persa).
    if backup_base_dir.exists():
        assert not any(p.is_file() for p in backup_base_dir.rglob("*"))


def test_cleanup_after_rejection_removes_staging_only(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    staging_root = Path(candidate["staging_root"])

    gate.reject_candidate(conn, candidate["id"], rationale="non conforme")

    assert not staging_root.exists()
    assert not (workspace.root / "capability.py").exists()


# --- 20. lo staging resta preservato dopo un fallimento di integrità ------------


def test_staging_preserved_for_diagnosis_after_integrity_failure(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    staging_root = Path(candidate["staging_root"])
    staging_mod.make_writable(staging_root)
    (staging_root / "capability.py").write_text("manomesso\n", encoding="utf-8")

    with pytest.raises(CandidateIntegrityError):
        gate.approve_candidate(conn, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    assert staging_root.exists()
    assert (staging_root / "capability.py").read_text(encoding="utf-8") == "manomesso\n"


# --- 21/22. end-to-end mockato: happy path e guasto DB con ripristino ------------


def test_full_mocked_end_to_end_happy_path_through_db_commit_to_approved(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    assert candidate["status"] == "pending_review"

    gate.approve_candidate(conn, candidate["id"], rationale="ok", backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate_after = models.get_candidate(conn, candidate["id"])
    assert candidate_after["status"] == "approved"
    assert candidate_after["backup_root"] is None
    assert (workspace.root / "capability.py").exists()
    goal_after = models.get_goal(conn, goal_id)
    assert goal_after["status"] == "done"


def test_full_mocked_end_to_end_with_db_failure_and_target_restore(tmp_path):
    conn, workspace, goal_id, candidate = _create_candidate(tmp_path)
    proxy = _FailingConnProxy(conn, "INSERT INTO decisions")

    with pytest.raises(RuntimeError):
        gate.approve_candidate(proxy, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate_after = models.get_candidate(conn, candidate["id"])
    assert candidate_after["status"] == "pending_review"
    assert not (workspace.root / "capability.py").exists()
    goal_after = models.get_goal(conn, goal_id)
    assert goal_after["status"] != "done"

"""Test dedicati a MF-FIX-006: blocco fail-closed delle candidate legacy
prive di staging (o comunque prive delle garanzie introdotte da
MF-FIX-004/MF-FIX-005).

L'audit del commit successivo a MF-FIX-005 ha trovato un solo bypass ancora
bloccante: `approve_candidate` permetteva ancora a una candidate senza
`staging_root`/`target_snapshot_hash`/`target_root`/manifest completi di
diventare `approved` tramite una semplice transazione DB. Questo file prova
che quel percorso è stato rimosso e che l'unico esito possibile per una
candidate legacy è `LegacyCandidateNotPromotableError` (approvazione) o un
rifiuto manuale esplicito (`reject_candidate`, sempre disponibile).

Nessuna chiamata reale al provider AI, nessun uso di MF-RUN-003, nessuna
approvazione/promozione di una candidate reale esistente, nessuna scrittura
fuori da `tmp_path`, nessuna modifica a `target_project`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury_foundry.agents.builder import Builder
from mercury_foundry.agents.evaluator import Evaluator
from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal
from mercury_foundry.approval import gate
from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.execution.loop import ExecutionLoop
from mercury_foundry.orchestrator.orchestrator import Orchestrator
from mercury_foundry.policy.errors import LegacyCandidateNotPromotableError
from mercury_foundry.sandbox.workspace import Workspace
from mercury_foundry.state import db, models
from mercury_foundry.testing.runner import TestRunResult

BACKUP_DIR_NAME = "mf_backups"


class _StubRunner:
    def run(self, command=None, env=None, cwd=None):
        return TestRunResult(passed=True, output="ok", returncode=0, duration_ms=1)


class _HealthyProvider(AIProvider):
    name = "legacy-fake"
    is_simulated = True

    def propose_plan(self, goal_description: str) -> list[str]:
        return ["crea capability.py"]

    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        return PatchProposal(
            summary="capability creata",
            files=[FileChange(path="capability.py", content="def get_value():\n    return 7\n")],
            test_files=[
                FileChange(
                    path="tests/test_capability.py",
                    content="import capability\n\n\ndef test_get_value():\n    assert capability.get_value() == 7\n",
                )
            ],
            provider_name=self.name,
            is_simulated=True,
        )


def _build_foundry(tmp_path):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    workspace = Workspace(tmp_path / "target_project")
    provider = _HealthyProvider()
    builder = Builder(provider, workspace)
    evaluator = Evaluator(_StubRunner())
    execution_loop = ExecutionLoop(conn, builder, evaluator, staging_base_dir=tmp_path / "mf_staging")
    orchestrator = Orchestrator(conn, provider, execution_loop)
    return conn, workspace, orchestrator


def _create_modern_candidate(tmp_path):
    """Candidate moderna e completa (MF-FIX-004/005): passa da tutte le
    garanzie di promuovibilità, resta approvabile normalmente."""
    conn, workspace, orchestrator = _build_foundry(tmp_path)
    goal_id = orchestrator.submit_goal("crea la capability")
    goal_run = orchestrator.run_goal(goal_id)
    outcome = goal_run.task_outcomes[0]
    candidate = models.get_candidate(conn, outcome.candidate_id)
    return conn, workspace, goal_id, candidate


def _make_bare_goal_and_task(conn):
    goal_id = models.create_goal(conn, "goal legacy pre-MF-FIX-004")
    task_id = models.create_task(conn, goal_id, order_index=0, description="task legacy", assigned_to="builder")
    return goal_id, task_id


def _create_legacy_candidate(
    conn,
    *,
    staging_root: str | None = None,
    target_snapshot_hash: str | None = None,
    manifest_json: str | None = None,
) -> int:
    """Crea una candidate esattamente come l'avrebbe creata il sistema
    pre-MF-FIX-004: nessuno staging, nessun hash del target, nessun
    manifest — il caso reale già presente nel DB di produzione (candidate
    #1, goal 3, `pending_review`, campi staging NULL)."""
    goal_id, task_id = _make_bare_goal_and_task(conn)
    candidate_id = models.create_candidate(
        conn,
        goal_id,
        task_id,
        summary="candidate legacy senza staging",
        provider_name="legacy-provider",
        is_simulated=True,
        staging_root=staging_root,
        target_snapshot_hash=target_snapshot_hash,
        manifest_json=manifest_json,
    )
    return goal_id, candidate_id


_VALID_STAGING_MANIFEST = {"capability.py": {"hash": "deadbeef", "size": 42}}
_VALID_DIFF_MANIFEST = {
    "created": ["capability.py"],
    "modified": [],
    "deleted": [],
    "final_hashes": {"capability.py": "deadbeef"},
    "final_sizes": {"capability.py": 42},
}


def _full_manifest_json(*, target_root: str | None = "some/target") -> str:
    payload = {"target_root": target_root, "staging_manifest": _VALID_STAGING_MANIFEST, "files": _VALID_DIFF_MANIFEST}
    return json.dumps(payload)


# --- 1-4. ognuna delle garanzie mancanti, singolarmente, blocca l'approvazione ----


def test_legacy_candidate_with_null_staging_root_is_not_promotable(tmp_path):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(
        conn,
        staging_root=None,
        target_snapshot_hash="abc123",
        manifest_json=_full_manifest_json(),
    )

    with pytest.raises(LegacyCandidateNotPromotableError, match="staging_root mancante"):
        gate.approve_candidate(conn, candidate_id, backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "pending_review"


def test_legacy_candidate_with_null_target_snapshot_hash_is_not_promotable(tmp_path):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(
        conn,
        staging_root=str(tmp_path / "some_staging"),
        target_snapshot_hash=None,
        manifest_json=_full_manifest_json(),
    )

    with pytest.raises(LegacyCandidateNotPromotableError, match="target_snapshot_hash mancante"):
        gate.approve_candidate(conn, candidate_id, backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "pending_review"


def test_legacy_candidate_with_missing_target_root_is_not_promotable(tmp_path):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(
        conn,
        staging_root=str(tmp_path / "some_staging"),
        target_snapshot_hash="abc123",
        manifest_json=_full_manifest_json(target_root=None),
    )

    with pytest.raises(LegacyCandidateNotPromotableError, match="target_root non registrato"):
        gate.approve_candidate(conn, candidate_id, backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "pending_review"


@pytest.mark.parametrize(
    "manifest_json",
    [
        json.dumps({"target_root": "some/target", "files": _VALID_DIFF_MANIFEST}),  # staging_manifest assente
        json.dumps(
            {"target_root": "some/target", "staging_manifest": {}, "files": _VALID_DIFF_MANIFEST}
        ),  # staging_manifest vuoto
        None,  # manifest interamente assente
    ],
)
def test_legacy_candidate_with_missing_or_invalid_staging_manifest_is_not_promotable(tmp_path, manifest_json):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(
        conn,
        staging_root=str(tmp_path / "some_staging"),
        target_snapshot_hash="abc123",
        manifest_json=manifest_json,
    )

    with pytest.raises(LegacyCandidateNotPromotableError, match="staging_manifest"):
        gate.approve_candidate(conn, candidate_id, backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "pending_review"


@pytest.mark.parametrize(
    "manifest_json",
    [
        json.dumps(
            {"target_root": "some/target", "staging_manifest": _VALID_STAGING_MANIFEST}
        ),  # "files" assente del tutto
        json.dumps(
            {"target_root": "some/target", "staging_manifest": _VALID_STAGING_MANIFEST, "files": {"created": []}}
        ),  # "files" incompleto (mancano modified/deleted/final_hashes/final_sizes)
        json.dumps(
            {"target_root": "some/target", "staging_manifest": _VALID_STAGING_MANIFEST, "files": "non-un-dizionario"}
        ),  # "files" non valido come tipo
    ],
)
def test_legacy_candidate_with_missing_or_invalid_diff_manifest_is_not_promotable(tmp_path, manifest_json):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(
        conn,
        staging_root=str(tmp_path / "some_staging"),
        target_snapshot_hash="abc123",
        manifest_json=manifest_json,
    )

    with pytest.raises(LegacyCandidateNotPromotableError, match="diff manifest"):
        gate.approve_candidate(conn, candidate_id, backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "pending_review"


# --- 5-7. effetti collaterali del blocco: target invariato, nessuna decisione, audit ----


def test_legacy_candidate_block_leaves_target_untouched(tmp_path):
    target_root = tmp_path / "real_target"
    target_root.mkdir()
    (target_root / "existing_file.py").write_text("# file preesistente nel target\n", encoding="utf-8")
    before = (target_root / "existing_file.py").read_text(encoding="utf-8")

    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(
        conn,
        staging_root=None,
        target_snapshot_hash=None,
        manifest_json=_full_manifest_json(target_root=str(target_root)),
    )

    with pytest.raises(LegacyCandidateNotPromotableError):
        gate.approve_candidate(conn, candidate_id, backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    after = (target_root / "existing_file.py").read_text(encoding="utf-8")
    assert after == before
    assert sorted(p.name for p in target_root.iterdir()) == ["existing_file.py"]
    # Nessun backup inutile creato per una candidate bloccata prima ancora
    # di sapere se il target esiste o cosa contiene.
    assert not (tmp_path / BACKUP_DIR_NAME).exists()


def test_legacy_candidate_block_creates_no_approve_decision(tmp_path):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(conn)

    with pytest.raises(LegacyCandidateNotPromotableError):
        gate.approve_candidate(conn, candidate_id, backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    decisions = conn.execute(
        "SELECT * FROM decisions WHERE candidate_id = ? AND decision_type = 'approve'", (candidate_id,)
    ).fetchall()
    assert decisions == []
    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "pending_review"


def test_legacy_candidate_block_is_audited(tmp_path):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(conn)

    with pytest.raises(LegacyCandidateNotPromotableError):
        gate.approve_candidate(conn, candidate_id, backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    rows = list_audit_log(conn, limit=1000)
    matching = [r for r in rows if r["entity_id"] == candidate_id and r["action"] == "CANDIDATE_LEGACY_NOT_PROMOTABLE"]
    assert len(matching) == 1
    payload = json.loads(matching[0]["payload_json"])
    assert "reasons" in payload
    assert len(payload["reasons"]) >= 1


# --- 8-9. rifiuto manuale resta possibile e definitivo -----------------------------


def test_legacy_candidate_is_rejectable(tmp_path):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(conn)

    gate.reject_candidate(conn, candidate_id, rationale="candidate legacy senza garanzie: rifiutata manualmente")

    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "rejected"
    goal = models.get_goal(conn, goal_id)
    assert goal["status"] == "blocked"

    decisions = conn.execute(
        "SELECT * FROM decisions WHERE candidate_id = ? AND decision_type = 'reject'", (candidate_id,)
    ).fetchall()
    assert len(decisions) == 1


def test_rejected_legacy_candidate_can_never_be_approved(tmp_path):
    from mercury_foundry.approval.gate import InvalidCandidateStateError

    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(conn)

    gate.reject_candidate(conn, candidate_id)

    with pytest.raises(InvalidCandidateStateError):
        gate.approve_candidate(conn, candidate_id, backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "rejected"


def test_rejecting_legacy_candidate_does_not_attempt_nonexistent_staging_cleanup(tmp_path):
    """Il reject non deve mai provare a ripulire uno staging che non è mai
    esistito (nessuna eccezione, nessuna scrittura sul filesystem)."""
    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(conn, staging_root=None)

    # Non deve sollevare alcuna eccezione, anche se staging_root è NULL.
    gate.reject_candidate(conn, candidate_id)

    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "rejected"


# --- 10. una candidate moderna e completa resta approvabile normalmente ------------


def test_modern_complete_candidate_is_still_approvable(tmp_path):
    conn, workspace, goal_id, candidate = _create_modern_candidate(tmp_path)

    gate.approve_candidate(conn, candidate["id"], backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    candidate_after = models.get_candidate(conn, candidate["id"])
    assert candidate_after["status"] == "approved"
    decisions = conn.execute(
        "SELECT * FROM decisions WHERE candidate_id = ? AND decision_type = 'approve'", (candidate["id"],)
    ).fetchall()
    assert len(decisions) == 1


# --- 11. l'intera suite gira in regressione (verificato separatamente con   -------
#          `pytest` sull'intero progetto; qui solo un canary locale) --------------


def test_legacy_candidate_error_message_names_every_missing_guarantee(tmp_path):
    conn = db.connect(tmp_path / "mercury_foundry.db")
    goal_id, candidate_id = _create_legacy_candidate(conn)  # tutto NULL/assente

    with pytest.raises(LegacyCandidateNotPromotableError) as exc_info:
        gate.approve_candidate(conn, candidate_id, backup_base_dir=tmp_path / BACKUP_DIR_NAME)

    message = str(exc_info.value)
    for expected_fragment in ("staging_root", "target_snapshot_hash", "target_root", "staging_manifest"):
        assert expected_fragment in message

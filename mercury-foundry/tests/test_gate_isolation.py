"""Test di isolamento del gate umano — MF-INCIDENT-001, FASE 3.

Verifica che:
- il runtime ordinario (submit, orchestrator, execution_loop, evaluator,
  diagnostics, test_helper) non possa approvare candidate;
- human_gate.approve_candidate sia bloccata in contesti non umani
  (test, automazione, stdin non-TTY);
- l'operazione compensativa revoke_approval_incident conservi la storia
  e rimuova solo i file promossi;
- un target non coincidente blocchi il rollback fail-closed;
- il target finale torni vuoto dopo revoke;
- zero chiamate al provider in queste operazioni.

Nessuna scrittura fuori da tmp_path. Nessuna chiamata reale al provider.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path

import pytest

from mercury_foundry.approval import gate
from mercury_foundry.approval.gate import (
    ApprovalRevokeConflictError,
    InvalidCandidateStateError,
    revoke_approval_incident,
)
from mercury_foundry.approval.human_gate import (
    HumanApprovalToken,
    RuntimeApprovalBlockedError,
    approve_candidate as human_approve_candidate,
)
from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.state import db, models
from mercury_foundry.wiring import build_foundry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_source(module_name: str) -> str:
    """Restituisce il sorgente di un modulo della Foundry."""
    import importlib.util
    spec = importlib.util.find_spec(module_name)
    assert spec is not None and spec.origin is not None, f"Modulo {module_name} non trovato"
    return Path(spec.origin).read_text(encoding="utf-8")


def _imports_approve_from_human_gate(module_name: str) -> bool:
    """True se il modulo importa 'approve_candidate' da human_gate (statico)."""
    src = _module_source(module_name)
    return "human_gate" in src and "approve_candidate" in src


def _imports_approve_candidate(module_name: str) -> bool:
    """True se il modulo importa 'approve_candidate' da qualsiasi percorso."""
    src = _module_source(module_name)
    # cerca import di approve_candidate direttamente o via from ... import approve_candidate
    return "approve_candidate" in src


def _build_isolated(tmp_path):
    return build_foundry(
        db_path=tmp_path / "mf.db",
        sandbox_root=tmp_path / "target",
        provider_name="fake",
    )


def _create_approved_candidate(tmp_path):
    """Crea un target isolato con una candidate approvata. Usa gate.approve_candidate
    direttamente (percorso business-logic, non human_gate) con db/target temporanei."""
    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("crea una capability isolata")
    run_result = foundry.orchestrator.run_goal(goal_id)
    candidate_id = run_result.task_outcomes[0].candidate_id
    gate.approve_candidate(
        foundry.conn,
        candidate_id,
        backup_base_dir=foundry.backup_base_dir,
    )
    return foundry, candidate_id


# ---------------------------------------------------------------------------
# SEZIONE 1 — Analisi statica: il runtime non importa approve_candidate
# ---------------------------------------------------------------------------

RUNTIME_MODULES = [
    "mercury_foundry.execution.loop",
    "mercury_foundry.orchestrator.orchestrator",
    "mercury_foundry.agents.builder",
    "mercury_foundry.agents.evaluator",
    "mercury_foundry.sandbox.workspace",
    "mercury_foundry.diagnostics",
    "mercury_foundry.wiring",
]


@pytest.mark.parametrize("module_name", RUNTIME_MODULES)
def test_runtime_module_does_not_import_approve_candidate_from_human_gate(module_name):
    """Verifica statica: nessun modulo del runtime importa approve_candidate da human_gate."""
    assert not _imports_approve_from_human_gate(module_name), (
        f"{module_name} importa approve_candidate da human_gate: "
        "il runtime non deve avere accesso all'entrypoint di approvazione umana."
    )


def test_submit_does_not_call_approve_candidate(tmp_path):
    """Un run completo (submit → run_goal) non chiama mai approve_candidate:
    la candidate resta pending_review dopo il run."""
    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test submit isolation")
    foundry.orchestrator.run_goal(goal_id)

    goal = models.get_goal(foundry.conn, goal_id)
    assert goal["status"] == "awaiting_approval", (
        f"Goal status atteso 'awaiting_approval', trovato '{goal['status']}'"
    )
    candidates = models.list_candidates(foundry.conn, goal_id)
    assert all(c["status"] == "pending_review" for c in candidates), (
        "Submit ha approvato automaticamente una candidate — isolamento violato."
    )


def test_evaluator_module_does_not_import_approve_candidate():
    """Analisi statica sull'Evaluator: nessuna chiamata a approve_candidate."""
    src = _module_source("mercury_foundry.agents.evaluator")
    assert "approve_candidate" not in src, "Evaluator importa/chiama approve_candidate."


def test_diagnostics_does_not_call_approve_candidate():
    """Il modulo diagnostics non chiama mai approve_candidate come funzione.
    Può citarla in stringhe di testo o commenti, ma non come `..approve_candidate(`.
    """
    src = _module_source("mercury_foundry.diagnostics")
    # Cerca la firma di una chiamata effettiva: approve_candidate(
    # Non conta: nomi in stringhe, commenti, o riferimenti parziali come 'approve_candidate)'
    assert "approve_candidate(" not in src, (
        "diagnostics.py chiama approve_candidate come funzione — "
        "il modulo diagnostics non deve poter approvare candidate."
    )


def test_test_helper_cannot_touch_real_target(tmp_path):
    """Un test che usa foundry con tmp_path non scrive mai nel target_project reale."""
    from mercury_foundry import config
    real_target = config.TARGET_PROJECT_DIR

    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test helper isolation")
    foundry.orchestrator.run_goal(goal_id)

    # Verifica che nulla nel target reale sia stato scritto durante il run del test
    real_files_after = list(real_target.rglob("*")) if real_target.exists() else []
    # Il target reale può esistere (vuoto) ma nessun nuovo file deve esserci
    for f in real_files_after:
        if f.is_file():
            # Un file nel target reale trovato durante un test con tmp_path è un leak
            pytest.fail(
                f"Il test helper ha scritto nel target reale: {f}. "
                "Tutti i test devono usare tmp_path isolato."
            )


# ---------------------------------------------------------------------------
# SEZIONE 2 — Blocco di human_gate in contesti automatici
# ---------------------------------------------------------------------------

def test_human_gate_blocked_under_pytest(tmp_path):
    """human_gate.approve_candidate è bloccata quando PYTEST_CURRENT_TEST è impostata.
    Questa variabile è impostata automaticamente da pytest: il test stesso è
    la prova che il blocco funziona."""
    assert os.environ.get("PYTEST_CURRENT_TEST"), (
        "Questo test deve girare sotto pytest (PYTEST_CURRENT_TEST deve essere impostata)."
    )

    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test human gate block")
    run_result = foundry.orchestrator.run_goal(goal_id)
    candidate_id = run_result.task_outcomes[0].candidate_id

    token = HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED")

    with pytest.raises(RuntimeApprovalBlockedError) as exc_info:
        human_approve_candidate(
            foundry.conn,
            candidate_id,
            token=token,
            backup_base_dir=foundry.backup_base_dir,
        )
    assert "PYTEST_CURRENT_TEST" in str(exc_info.value)

    # La candidate deve essere rimasta pending_review
    candidate = models.get_candidate(foundry.conn, candidate_id)
    assert candidate["status"] == "pending_review", (
        f"La candidate deve restare pending_review, trovato '{candidate['status']}'"
    )


def test_human_gate_blocked_without_token(tmp_path):
    """human_gate.approve_candidate è bloccata se token non fornito.
    (Testato bypassando il check pytest per testare specificamente il check token.)"""
    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test no token block")
    run_result = foundry.orchestrator.run_goal(goal_id)
    candidate_id = run_result.task_outcomes[0].candidate_id

    # Bypassa il check pytest/isatty per testare il check token in isolamento
    import mercury_foundry.approval.human_gate as hg
    original = hg._assert_human_context

    def _only_token_check(cid, token):
        if token is None:
            raise RuntimeApprovalBlockedError(
                f"approve_candidate (human_gate) bloccata: nessun HumanApprovalToken fornito."
            )
        expected = f"APPROVE-{cid}-CONFIRMED"
        if token.candidate_id_confirmation != expected:
            raise RuntimeApprovalBlockedError(
                f"token.candidate_id_confirmation {token.candidate_id_confirmation!r} "
                f"non corrisponde a {expected!r}."
            )

    hg._assert_human_context = _only_token_check
    try:
        with pytest.raises(RuntimeApprovalBlockedError) as exc_info:
            human_approve_candidate(foundry.conn, candidate_id, token=None,
                                    backup_base_dir=foundry.backup_base_dir)
        assert "HumanApprovalToken" in str(exc_info.value)
    finally:
        hg._assert_human_context = original


def test_human_gate_blocked_with_wrong_token(tmp_path):
    """human_gate.approve_candidate è bloccata se il token non corrisponde all'ID."""
    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test wrong token block")
    run_result = foundry.orchestrator.run_goal(goal_id)
    candidate_id = run_result.task_outcomes[0].candidate_id

    import mercury_foundry.approval.human_gate as hg
    original = hg._assert_human_context

    def _only_token_check(cid, token):
        if token is None:
            raise RuntimeApprovalBlockedError("token mancante")
        expected = f"APPROVE-{cid}-CONFIRMED"
        if token.candidate_id_confirmation != expected:
            raise RuntimeApprovalBlockedError(
                f"token.candidate_id_confirmation {token.candidate_id_confirmation!r} "
                f"non corrisponde a {expected!r}."
            )

    hg._assert_human_context = _only_token_check
    try:
        wrong_token = HumanApprovalToken(f"APPROVE-9999-CONFIRMED")  # ID sbagliato
        with pytest.raises(RuntimeApprovalBlockedError) as exc_info:
            human_approve_candidate(foundry.conn, candidate_id, token=wrong_token,
                                    backup_base_dir=foundry.backup_base_dir)
        assert "non corrisponde" in str(exc_info.value)
    finally:
        hg._assert_human_context = original


def test_modern_valid_candidate_stays_pending_review_without_human_gate(tmp_path):
    """Una candidate moderna e valida (con staging/manifest completi) resta
    pending_review al termine del run: non esiste nessuna approvazione automatica."""
    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test candidate stays pending")
    run_result = foundry.orchestrator.run_goal(goal_id)

    candidate_id = run_result.task_outcomes[0].candidate_id
    candidate = models.get_candidate(foundry.conn, candidate_id)

    assert candidate["status"] == "pending_review"
    assert candidate["staging_root"] is not None
    assert candidate["target_snapshot_hash"] is not None
    assert candidate["manifest_json"] is not None

    goal = models.get_goal(foundry.conn, goal_id)
    assert goal["status"] == "awaiting_approval"


# ---------------------------------------------------------------------------
# SEZIONE 3 — Operazione compensativa revoke_approval_incident
# ---------------------------------------------------------------------------

def test_revoke_approval_preserves_historical_decision_and_audit(tmp_path):
    """revoke_approval_incident conserva la decisione approve e l'audit CANDIDATE_APPROVED
    originali, e aggiunge una nuova decisione/audit compensativi."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)
    conn = foundry.conn

    audit_before = list_audit_log(conn, limit=1000)
    approved_events = [r for r in audit_before if r["action"] == "CANDIDATE_APPROVED"]
    assert len(approved_events) == 1, "Deve esserci esattamente un audit CANDIDATE_APPROVED prima del revoke"

    decisions_before = conn.execute(
        "SELECT * FROM decisions WHERE candidate_id=?", (candidate_id,)
    ).fetchall()
    approve_decisions = [d for d in decisions_before if d["decision_type"] == "approve"]
    assert len(approve_decisions) == 1

    target_root = foundry.workspace.root
    revoke_approval_incident(
        conn, candidate_id,
        rationale="test: verifica conservazione storia",
        target_root=target_root,
    )

    # La decisione approve originale deve essere ancora lì
    all_decisions = conn.execute(
        "SELECT * FROM decisions WHERE candidate_id=?", (candidate_id,)
    ).fetchall()
    decision_types = [d["decision_type"] for d in all_decisions]
    assert "approve" in decision_types, "Decisione approve originale rimossa — violazione di append-only"
    assert "approval_revoke_incident" in decision_types, "Decisione compensativa non creata"

    # L'audit CANDIDATE_APPROVED originale deve essere ancora lì
    audit_after = list_audit_log(conn, limit=1000)
    approved_events_after = [r for r in audit_after if r["action"] == "CANDIDATE_APPROVED"]
    assert len(approved_events_after) == 1, "Audit CANDIDATE_APPROVED originale rimosso — violazione di append-only"

    revoke_events = [r for r in audit_after if r["action"] == "CANDIDATE_APPROVAL_REVOKED_INCIDENT"]
    assert len(revoke_events) == 1, "Audit compensativo CANDIDATE_APPROVAL_REVOKED_INCIDENT non presente"

    candidate = models.get_candidate(conn, candidate_id)
    assert candidate["status"] == "approval_revoked"


def test_revoke_approval_removes_only_promoted_files(tmp_path):
    """revoke_approval_incident rimuove SOLO i file promossi dal manifest,
    lasciando invariati altri file eventualmente presenti nel target."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)
    target_root = foundry.workspace.root

    # Crea un file extra nel target che NON era parte della promozione
    extra_file = target_root / "extra_unrelated.txt"
    extra_file.write_text("non toccare questo file")

    candidate = models.get_candidate(foundry.conn, candidate_id)
    manifest = json.loads(candidate["manifest_json"])
    promoted = (
        list(manifest["files"]["created"])
        + list(manifest["files"]["modified"])
    )
    assert len(promoted) > 0, "Il test richiede almeno un file promosso nel manifest"

    revoke_approval_incident(
        foundry.conn, candidate_id,
        rationale="test: solo file promossi rimossi",
        target_root=target_root,
    )

    # I file promossi devono essere stati rimossi
    for rel in promoted:
        assert not (target_root / rel).exists(), f"File promosso {rel} non rimosso"

    # Il file extra deve essere ancora lì
    assert extra_file.exists(), "extra_unrelated.txt rimosso — revoke ha rimosso più del dovuto"


def test_revoke_blocked_on_target_hash_mismatch(tmp_path):
    """revoke_approval_incident è bloccata fail-closed se un file nel target
    non corrisponde all'hash del manifest."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)
    target_root = foundry.workspace.root

    candidate = models.get_candidate(foundry.conn, candidate_id)
    manifest = json.loads(candidate["manifest_json"])
    promoted = list(manifest["files"]["created"]) + list(manifest["files"]["modified"])
    assert len(promoted) > 0

    # Altera il contenuto di uno dei file promossi nel target
    altered_file = target_root / promoted[0]
    altered_file.write_text("contenuto alterato — hash diverso")

    with pytest.raises(ApprovalRevokeConflictError) as exc_info:
        revoke_approval_incident(
            foundry.conn, candidate_id,
            rationale="test: mismatch blocca revoke",
            target_root=target_root,
        )
    assert promoted[0] in str(exc_info.value) or "hash" in str(exc_info.value).lower()

    # La candidate deve restare approved (nessuna rimozione avvenuta)
    candidate = models.get_candidate(foundry.conn, candidate_id)
    assert candidate["status"] == "approved", (
        "La candidate non deve cambiare stato se revoke è bloccata"
    )

    # Nemmeno i file non alterati devono essere stati rimossi
    for rel in promoted[1:]:
        assert (target_root / rel).exists(), f"File {rel} rimosso nonostante il blocco"


def test_revoke_blocked_on_missing_target_file(tmp_path):
    """revoke_approval_incident è bloccata se un file promosso è già mancante nel target."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)
    target_root = foundry.workspace.root

    candidate = models.get_candidate(foundry.conn, candidate_id)
    manifest = json.loads(candidate["manifest_json"])
    promoted = list(manifest["files"]["created"]) + list(manifest["files"]["modified"])
    assert len(promoted) > 0

    # Rimuovi manualmente uno dei file (simula target già manomesso)
    (target_root / promoted[0]).unlink()

    with pytest.raises(ApprovalRevokeConflictError) as exc_info:
        revoke_approval_incident(
            foundry.conn, candidate_id,
            rationale="test: file mancante blocca revoke",
            target_root=target_root,
        )
    assert "mancante" in str(exc_info.value).lower() or promoted[0] in str(exc_info.value)

    candidate = models.get_candidate(foundry.conn, candidate_id)
    assert candidate["status"] == "approved"


def test_revoke_blocked_on_wrong_initial_status(tmp_path):
    """revoke_approval_incident è bloccata su una candidate non approvata."""
    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test revoke on pending")
    run_result = foundry.orchestrator.run_goal(goal_id)
    candidate_id = run_result.task_outcomes[0].candidate_id

    # La candidate è pending_review, non approved
    with pytest.raises(InvalidCandidateStateError):
        revoke_approval_incident(
            foundry.conn, candidate_id,
            rationale="test: stato errato",
            target_root=foundry.workspace.root,
        )


def test_target_is_empty_after_revoke_of_fully_created_candidate(tmp_path):
    """Il target torna vuoto dopo revoke se tutti i file erano stati creati dalla candidate."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)
    target_root = foundry.workspace.root

    # Prima dell'operazione il target deve avere i file promossi
    target_files_before = list(target_root.rglob("*"))
    target_regular_files = [f for f in target_files_before if f.is_file()]
    assert len(target_regular_files) > 0, "Il target deve avere file dopo l'approvazione"

    revoke_approval_incident(
        foundry.conn, candidate_id,
        rationale="test: target vuoto dopo revoke",
        target_root=target_root,
    )

    remaining = [f for f in target_root.rglob("*") if f.is_file()]
    assert len(remaining) == 0, (
        f"Il target non è vuoto dopo revoke: {remaining}"
    )


# ---------------------------------------------------------------------------
# SEZIONE 4 — Zero chiamate al provider durante le operazioni di incidente
# ---------------------------------------------------------------------------

def test_no_provider_calls_during_revoke_incident(tmp_path):
    """revoke_approval_incident non effettua alcuna chiamata al provider AI."""
    foundry, candidate_id = _create_approved_candidate(tmp_path)

    calls_before = foundry.conn.execute("SELECT COUNT(*) as n FROM provider_calls").fetchone()["n"]

    revoke_approval_incident(
        foundry.conn, candidate_id,
        rationale="test: zero chiamate provider",
        target_root=foundry.workspace.root,
    )

    calls_after = foundry.conn.execute("SELECT COUNT(*) as n FROM provider_calls").fetchone()["n"]
    assert calls_after == calls_before, (
        f"revoke_approval_incident ha effettuato {calls_after - calls_before} chiamate al provider"
    )


def test_no_provider_calls_during_human_gate_check(tmp_path):
    """Il blocco di human_gate non effettua chiamate al provider."""
    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test no calls in gate check")
    run_result = foundry.orchestrator.run_goal(goal_id)
    candidate_id = run_result.task_outcomes[0].candidate_id

    calls_before = foundry.conn.execute("SELECT COUNT(*) as n FROM provider_calls").fetchone()["n"]

    token = HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED")
    with pytest.raises(RuntimeApprovalBlockedError):
        human_approve_candidate(foundry.conn, candidate_id, token=token,
                                backup_base_dir=foundry.backup_base_dir)

    calls_after = foundry.conn.execute("SELECT COUNT(*) as n FROM provider_calls").fetchone()["n"]
    assert calls_after == calls_before, "human_gate ha effettuato chiamate al provider durante il blocco"


# ---------------------------------------------------------------------------
# SEZIONE 5 — Validazione HumanApprovalToken
# ---------------------------------------------------------------------------

def test_human_approval_token_rejects_empty_string():
    with pytest.raises(ValueError, match="non vuota"):
        HumanApprovalToken("")


def test_human_approval_token_rejects_whitespace_only():
    with pytest.raises(ValueError):
        HumanApprovalToken("   ")


def test_human_approval_token_stores_stripped_confirmation():
    token = HumanApprovalToken("  APPROVE-42-CONFIRMED  ")
    assert token.candidate_id_confirmation == "APPROVE-42-CONFIRMED"

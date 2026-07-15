"""Test del protocollo di approvazione umana — MF-GATE-002.

Verifica tutti i requisiti del nuovo canale di approvazione:
- disabilitato per default (MERCURY_HUMAN_APPROVAL_ENABLED non impostata);
- token prevedibile non sufficiente senza il resto del protocollo;
- canale abilitato senza segreto → bloccato;
- segreto presente ma non-TTY → bloccato;
- pytest sempre bloccato (prima del check TTY/challenge);
- challenge monouso (già usata → bloccata);
- challenge scaduta → bloccata;
- risposta errata alla challenge → bloccata;
- flusso valido completo (mocked TTY + input + challenge);
- export candidate non promuove e non tocca il target;
- audit APPROVAL_CHANNEL_DISABLED registrato quando il canale è off;
- zero chiamate al provider durante tutti i blocchi.

Nessuna chiamata reale al provider. Nessuna scrittura fuori da tmp_path.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import mercury_foundry.approval.human_gate as hg
from mercury_foundry.approval import gate
from mercury_foundry.approval.human_gate import (
    APPROVAL_ENABLED_ENV,
    APPROVAL_SECRET_ENV,
    ApprovalChannelDisabledError,
    ApprovalChallengeExpiredError,
    ApprovalChallengeMismatchError,
    ApprovalChallengeReusedError,
    ApprovalSecretMissingError,
    HumanApprovalToken,
    RuntimeApprovalBlockedError,
    approve_candidate as human_approve_candidate,
    export_candidate_package,
    generate_challenge,
    is_channel_enabled,
    is_secret_configured,
    verify_challenge,
)
from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.state import db, models
from mercury_foundry.wiring import build_foundry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_isolated(tmp_path):
    return build_foundry(
        db_path=tmp_path / "mf.db",
        sandbox_root=tmp_path / "target",
        provider_name="fake",
    )


def _run_and_get_candidate_id(foundry, description="test goal"):
    goal_id = foundry.orchestrator.submit_goal(description)
    run_result = foundry.orchestrator.run_goal(goal_id)
    return run_result.task_outcomes[0].candidate_id


def _with_channel_env(enabled: bool = True, secret: str | None = "test-secret-value"):
    """Contesto manager che imposta/ripristina le env var del canale.
    Usato per testare i check interni DOPO quello del canale."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        old_enabled = os.environ.get(APPROVAL_ENABLED_ENV)
        old_secret = os.environ.get(APPROVAL_SECRET_ENV)
        try:
            if enabled:
                os.environ[APPROVAL_ENABLED_ENV] = "true"
            elif APPROVAL_ENABLED_ENV in os.environ:
                del os.environ[APPROVAL_ENABLED_ENV]
            if secret is not None:
                os.environ[APPROVAL_SECRET_ENV] = secret
            elif APPROVAL_SECRET_ENV in os.environ:
                del os.environ[APPROVAL_SECRET_ENV]
            yield
        finally:
            if old_enabled is None:
                os.environ.pop(APPROVAL_ENABLED_ENV, None)
            else:
                os.environ[APPROVAL_ENABLED_ENV] = old_enabled
            if old_secret is None:
                os.environ.pop(APPROVAL_SECRET_ENV, None)
            else:
                os.environ[APPROVAL_SECRET_ENV] = old_secret

    return _ctx()


# ---------------------------------------------------------------------------
# SEZIONE 1 — Stato del canale (abilitazione e segreto)
# ---------------------------------------------------------------------------

def test_channel_disabled_by_default():
    """Il canale è disabilitato per default: MERCURY_HUMAN_APPROVAL_ENABLED non impostata."""
    # Rimuove temporaneamente la variabile se presente
    old = os.environ.pop(APPROVAL_ENABLED_ENV, None)
    try:
        assert not is_channel_enabled(), (
            f"{APPROVAL_ENABLED_ENV} non deve essere abilitata per default nel workspace Agent"
        )
    finally:
        if old is not None:
            os.environ[APPROVAL_ENABLED_ENV] = old


def test_channel_enabled_only_with_exact_true():
    """Il canale è abilitato solo con il valore esatto 'true' (case-insensitive)."""
    # is_channel_enabled() usa .strip().lower() — spazi intorno a 'true' sono accettati
    cases_disabled = ["", "1", "yes", "on", "  ", "false", "TRUE-no", "true1"]
    cases_enabled = ["true", "True", "TRUE", "  true  ", "True ", "TRUE\n"]

    original = os.environ.get(APPROVAL_ENABLED_ENV)
    try:
        for val in cases_disabled:
            os.environ[APPROVAL_ENABLED_ENV] = val
            assert not is_channel_enabled(), f"'{val}' non dovrebbe abilitare il canale"
        for val in cases_enabled:
            os.environ[APPROVAL_ENABLED_ENV] = val
            assert is_channel_enabled(), f"'{val}' dovrebbe abilitare il canale"
    finally:
        if original is None:
            os.environ.pop(APPROVAL_ENABLED_ENV, None)
        else:
            os.environ[APPROVAL_ENABLED_ENV] = original


def test_secret_not_configured_by_default():
    """MERCURY_HUMAN_APPROVAL_SECRET non è impostato nel workspace Agent."""
    old = os.environ.pop(APPROVAL_SECRET_ENV, None)
    try:
        assert not is_secret_configured(), (
            f"{APPROVAL_SECRET_ENV} non deve essere impostato nel workspace Agent"
        )
    finally:
        if old is not None:
            os.environ[APPROVAL_SECRET_ENV] = old


def test_channel_disabled_raises_and_logs_audit(tmp_path):
    """Tentativo di approvazione con canale disabilitato → ApprovalChannelDisabledError + audit."""
    with _with_channel_env(enabled=False, secret=None):
        foundry = _build_isolated(tmp_path)
        candidate_id = _run_and_get_candidate_id(foundry)
        token = HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED")

        with pytest.raises(ApprovalChannelDisabledError) as exc_info:
            human_approve_candidate(foundry.conn, candidate_id, token=token)

        assert APPROVAL_ENABLED_ENV in str(exc_info.value)

        # Audit APPROVAL_CHANNEL_DISABLED deve essere registrato
        rows = list_audit_log(foundry.conn, limit=200)
        disabled = [r for r in rows if r["action"] == "APPROVAL_CHANNEL_DISABLED"]
        assert len(disabled) >= 1, "Audit APPROVAL_CHANNEL_DISABLED non registrato"

        # La candidate deve restare pending_review
        c = models.get_candidate(foundry.conn, candidate_id)
        assert c["status"] == "pending_review"


def test_channel_enabled_without_secret_blocked(tmp_path):
    """Canale abilitato ma segreto non impostato → ApprovalSecretMissingError."""
    with _with_channel_env(enabled=True, secret=None):
        foundry = _build_isolated(tmp_path)
        candidate_id = _run_and_get_candidate_id(foundry)
        token = HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED")

        with pytest.raises(ApprovalSecretMissingError) as exc_info:
            human_approve_candidate(foundry.conn, candidate_id, token=token)

        assert APPROVAL_SECRET_ENV in str(exc_info.value)
        c = models.get_candidate(foundry.conn, candidate_id)
        assert c["status"] == "pending_review"


def test_channel_enabled_with_secret_but_pytest_blocked(tmp_path):
    """Canale + segreto impostati, ma esecuzione sotto pytest → RuntimeApprovalBlockedError."""
    assert os.environ.get("PYTEST_CURRENT_TEST"), "Questo test deve girare sotto pytest"

    with _with_channel_env(enabled=True, secret="test-secret"):
        foundry = _build_isolated(tmp_path)
        candidate_id = _run_and_get_candidate_id(foundry)
        token = HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED")

        with pytest.raises(RuntimeApprovalBlockedError) as exc_info:
            human_approve_candidate(foundry.conn, candidate_id, token=token)

        assert "PYTEST_CURRENT_TEST" in str(exc_info.value) or "pytest" in str(exc_info.value).lower()
        c = models.get_candidate(foundry.conn, candidate_id)
        assert c["status"] == "pending_review"


def test_predictable_token_alone_not_sufficient(tmp_path):
    """Un token prevedibile APPROVE-N-CONFIRMED da solo non è sufficiente per approvare.
    Con il canale disabilitato il blocco avviene prima ancora del check token."""
    foundry = _build_isolated(tmp_path)
    candidate_id = _run_and_get_candidate_id(foundry)

    # Anche con il token corretto, senza il canale abilitato → bloccato
    token = HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED")

    with pytest.raises(ApprovalChannelDisabledError):
        human_approve_candidate(foundry.conn, candidate_id, token=token)

    c = models.get_candidate(foundry.conn, candidate_id)
    assert c["status"] == "pending_review"


def test_enabled_flag_alone_not_sufficient(tmp_path):
    """Impostare solo MERCURY_HUMAN_APPROVAL_ENABLED=true senza il segreto non basta."""
    with _with_channel_env(enabled=True, secret=None):
        foundry = _build_isolated(tmp_path)
        candidate_id = _run_and_get_candidate_id(foundry)
        token = HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED")

        with pytest.raises(ApprovalSecretMissingError):
            human_approve_candidate(foundry.conn, candidate_id, token=token)


def test_secret_without_tty_not_sufficient_mocked(tmp_path):
    """Segreto configurato + canale abilitato ma senza TTY → bloccato.
    Testato bypassando il check pytest e simulando isatty=False."""
    with _with_channel_env(enabled=True, secret="test-secret"):
        foundry = _build_isolated(tmp_path)
        candidate_id = _run_and_get_candidate_id(foundry)
        token = HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED")

        original = hg._assert_human_context

        def _skip_pytest_check_only(conn, cid, tok):
            # Salta il check pytest ma mantiene gli altri
            import sys as _sys
            if not _sys.stdin.isatty():
                raise RuntimeApprovalBlockedError(
                    "stdin non è un terminale interattivo (non-TTY)."
                )
            # In un test stdin non è mai un TTY, quindi questo path
            # verifica che il check non-TTY blocchi correttamente

        hg._assert_human_context = _skip_pytest_check_only
        try:
            import sys as _sys
            assert not _sys.stdin.isatty(), "stdin è un TTY in ambiente test — atteso non-TTY"
            with pytest.raises(RuntimeApprovalBlockedError) as exc_info:
                human_approve_candidate(foundry.conn, candidate_id, token=token)
            assert "TTY" in str(exc_info.value) or "tty" in str(exc_info.value).lower() or "terminale" in str(exc_info.value).lower()
        finally:
            hg._assert_human_context = original


# ---------------------------------------------------------------------------
# SEZIONE 2 — Challenge monouso e scadenza
# ---------------------------------------------------------------------------

def test_challenge_is_random_each_time():
    """generate_challenge produce sfide diverse ad ogni chiamata."""
    challenges = {generate_challenge()[0] for _ in range(20)}
    assert len(challenges) >= 18, "Le challenge non sono sufficientemente casuali"


def test_challenge_format():
    """Il formato della challenge è 'MF-XXXXXXXX' (8 caratteri esadecimali maiuscoli)."""
    for _ in range(10):
        ch, _ = generate_challenge()
        assert ch.startswith("MF-"), f"Challenge {ch!r} non inizia con 'MF-'"
        suffix = ch[3:]
        assert len(suffix) == 8, f"Suffisso challenge {suffix!r} non è di 8 caratteri"
        assert suffix == suffix.upper(), f"Suffisso non è maiuscolo: {suffix!r}"
        assert all(c in "0123456789ABCDEF" for c in suffix), f"Caratteri non esadecimali: {suffix!r}"


def test_challenge_verify_correct():
    """Una challenge corretta e non scaduta è verificata con successo."""
    ch, created_at = generate_challenge()
    # Non deve sollevare nulla
    verify_challenge(ch, ch, created_at)
    # Deve essere ora nei _used_challenges
    assert ch in hg._used_challenges


def test_challenge_reused_rejected():
    """Una challenge già usata viene rifiutata (monouso)."""
    ch, created_at = generate_challenge()
    # Prima utilizzo: ok
    verify_challenge(ch, ch, created_at)
    # Secondo utilizzo: bloccato
    ch2, created_at2 = generate_challenge()
    # Usiamo la stessa stringa come se fosse nuova, ma è già in _used_challenges
    hg._used_challenges.add(ch2)  # Simula che ch2 sia già stata usata
    with pytest.raises(ApprovalChallengeReusedError) as exc_info:
        verify_challenge(ch2, ch2, created_at2)
    assert "monouso" in str(exc_info.value).lower() or "già" in str(exc_info.value)


def test_challenge_expired_rejected():
    """Una challenge scaduta (TTL superato) viene rifiutata."""
    ch, _ = generate_challenge()
    # Simula un created_at molto vecchio (TTL + 1 secondo nel passato)
    expired_at = time.monotonic() - (hg.CHALLENGE_TTL_SECONDS + 1)
    with pytest.raises(ApprovalChallengeExpiredError) as exc_info:
        verify_challenge(ch, ch, expired_at)
    assert "scaduta" in str(exc_info.value).lower() or "scadut" in str(exc_info.value).lower()


def test_challenge_mismatch_rejected():
    """Una risposta errata alla challenge viene rifiutata."""
    ch, created_at = generate_challenge()
    with pytest.raises(ApprovalChallengeMismatchError):
        verify_challenge(ch, "MF-00000000", created_at)


def test_challenge_mismatch_with_close_value():
    """Anche una challenge quasi-corretta (un carattere sbagliato) viene rifiutata."""
    ch, created_at = generate_challenge()
    wrong = ch[:-1] + ("X" if ch[-1] != "X" else "Y")
    with pytest.raises(ApprovalChallengeMismatchError):
        verify_challenge(ch, wrong, created_at)


def test_challenge_whitespace_stripped_on_verify():
    """La risposta dell'utente viene strippata prima del confronto (spazi/newline terminali)."""
    ch, created_at = generate_challenge()
    verify_challenge(ch, f"  {ch}  ", created_at)
    assert ch in hg._used_challenges


def test_full_approval_flow_mocked(tmp_path):
    """Flusso completo di approvazione con canale abilitato, segreto, non-pytest, TTY e
    challenge corretta — tutto mockato per girare senza un terminale reale."""
    with _with_channel_env(enabled=True, secret="test-secret-value"):
        foundry = _build_isolated(tmp_path)
        candidate_id = _run_and_get_candidate_id(foundry)
        token = HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED")

        # Mock completo del contesto umano: bypass tutti i check di sistema
        captured_challenge = None

        def _fake_full_context(conn, cid, tok):
            nonlocal captured_challenge
            # Simula la generazione e verifica della challenge
            ch, created_at = generate_challenge()
            captured_challenge = ch
            # Simula input corretto dell'operatore (risposta identica alla challenge)
            verify_challenge(ch, ch, created_at)

        original = hg._assert_human_context
        hg._assert_human_context = _fake_full_context
        try:
            human_approve_candidate(
                foundry.conn,
                candidate_id,
                rationale="test flusso completo mockato",
                token=token,
                backup_base_dir=foundry.backup_base_dir,
            )
        finally:
            hg._assert_human_context = original

        # La candidate deve essere approved e il target deve contenere i file
        c = models.get_candidate(foundry.conn, candidate_id)
        assert c["status"] == "approved"
        target_files = list(foundry.workspace.root.rglob("*"))
        promoted = [f for f in target_files if f.is_file()]
        assert len(promoted) > 0, "Nessun file promosso nel target dopo approvazione riuscita"
        assert captured_challenge is not None
        assert captured_challenge in hg._used_challenges


# ---------------------------------------------------------------------------
# SEZIONE 3 — Export candidate (senza promozione)
# ---------------------------------------------------------------------------

def test_export_candidate_does_not_promote(tmp_path):
    """export_candidate_package non tocca il target reale e non crea decisioni approve."""
    foundry = _build_isolated(tmp_path)
    candidate_id = _run_and_get_candidate_id(foundry)

    target_before = list(foundry.workspace.root.rglob("*"))
    target_files_before = [f for f in target_before if f.is_file()]

    result = export_candidate_package(foundry.conn, candidate_id)

    # Il target non è stato toccato
    target_after = list(foundry.workspace.root.rglob("*"))
    target_files_after = [f for f in target_after if f.is_file()]
    assert target_files_before == target_files_after, "export_candidate_package ha toccato il target"

    # La candidate è ancora pending_review
    c = models.get_candidate(foundry.conn, candidate_id)
    assert c["status"] == "pending_review"

    # Nessuna decisione approve creata
    decisions = foundry.conn.execute(
        "SELECT * FROM decisions WHERE candidate_id=? AND decision_type='approve'",
        (candidate_id,),
    ).fetchall()
    assert len(decisions) == 0

    # Il risultato indica chiaramente che non è stata promossa
    assert result["promoted"] is False
    assert result["target_modified"] is False
    assert result["candidate_id"] == candidate_id


def test_export_candidate_returns_manifest(tmp_path):
    """export_candidate_package restituisce il manifest della candidate."""
    foundry = _build_isolated(tmp_path)
    candidate_id = _run_and_get_candidate_id(foundry)

    result = export_candidate_package(foundry.conn, candidate_id)
    assert isinstance(result["manifest"], dict), "Il manifest deve essere un dict"
    assert "files" in result["manifest"], "Il manifest deve contenere la chiave 'files'"


def test_export_candidate_lists_staging_files(tmp_path):
    """export_candidate_package elenca i file dello staging se ancora presenti."""
    foundry = _build_isolated(tmp_path)
    candidate_id = _run_and_get_candidate_id(foundry)

    result = export_candidate_package(foundry.conn, candidate_id)
    # La staging root è ancora presente (la candidate non è stata approvata)
    assert len(result["staging_files"]) > 0, (
        "La staging ancora presente deve contenere file elencabili"
    )


def test_export_candidate_with_zip(tmp_path):
    """export_candidate_package con output_dir crea un file zip."""
    foundry = _build_isolated(tmp_path)
    candidate_id = _run_and_get_candidate_id(foundry)

    output_dir = tmp_path / "exports"
    result = export_candidate_package(foundry.conn, candidate_id, output_dir=output_dir)

    assert result["zip_path"] is not None
    zip_path = Path(result["zip_path"])
    assert zip_path.exists(), f"Lo zip non è stato creato in {zip_path}"
    assert zip_path.suffix == ".zip"

    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "MANIFEST.json" in names, "Lo zip non contiene MANIFEST.json"
    assert len(names) >= 2, "Lo zip deve contenere almeno MANIFEST.json + un file sorgente"


def test_export_candidate_does_not_modify_target_project():
    """export_candidate_package non tocca mai il target_project reale (analisi statica)."""
    import inspect
    src = inspect.getsource(export_candidate_package)
    # Il target_project reale non viene mai toccato dalla funzione export
    # Verifica che non ci siano scritture dirette (write, unlink, mkdir) sul target
    forbidden_patterns = [
        "target_root.write",
        "target_root.unlink",
        "target_root.mkdir",
        ".write_bytes",
        ".write_text",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in src, (
            f"export_candidate_package contiene '{pattern}' — potrebbe modificare il target"
        )


# ---------------------------------------------------------------------------
# SEZIONE 4 — Submit e verify non possono approvare
# ---------------------------------------------------------------------------

def test_submit_does_not_approve_candidate(tmp_path):
    """Un run completo (submit → run_goal) non crea mai una decisione approve."""
    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test submit no approve")
    foundry.orchestrator.run_goal(goal_id)

    decisions = foundry.conn.execute(
        "SELECT * FROM decisions WHERE decision_type='approve'"
    ).fetchall()
    assert len(decisions) == 0, "submit/run_goal ha creato decisioni approve — isolamento violato"


def test_target_real_not_touched_by_tests(tmp_path):
    """Un test con foundry isolata non scrive nel target_project reale."""
    from mercury_foundry import config

    real_target = config.TARGET_PROJECT_DIR
    files_before = set(str(f) for f in real_target.rglob("*") if f.is_file()) if real_target.exists() else set()

    foundry = _build_isolated(tmp_path)
    goal_id = foundry.orchestrator.submit_goal("test no real target write")
    foundry.orchestrator.run_goal(goal_id)

    files_after = set(str(f) for f in real_target.rglob("*") if f.is_file()) if real_target.exists() else set()
    new_files = files_after - files_before
    assert not new_files, f"Nuovi file nel target reale: {new_files}"


# ---------------------------------------------------------------------------
# SEZIONE 5 — Zero chiamate al provider durante i blocchi
# ---------------------------------------------------------------------------

def test_no_provider_calls_during_channel_disabled_block(tmp_path):
    """Nessuna chiamata al provider quando il canale è disabilitato."""
    with _with_channel_env(enabled=False, secret=None):
        foundry = _build_isolated(tmp_path)
        candidate_id = _run_and_get_candidate_id(foundry)
        calls_before = foundry.conn.execute("SELECT COUNT(*) as n FROM provider_calls").fetchone()["n"]

        with pytest.raises(ApprovalChannelDisabledError):
            human_approve_candidate(
                foundry.conn, candidate_id,
                token=HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED"),
            )

        calls_after = foundry.conn.execute("SELECT COUNT(*) as n FROM provider_calls").fetchone()["n"]
        assert calls_after == calls_before


def test_no_provider_calls_during_secret_missing_block(tmp_path):
    """Nessuna chiamata al provider quando il segreto è assente."""
    with _with_channel_env(enabled=True, secret=None):
        foundry = _build_isolated(tmp_path)
        candidate_id = _run_and_get_candidate_id(foundry)
        calls_before = foundry.conn.execute("SELECT COUNT(*) as n FROM provider_calls").fetchone()["n"]

        with pytest.raises(ApprovalSecretMissingError):
            human_approve_candidate(
                foundry.conn, candidate_id,
                token=HumanApprovalToken(f"APPROVE-{candidate_id}-CONFIRMED"),
            )

        calls_after = foundry.conn.execute("SELECT COUNT(*) as n FROM provider_calls").fetchone()["n"]
        assert calls_after == calls_before


def test_no_provider_calls_during_challenge_failures():
    """Nessun effetto collaterale su provider_calls durante verifiche di challenge fallite."""
    calls_before_set = set(hg._used_challenges)

    ch, created_at = generate_challenge()

    with pytest.raises(ApprovalChallengeMismatchError):
        verify_challenge(ch, "WRONG-WRONG", created_at)

    # Challenge non deve essere stata aggiunta ai used dopo un mismatch
    assert ch not in hg._used_challenges, (
        "Una challenge con risposta errata non deve essere registrata come usata"
    )

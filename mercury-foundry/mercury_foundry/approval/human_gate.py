"""Entrypoint di approvazione SEPARATO dal runtime ordinario della Foundry.

MF-GATE-002: il canale di approvazione è DISABILITATO per default.

Per abilitarlo in futuro sono richiesti CONTEMPORANEAMENTE:
  1. MERCURY_HUMAN_APPROVAL_ENABLED=true (Replit Secret)
  2. MERCURY_HUMAN_APPROVAL_SECRET impostato (Replit Secret, mai stampato/loggato)
  3. Terminale interattivo reale (sys.stdin.isatty())
  4. Esecuzione NON sotto pytest (PYTEST_CURRENT_TEST non impostata)
  5. HumanApprovalToken con candidate_id_confirmation == 'APPROVE-{id}-CONFIRMED'
  6. Challenge casuale monouso con scadenza di 60 secondi digitata dall'operatore

Il normale workspace Agent mantiene l'approvazione disabilitata:
MERCURY_HUMAN_APPROVAL_ENABLED non è impostata e MERCURY_HUMAN_APPROVAL_SECRET
non è configurato — qualunque tentativo di approvazione registra un audit
APPROVAL_CHANNEL_DISABLED e si ferma fail-closed.

Il segreto non viene mai stampato, registrato nel DB o salvato in log.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable

from mercury_foundry.approval import gate
from mercury_foundry.audit.logger import log_action

# Nomi delle variabili d'ambiente controllate (lette, mai scritte da questo modulo)
APPROVAL_ENABLED_ENV = "MERCURY_HUMAN_APPROVAL_ENABLED"
APPROVAL_SECRET_ENV = "MERCURY_HUMAN_APPROVAL_SECRET"

# Scadenza challenge in secondi
CHALLENGE_TTL_SECONDS = 60

# Registro monouso delle challenge usate (lifetime del processo)
# Struttura: set di stringhe challenge già consumate.
_used_challenges: set[str] = set()

# Funzione di input intercettabile nei test (mai da codice di produzione esterno)
# — unico punto di iniezione, documentato esplicitamente per trasparenza.
_input_fn: Callable[[str], str] = input


# ---------------------------------------------------------------------------
# Eccezioni
# ---------------------------------------------------------------------------

class ApprovalChannelDisabledError(RuntimeError):
    """Il canale di approvazione umana è disabilitato (MERCURY_HUMAN_APPROVAL_ENABLED != true).

    Qualunque tentativo di approvazione è bloccato fail-closed e registrato
    con audit APPROVAL_CHANNEL_DISABLED. Nessun file viene promosso e nessuna
    decisione approve viene creata.
    """


class ApprovalSecretMissingError(RuntimeError):
    """MERCURY_HUMAN_APPROVAL_ENABLED=true ma MERCURY_HUMAN_APPROVAL_SECRET non impostato.

    Avere solo il flag abilitante senza il segreto non è sufficiente.
    """


class ApprovalChallengeExpiredError(RuntimeError):
    """La challenge è scaduta (TTL di {CHALLENGE_TTL_SECONDS}s superato).

    Richiedere una nuova sessione di approvazione.
    """


class ApprovalChallengeReusedError(RuntimeError):
    """La challenge è già stata usata in questa sessione del processo.

    Ogni challenge è monouso. Richiedere una nuova sessione.
    """


class ApprovalChallengeMismatchError(RuntimeError):
    """La risposta alla challenge non corrisponde. Nessuna approvazione effettuata."""


class RuntimeApprovalBlockedError(RuntimeError):
    """Tentativo di approvazione bloccato: contesto non umano rilevato.

    Può essere sollevata per:
    - esecuzione sotto pytest (PYTEST_CURRENT_TEST impostata);
    - stdin non interattivo (non-TTY);
    - token di approvazione assente;
    - token.candidate_id_confirmation != 'APPROVE-{id}-CONFIRMED'.
    """


# ---------------------------------------------------------------------------
# HumanApprovalToken
# ---------------------------------------------------------------------------

class HumanApprovalToken:
    """Prova di intento umano. candidate_id_confirmation deve essere
    esattamente ``APPROVE-{candidate_id}-CONFIRMED``.

    Il token è necessario ma NON sufficiente: anche canale abilitato,
    segreto configurato, TTY, non-pytest e challenge corretta sono richiesti.
    """

    def __init__(self, candidate_id_confirmation: str) -> None:
        if not isinstance(candidate_id_confirmation, str) or not candidate_id_confirmation.strip():
            raise ValueError(
                "HumanApprovalToken richiede una stringa non vuota. "
                "Formato atteso: 'APPROVE-{candidate_id}-CONFIRMED'."
            )
        self.candidate_id_confirmation = candidate_id_confirmation.strip()

    def __repr__(self) -> str:
        return f"HumanApprovalToken({self.candidate_id_confirmation!r})"


# ---------------------------------------------------------------------------
# Helpers di stato del canale
# ---------------------------------------------------------------------------

def is_channel_enabled() -> bool:
    """True solo se MERCURY_HUMAN_APPROVAL_ENABLED è esattamente 'true' (case-insensitive)."""
    return os.environ.get(APPROVAL_ENABLED_ENV, "").strip().lower() == "true"


def is_secret_configured() -> bool:
    """True se MERCURY_HUMAN_APPROVAL_SECRET è impostato e non vuoto.

    Non restituisce il valore del segreto: controlla solo la presenza.
    """
    return bool(os.environ.get(APPROVAL_SECRET_ENV, "").strip())


# ---------------------------------------------------------------------------
# Challenge monouso con scadenza
# ---------------------------------------------------------------------------

def generate_challenge() -> tuple[str, float]:
    """Genera una challenge casuale monouso e restituisce (challenge, created_at_monotonic).

    La challenge è una stringa di 8 caratteri esadecimali maiuscoli preceduti
    da 'MF-': es. 'MF-A3F7B2C1'. Viene generata con `secrets.token_hex` (CSPRNG).
    Il chiamante è responsabile di presentarla all'operatore e poi validarla
    con `verify_challenge`.
    """
    token = secrets.token_hex(4).upper()
    challenge = f"MF-{token}"
    return challenge, time.monotonic()


def verify_challenge(challenge: str, user_input: str, created_at: float) -> None:
    """Verifica la risposta dell'operatore alla challenge.

    Controlla in ordine:
    1. Scadenza (elapsed > CHALLENGE_TTL_SECONDS) → ApprovalChallengeExpiredError
    2. Monouso (già in _used_challenges) → ApprovalChallengeReusedError
    3. Corrispondenza (secrets.compare_digest) → ApprovalChallengeMismatchError

    Se tutte le verifiche passano, registra la challenge come usata.
    Il segreto di approvazione NON viene verificato qui: la sua presenza
    è già stata controllata a monte da `_assert_human_context`.
    """
    elapsed = time.monotonic() - created_at
    if elapsed > CHALLENGE_TTL_SECONDS:
        raise ApprovalChallengeExpiredError(
            f"Challenge scaduta dopo {elapsed:.1f}s (limite: {CHALLENGE_TTL_SECONDS}s). "
            "Avviare una nuova sessione di approvazione."
        )
    if challenge in _used_challenges:
        raise ApprovalChallengeReusedError(
            f"Challenge {challenge!r} già usata in questa sessione del processo. "
            "Ogni challenge è monouso — avviare una nuova sessione."
        )
    # Confronto timing-safe per prevenire timing attacks
    if not secrets.compare_digest(user_input.strip(), challenge):
        raise ApprovalChallengeMismatchError(
            "La risposta alla challenge non corrisponde. Nessuna approvazione effettuata."
        )
    _used_challenges.add(challenge)


# ---------------------------------------------------------------------------
# Verifica del contesto completo
# ---------------------------------------------------------------------------

def _assert_human_context(
    conn: sqlite3.Connection,
    candidate_id: int,
    token: HumanApprovalToken | None,
) -> None:
    """Verifica TUTTI i prerequisiti del canale di approvazione umana.

    Ordine di verifica (fail-closed, ogni errore ferma la catena):
    1. Canale abilitato (MERCURY_HUMAN_APPROVAL_ENABLED=true)
    2. Segreto configurato (MERCURY_HUMAN_APPROVAL_SECRET impostato)
    3. Non sotto pytest (PYTEST_CURRENT_TEST non impostata)
    4. Stdin interattivo (sys.stdin.isatty())
    5. Token presente e candidate_id_confirmation corrispondente
    6. Challenge interattiva casuale e monouso (digitata dall'operatore)
    """
    # --- Check 1: Canale abilitato ---
    if not is_channel_enabled():
        log_action(
            conn,
            entity_type="candidate",
            entity_id=candidate_id,
            action="APPROVAL_CHANNEL_DISABLED",
            actor="system",
            payload={
                "reason": f"{APPROVAL_ENABLED_ENV} non impostata o != 'true'",
                "candidate_id": candidate_id,
            },
        )
        raise ApprovalChannelDisabledError(
            f"Canale di approvazione disabilitato: {APPROVAL_ENABLED_ENV} non è 'true'. "
            "Nessun file promosso, nessuna decisione creata. "
            "Audit APPROVAL_CHANNEL_DISABLED registrato."
        )

    # --- Check 2: Segreto configurato ---
    if not is_secret_configured():
        raise ApprovalSecretMissingError(
            f"{APPROVAL_ENABLED_ENV}=true ma {APPROVAL_SECRET_ENV} non è impostato. "
            "Entrambi sono richiesti per abilitare il canale di approvazione."
        )

    # --- Check 3: Non sotto pytest ---
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeApprovalBlockedError(
            "approve_candidate bloccata: esecuzione sotto pytest "
            f"(PYTEST_CURRENT_TEST={os.environ['PYTEST_CURRENT_TEST']!r}). "
            "I test non possono approvare candidate tramite human_gate."
        )

    # --- Check 4: Stdin interattivo ---
    if not sys.stdin.isatty():
        raise RuntimeApprovalBlockedError(
            "approve_candidate bloccata: stdin non è un terminale interattivo (non-TTY). "
            "L'approvazione richiede una sessione TTY reale."
        )

    # --- Check 5: Token presente e corrispondente ---
    if token is None:
        raise RuntimeApprovalBlockedError(
            f"Nessun HumanApprovalToken fornito. "
            f"Fornire token=HumanApprovalToken('APPROVE-{candidate_id}-CONFIRMED')."
        )
    expected = f"APPROVE-{candidate_id}-CONFIRMED"
    if token.candidate_id_confirmation != expected:
        raise RuntimeApprovalBlockedError(
            f"token.candidate_id_confirmation {token.candidate_id_confirmation!r} "
            f"non corrisponde a {expected!r}."
        )

    # --- Check 6: Challenge interattiva ---
    challenge, created_at = generate_challenge()
    print(f"\n[MF-GATE] Approvazione candidate {candidate_id} richiesta.")
    print(f"[MF-GATE] Challenge monouso (valida {CHALLENGE_TTL_SECONDS}s): {challenge}")
    print("[MF-GATE] Digita la challenge esatta e premi Invio:")
    user_input = _input_fn("")
    verify_challenge(challenge, user_input, created_at)


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------

def approve_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    rationale: str | None = None,
    *,
    token: HumanApprovalToken,
    target_root: Path | None = None,
    backup_base_dir: Path | None = None,
) -> None:
    """Approva una candidate tramite il canale di approvazione umana completo.

    Richiede simultaneamente: canale abilitato, segreto configurato, TTY,
    non-pytest, token corrispondente, challenge monouso digitata dall'operatore.

    Se qualsiasi prerequisito manca → fail-closed, nessuna promozione.
    In caso di APPROVAL_CHANNEL_DISABLED → audit registrato nel DB.
    """
    _assert_human_context(conn, candidate_id, token)
    gate.approve_candidate(
        conn,
        candidate_id,
        rationale=rationale,
        target_root=target_root,
        backup_base_dir=backup_base_dir,
    )


# ---------------------------------------------------------------------------
# Export candidate (senza promozione)
# ---------------------------------------------------------------------------

def export_candidate_package(
    conn: sqlite3.Connection,
    candidate_id: int,
    output_dir: Path | None = None,
) -> dict:
    """Esporta il pacchetto di una candidate per revisione umana esterna.

    NON promuove nulla nel target reale. NON crea decisioni approve.
    Restituisce un dict con il manifest e i percorsi dei file nello staging
    (se ancora presenti). Se output_dir è fornito, crea uno zip dell'area
    di staging.

    Usare questo comando per inviare una candidate a un revisore umano
    esterno senza modificare target_project.
    """
    import json
    import zipfile
    from datetime import datetime, timezone
    from mercury_foundry.state import models

    candidate = models.get_candidate(conn, candidate_id)
    if candidate is None:
        raise ValueError(f"Candidate {candidate_id} non trovata.")

    manifest = json.loads(candidate["manifest_json"]) if candidate["manifest_json"] else {}
    staging_root = Path(candidate["staging_root"]) if candidate["staging_root"] else None
    staging_files: dict[str, dict] = {}
    zip_path: str | None = None

    if staging_root and staging_root.exists():
        for f in sorted(staging_root.rglob("*")):
            if f.is_file() and not any(
                p in {".mf_test_home", ".mf_test_tmp", "__pycache__", ".pytest_cache"}
                for p in f.parts
            ):
                rel = f.relative_to(staging_root).as_posix()
                staging_files[rel] = {
                    "path": str(f),
                    "size": f.stat().st_size,
                }

        if output_dir is not None:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            zip_name = f"candidate_{candidate_id}_{ts}.zip"
            zip_path = str(output_dir / zip_name)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False))
                for rel, info in staging_files.items():
                    zf.write(info["path"], arcname=rel)

    return {
        "candidate_id": candidate_id,
        "status": candidate["status"],
        "provider_name": candidate["provider_name"],
        "is_simulated": bool(candidate["is_simulated"]),
        "manifest": manifest,
        "staging_root": str(staging_root) if staging_root else None,
        "staging_files": staging_files,
        "zip_path": zip_path,
        "promoted": False,
        "target_modified": False,
    }

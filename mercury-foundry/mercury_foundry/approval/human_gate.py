"""Entrypoint di approvazione SEPARATO dal runtime ordinario della Foundry.

Questo modulo è l'UNICO percorso pubblico attraverso cui `approve_candidate`
può essere chiamata da un operatore umano. Il runtime ordinario (Orchestrator,
ExecutionLoop, Builder, Evaluator, wiring, diagnostics) NON importa da questo
modulo e non ha quindi alcuna capacità di approvare candidate.

Separazione garantita dai blocchi seguenti (MF-INCIDENT-001, FASE 3):

1. Blocco test/automazione: viene rifiutata qualunque chiamata effettuata
   mentre è attiva la variabile d'ambiente `PYTEST_CURRENT_TEST` (impostata
   automaticamente da pytest per ogni test in esecuzione).

2. Blocco non-interattivo: viene rifiutata qualunque chiamata proveniente da
   un processo il cui stdin non è un terminale interattivo (pipe, subprocess,
   reindirizzamento, CI senza TTY, script automatici).

3. Blocco token assente o non corrispondente: il chiamante deve fornire un
   `HumanApprovalToken` con un `candidate_id_confirmation` che corrisponda
   ESATTAMENTE alla stringa ``APPROVE-{candidate_id}-CONFIRMED``. Non è
   sufficiente passare un flag `--confirm` generico che un agente automatico
   possa aggiungere autonomamente.

Non considerare questo entrypoint come un sostituto di un canale di approvazione
esterno (es. integrazione CI/CD con step umano). In un ambiente completamente
headless, anche un operatore umano sarebbe bloccato dal check `isatty`. La
sicurezza è prioritaria rispetto alla comodità (spec MF-INCIDENT-001, req 121).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from mercury_foundry.approval import gate


class RuntimeApprovalBlockedError(RuntimeError):
    """Tentativo di approvazione bloccato: contesto non umano rilevato.

    Può essere sollevata per:
    - esecuzione sotto pytest (PYTEST_CURRENT_TEST impostata);
    - stdin non interattivo (non-TTY: pipe, subprocess, automazione);
    - token di approvazione assente;
    - token.candidate_id_confirmation non corrispondente a APPROVE-{id}-CONFIRMED.
    """


class HumanApprovalToken:
    """Prova di intento umano necessaria per chiamare `approve_candidate`.

    La stringa `candidate_id_confirmation` deve essere esattamente
    ``APPROVE-{candidate_id}-CONFIRMED`` (es. ``APPROVE-7-CONFIRMED``).
    Non basta un flag `--confirm` generico: il chiamante deve conoscere
    l'ID specifico della candidate che intende approvare e fornirlo
    esplicitamente come parte della stringa di conferma.

    Il token NON esegue alcun check di contesto al momento della creazione:
    i check (test, isatty, corrispondenza ID) vengono eseguiti da
    `approve_candidate` al momento della chiamata.
    """

    def __init__(self, candidate_id_confirmation: str) -> None:
        if not isinstance(candidate_id_confirmation, str) or not candidate_id_confirmation.strip():
            raise ValueError(
                "HumanApprovalToken richiede una stringa non vuota per candidate_id_confirmation. "
                "Formato atteso: 'APPROVE-{candidate_id}-CONFIRMED'."
            )
        self.candidate_id_confirmation = candidate_id_confirmation.strip()

    def __repr__(self) -> str:
        return f"HumanApprovalToken(candidate_id_confirmation={self.candidate_id_confirmation!r})"


def _assert_human_context(candidate_id: int, token: HumanApprovalToken | None) -> None:
    """Verifica che il contesto di chiamata sia genuinamente umano.

    Solleva `RuntimeApprovalBlockedError` se uno dei seguenti è vero:
    - PYTEST_CURRENT_TEST è impostata (esecuzione sotto pytest);
    - sys.stdin non è un terminale interattivo (non-TTY);
    - token non fornito;
    - token.candidate_id_confirmation != 'APPROVE-{candidate_id}-CONFIRMED'.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeApprovalBlockedError(
            "approve_candidate (human_gate) bloccata: rilevata esecuzione sotto pytest "
            f"(PYTEST_CURRENT_TEST={os.environ['PYTEST_CURRENT_TEST']!r}). "
            "I test del gate devono usare gate.approve_candidate con db/target temporanei isolati."
        )

    if not sys.stdin.isatty():
        raise RuntimeApprovalBlockedError(
            "approve_candidate (human_gate) bloccata: stdin non è un terminale interattivo. "
            "L'approvazione umana richiede una sessione TTY — nessuna approvazione da pipe, "
            "subprocess, automazione, CI senza TTY o script agent."
        )

    if token is None:
        raise RuntimeApprovalBlockedError(
            f"approve_candidate (human_gate) bloccata: nessun HumanApprovalToken fornito. "
            f"Fornire token=HumanApprovalToken('APPROVE-{candidate_id}-CONFIRMED')."
        )

    expected = f"APPROVE-{candidate_id}-CONFIRMED"
    if token.candidate_id_confirmation != expected:
        raise RuntimeApprovalBlockedError(
            f"approve_candidate (human_gate) bloccata: token.candidate_id_confirmation "
            f"{token.candidate_id_confirmation!r} non corrisponde a {expected!r}. "
            "Fornire il token con la stringa esatta corrispondente all'ID della candidate."
        )


def approve_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    rationale: str | None = None,
    *,
    token: HumanApprovalToken,
    target_root: Path | None = None,
    backup_base_dir: Path | None = None,
) -> None:
    """Approva una candidate tramite conferma umana esplicita.

    Blocca fail-closed se il contesto non è genuinamente umano (vedi
    `_assert_human_context`). Dopo la verifica del contesto, delega la
    logica di promozione a `gate.approve_candidate`, che riverifica
    staging/target e promuove atomicamente.

    Args:
        conn: connessione al DB di stato.
        candidate_id: ID della candidate da approvare.
        rationale: motivazione opzionale dell'approvazione.
        token: HumanApprovalToken con candidate_id_confirmation ==
               'APPROVE-{candidate_id}-CONFIRMED'. Obbligatorio.
        target_root: root del target (override, usato solo nei test).
        backup_base_dir: root dei backup (override).
    """
    _assert_human_context(candidate_id, token)
    gate.approve_candidate(
        conn,
        candidate_id,
        rationale=rationale,
        target_root=target_root,
        backup_base_dir=backup_base_dir,
    )

"""CLI per l'Outreach Agent — MF-QB-OUTREACH-001.

Utilizzo:
    python -m mercury_foundry.outreach --prepare-latest
    python -m mercury_foundry.outreach --send-latest
    python -m mercury_foundry.outreach --prepare-latest --output output/outreach/prepared_latest.json
    python -m mercury_foundry.outreach --send-latest --prepared-file output/outreach/prepared_latest.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mercury_foundry.outreach.agent import OutreachAgent
from mercury_foundry.outreach.models import (
    DeliveryStatus,
    OutreachResultStatus,
)

# ── Percorsi default ─────────────────────────────────────────────────────────

_VERIFIED_PATH   = Path("output/leads/contact_verified_latest.json")
_ENRICHED_PATH   = Path("output/leads/enriched_latest.json")
_OPPORTUNITY_PATH = Path("output/opportunity/latest.json")
_PREPARED_PATH   = Path("output/outreach/prepared_latest.json")
_SENT_PATH       = Path("output/outreach/sent_latest.json")


# ── Helper di caricamento ────────────────────────────────────────────────────

def _load_json(path: Path, label: str) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERRORE: impossibile leggere '{path}' ({label}): {exc}", file=sys.stderr)
        return None


def _load_verified_result() -> tuple[dict | None, Path]:
    """Carica contact_verified_latest.json, con fallback a enriched_latest.json."""
    if _VERIFIED_PATH.exists():
        data = _load_json(_VERIFIED_PATH, "contact_verified_result")
        if data is not None:
            return data, _VERIFIED_PATH
    if _ENRICHED_PATH.exists():
        data = _load_json(_ENRICHED_PATH, "enriched_result")
        if data is not None:
            return data, _ENRICHED_PATH
    return None, _VERIFIED_PATH


def _has_real_provider() -> bool:
    try:
        from mercury_foundry.ai.provider_config import load_real_provider_config
        load_real_provider_config()
        return True
    except Exception:
        return False


# ── Stampa risultati ─────────────────────────────────────────────────────────

def _print_prepared(result_dict: dict) -> None:
    print()
    print(f"STATO:   {result_dict.get('status', 'N/D')}")
    print(f"PRONTI:  {result_dict.get('prepared_count', 0)}")

    if result_dict.get("block_reason"):
        print()
        print(f"BLOCCO:\n  {result_dict['block_reason']}")
        print(f"\nPROSSIMA AZIONE:\n  {result_dict.get('next_action', '')}")
        return

    messages = result_dict.get("messages", [])
    for i, msg in enumerate(messages, 1):
        if msg.get("delivery_status") != "PREPARED":
            continue
        print()
        print(f"  ── Messaggio {i}: {msg.get('lead_id', '')} ──────────────────────")
        print(f"  A:       {msg.get('recipient', '')}")
        print(f"  Canale:  {msg.get('channel', '')}")
        print(f"  Oggetto: {msg.get('subject', '')}")
        print()
        for line in msg.get("message", "").splitlines():
            print(f"  {line}")
        print()
        print(f"  [Follow-up]: {msg.get('follow_up_message', '')[:120]}...")
        print(f"  [Azione]:    {msg.get('next_action', '')}")

    print()
    print(f"PROSSIMA AZIONE:\n  {result_dict.get('next_action', '')}")


def _print_sent(result_dict: dict) -> None:
    print()
    print(f"STATO:    {result_dict.get('status', 'N/D')}")
    print(f"INVIATI:  {result_dict.get('sent_count', 0)}")
    print(f"FALLITI:  {result_dict.get('failed_count', 0)}")

    if result_dict.get("block_reason"):
        print()
        print(f"BLOCCO:\n  {result_dict['block_reason']}")
        if result_dict.get("missing_env_vars"):
            print()
            print("Variabili d'ambiente mancanti:")
            for v in result_dict["missing_env_vars"]:
                print(f"  - {v}")
        if result_dict.get("expected_provider"):
            print()
            print(f"Provider atteso: {result_dict['expected_provider']}")
        print(f"\nPROSSIMA AZIONE:\n  {result_dict.get('next_action', '')}")
        return

    messages = result_dict.get("messages", [])
    sent    = [m for m in messages if m.get("delivery_status") == "SENT"]
    failed  = [m for m in messages if m.get("delivery_status") == "FAILED"]

    if sent:
        print()
        print("INVIATI:")
        for m in sent:
            print(f"  ✓ {m.get('lead_id','')} → {m.get('recipient','')} | "
                  f"id={m.get('provider_message_id','')[:40]} | "
                  f"follow-up: {m.get('follow_up_due','')[:10]}")

    if failed:
        print()
        print("FALLITI:")
        for m in failed:
            print(f"  ✗ {m.get('lead_id','')} → {m.get('recipient','')} | "
                  f"errore: {m.get('error','')[:80]}")

    print()
    print(f"PROSSIMA AZIONE:\n  {result_dict.get('next_action', '')}")


# ── Comandi ──────────────────────────────────────────────────────────────────

def _cmd_prepare(args: argparse.Namespace) -> int:
    verified, source_path = _load_verified_result()
    if verified is None:
        print(
            "ERRORE: nessun risultato lead verificato trovato.\n"
            f"  Cercato in: {_VERIFIED_PATH}, {_ENRICHED_PATH}\n"
            "Eseguire prima:\n"
            "  python -m mercury_foundry.lead_enrichment --verify-contacts-latest",
            file=sys.stderr,
        )
        return 1

    opportunity = _load_json(_OPPORTUNITY_PATH, "opportunity")
    if opportunity is None:
        print(
            f"ERRORE: nessun OpportunityResult trovato in '{_OPPORTUNITY_PATH}'.\n"
            "Eseguire prima:\n"
            "  python -m mercury_foundry.opportunity --run",
            file=sys.stderr,
        )
        return 1

    leads_count = len(verified.get("enriched_leads", []))
    print("Mercury Outreach Agent — preparazione messaggi...")
    print(f"Sorgente lead: {source_path}")
    print(f"Lead disponibili: {leads_count}")
    print(f"Opportunità: {opportunity.get('problem', 'N/A')[:80]}")
    print(f"Provider AI: {'reale' if _has_real_provider() else 'non configurato'}")

    if _has_real_provider():
        try:
            agent = OutreachAgent.with_real_provider()
        except RuntimeError as exc:
            print(f"\nERRORE provider AI: {exc}", file=sys.stderr)
            return 1
    else:
        print(
            "\nERRORE: provider AI non configurato.\n"
            "Imposta MERCURY_AI_PROVIDER=openai_compatible e le variabili associate.",
            file=sys.stderr,
        )
        return 1

    result = agent.prepare(verified, opportunity)
    result_dict = result.to_dict()
    _print_prepared(result_dict)

    output_path = Path(args.output) if args.output else _PREPARED_PATH
    result.save(output_path)
    print(f"\nRisultato salvato in: {output_path}")

    return 0 if result.status in (
        OutreachResultStatus.PREPARED,
        OutreachResultStatus.COMPLETED,
    ) else 1


def _cmd_send(args: argparse.Namespace) -> int:
    prepared_path = (
        Path(args.prepared_file) if args.prepared_file else _PREPARED_PATH
    )
    prepared = _load_json(prepared_path, "prepared_result")
    if prepared is None:
        print(
            f"ERRORE: nessun outreach preparato trovato in '{prepared_path}'.\n"
            "Eseguire prima:\n"
            "  python -m mercury_foundry.outreach --prepare-latest",
            file=sys.stderr,
        )
        return 1

    msg_count = len([
        m for m in prepared.get("messages", [])
        if m.get("delivery_status") == "PREPARED"
    ])
    print("Mercury Outreach Agent — invio messaggi...")
    print(f"Messaggi pronti: {msg_count}")

    agent = OutreachAgent()  # smtp_fn=None → usa SMTP reale o BLOCKED
    result = agent.send_prepared(prepared)
    result_dict = result.to_dict()
    _print_sent(result_dict)

    output_path = Path(args.output) if args.output else _SENT_PATH
    result.save(output_path)
    print(f"\nRisultato salvato in: {output_path}")

    return 0 if result.status in (
        OutreachResultStatus.COMPLETED,
        OutreachResultStatus.PARTIAL,
        OutreachResultStatus.BLOCKED_EMAIL_PROVIDER_NOT_CONFIGURED,
    ) else 1


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mercury_foundry.outreach",
        description="Mercury Outreach Agent — primo contatto commerciale reale.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--prepare-latest",
        action="store_true",
        help="Prepara messaggi personalizzati per i lead DIRECT verificati.",
    )
    group.add_argument(
        "--send-latest",
        action="store_true",
        help="Invia i messaggi già preparati via SMTP (max 4).",
    )
    parser.add_argument(
        "--prepared-file",
        type=str,
        default=None,
        metavar="PATH",
        help=f"Percorso alternativo per prepared_latest.json (default: {_PREPARED_PATH}).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        metavar="PATH",
        help="Percorso file JSON di output.",
    )

    args = parser.parse_args(argv)

    if args.prepare_latest:
        return _cmd_prepare(args)
    if args.send_latest:
        return _cmd_send(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

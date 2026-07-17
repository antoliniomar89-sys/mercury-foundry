"""CLI per il Lead Enrichment Agent.

Utilizzo:
    python -m mercury_foundry.lead_enrichment --run-latest
    python -m mercury_foundry.lead_enrichment --lead-result-file PATH/TO/leads.json
    python -m mercury_foundry.lead_enrichment --run-latest --output output/leads/enriched_latest.json
    python -m mercury_foundry.lead_enrichment --verify-contacts-latest
    python -m mercury_foundry.lead_enrichment --verify-contacts-latest --output output/leads/contact_verified_latest.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mercury_foundry.lead_enrichment.agent import EnrichmentAgent
from mercury_foundry.lead_enrichment.contact_verify import ContactVerifier
from mercury_foundry.lead_enrichment.models import (
    EnrichedLeadResult,
    EnrichedLeadResultStatus,
    EnrichedLeadStatus,
)

_DEFAULT_LEAD_RESULT_PATH    = Path("output/leads/latest.json")
_DEFAULT_OUTPUT_PATH         = Path("output/leads/enriched_latest.json")
_DEFAULT_ENRICHED_PATH       = Path("output/leads/enriched_latest.json")
_DEFAULT_VERIFIED_OUTPUT_PATH = Path("output/leads/contact_verified_latest.json")


def _load_lead_result(path: Path) -> dict | None:
    """Carica un LeadResult da file JSON."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _has_real_provider() -> bool:
    return bool(
        os.environ.get("MERCURY_AI_PROVIDER")
        and os.environ.get("MERCURY_AI_API_KEY")
    )


def _print_result(result: EnrichedLeadResult) -> None:
    print()
    print(f"STATO: {result.status.value}")
    print(f"TIMESTAMP: {result.timestamp}")

    if result.status in (
        EnrichedLeadResultStatus.BLOCKED_INVALID_LEAD_INPUT,
        EnrichedLeadResultStatus.BLOCKED_NO_WEB_ACCESS,
        EnrichedLeadResultStatus.BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS,
        EnrichedLeadResultStatus.FAILED,
    ):
        print()
        print(f"BLOCCO:\n  {result.block_reason}")
        print()
        print(f"PROSSIMA AZIONE:\n  {result.next_action}")
        return

    total_usable = len(result.enriched_leads)
    print()
    print(
        f"LEAD ARRICCHITI: {total_usable} utilizzabili | "
        f"{result.high_fit_count} HIGH_FIT | "
        f"{result.plausible_count} PLAUSIBLE | "
        f"{result.needs_review_count} NEEDS_REVIEW | "
        f"{result.rejected_count} RIFIUTATI"
    )

    high_fit = [
        l for l in result.enriched_leads
        if l.qualification_status == EnrichedLeadStatus.HIGH_FIT
    ]
    if high_fit:
        print()
        print("HIGH FIT:")
        for i, lead in enumerate(high_fit, 1):
            print(f"\n  {i}. {lead.name}")
            print(f"     Ruolo/Business: {lead.verified_role_or_business}")
            print(f"     Sito: {lead.primary_website or 'N/D'}")
            print(f"     Contatto: {lead.public_contact or 'N/D'} ({lead.contact_type})")
            print(f"     Contactability: {lead.contactability.value}")
            print(f"     Motivo: {lead.fit_reason[:120]}")

    plausible = [
        l for l in result.enriched_leads
        if l.qualification_status == EnrichedLeadStatus.PLAUSIBLE
    ]
    if plausible:
        print()
        print("PLAUSIBLE:")
        for i, lead in enumerate(plausible, 1):
            print(f"\n  {i}. {lead.name}")
            print(f"     Ruolo/Business: {lead.verified_role_or_business}")
            print(f"     Sito: {lead.primary_website or 'N/D'}")
            print(f"     Contactability: {lead.contactability.value}")

    needs_review = [
        l for l in result.enriched_leads
        if l.qualification_status == EnrichedLeadStatus.NEEDS_REVIEW
    ]
    if needs_review:
        print()
        print(f"NEEDS REVIEW ({len(needs_review)}):")
        for lead in needs_review:
            print(f"  - {lead.name}: {lead.evidence_summary[:80]}")

    if result.rejected_leads:
        print()
        print(f"RIFIUTATI ({result.rejected_count}):")
        for lead in result.rejected_leads:
            print(f"  - {lead.name}: {lead.rejection_reason or 'non classificabile'}")

    print()
    print(f"FONTI CONSULTATE: {len(result.sources_consulted)}")
    print()
    print(f"PROSSIMA AZIONE:\n  {result.next_action}")


def _print_verified_result(result: dict) -> None:
    """Stampa il risultato della verifica contatti."""
    print()
    print(f"STATO: {result.get('status', 'N/D')}")
    print(f"TIMESTAMP: {result.get('timestamp', 'N/D')}")

    if result.get("block_reason"):
        print()
        print(f"BLOCCO:\n  {result['block_reason']}")
        print()
        print(f"PROSSIMA AZIONE:\n  {result.get('next_action', '')}")
        return

    direct   = [l for l in result.get("enriched_leads", []) if l.get("contactability") == "DIRECT"]
    indirect = [l for l in result.get("enriched_leads", []) if l.get("contactability") == "INDIRECT"]
    review   = [l for l in result.get("enriched_leads", []) if l.get("contactability") not in ("DIRECT", "INDIRECT")]
    none_    = result.get("rejected_leads", [])

    print()
    print(
        f"RISULTATO: {len(direct)} DIRECT | {len(indirect)} INDIRECT | "
        f"{len(review)} NEEDS_REVIEW | {len(none_)} NONE"
    )

    if direct:
        print()
        print("DIRECT (canale verificato):")
        for i, lead in enumerate(direct, 1):
            print(f"\n  {i}. {lead.get('name', 'N/D')}")
            print(f"     Canale: {lead.get('public_contact', 'N/D')} ({lead.get('contact_type', 'N/D')})")
            if lead.get("verified_email"):
                print(f"     Email:  {lead['verified_email']}")
            if lead.get("verified_form_url"):
                print(f"     Form:   {lead['verified_form_url']}")
            if lead.get("verified_social_url"):
                print(f"     Social: {lead['verified_social_url']}")
            print(f"     Evidenza: {lead.get('verification_evidence', 'N/D')[:120]}")

    if indirect:
        print()
        print("INDIRECT:")
        for lead in indirect:
            print(f"  - {lead.get('name', 'N/D')}: {lead.get('verified_social_url') or lead.get('public_contact', 'N/D')}")

    if none_:
        print()
        print(f"NONE ({len(none_)}) — nessun canale trovato:")
        for lead in none_:
            print(f"  - {lead.get('name', 'N/D')}: {lead.get('verification_evidence', 'N/D')[:80]}")

    print()
    print(f"PROSSIMA AZIONE:\n  {result.get('next_action', '')}")


def _run_verify_contacts(args: argparse.Namespace) -> int:
    """Esegue la verifica reale dei canali per enriched_latest.json."""
    input_path = (
        Path(args.enriched_file) if hasattr(args, "enriched_file") and args.enriched_file
        else _DEFAULT_ENRICHED_PATH
    )

    if not input_path.exists():
        print(
            f"ERRORE: EnrichedLeadResult non trovato in '{input_path}'.",
            file=sys.stderr,
        )
        print(
            f"Eseguire prima: python -m mercury_foundry.lead_enrichment --run-latest "
            f"--output {_DEFAULT_ENRICHED_PATH}",
            file=sys.stderr,
        )
        return 1

    try:
        enriched_result = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERRORE: impossibile leggere '{input_path}': {exc}", file=sys.stderr)
        return 1

    leads_count = len(enriched_result.get("enriched_leads", []))
    print("Mercury Contact Verifier — avvio verifica canali reali...")
    print(f"Lead da verificare: {leads_count}")

    verifier = ContactVerifier()
    result = verifier.run(enriched_result)
    _print_verified_result(result)

    output_path = (
        Path(args.output) if args.output else _DEFAULT_VERIFIED_OUTPUT_PATH
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nRisultato salvato in: {output_path}")

    status = result.get("status", "")
    return 0 if status in ("COMPLETED", "COMPLETED_WITH_REVIEW") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mercury_foundry.lead_enrichment",
        description="Mercury Lead Enrichment Agent — arricchisce e classifica lead grezzi.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--run-latest",
        action="store_true",
        help=f"Usa l'ultimo LeadResult salvato in {_DEFAULT_LEAD_RESULT_PATH}.",
    )
    group.add_argument(
        "--lead-result-file",
        type=str,
        default=None,
        metavar="PATH",
        help="Percorso di un LeadResult JSON da arricchire.",
    )
    group.add_argument(
        "--verify-contacts-latest",
        action="store_true",
        help=f"Verifica i canali reali dell'ultimo EnrichedLeadResult in {_DEFAULT_ENRICHED_PATH}.",
    )
    parser.add_argument(
        "--enriched-file",
        type=str,
        default=None,
        metavar="PATH",
        help=f"Percorso alternativo dell'EnrichedLeadResult per --verify-contacts-latest.",
    )
    parser.add_argument(
        "--lead-result-id",
        type=str,
        default=None,
        metavar="ID",
        help="Percorso alternativo o ID del LeadResult (usa come PATH se è un file).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=f"Percorso file JSON in cui salvare il risultato.",
    )

    args = parser.parse_args(argv)

    if args.verify_contacts_latest:
        return _run_verify_contacts(args)

    if not args.run_latest and not args.lead_result_file and not args.lead_result_id:
        parser.print_help()
        return 1

    # Determina path del LeadResult
    if args.lead_result_file:
        lr_path = Path(args.lead_result_file)
    elif args.lead_result_id:
        # Tratta come path file (convenzione semplice)
        lr_path = Path(args.lead_result_id)
    else:
        lr_path = _DEFAULT_LEAD_RESULT_PATH

    lead_result = _load_lead_result(lr_path)
    if lead_result is None:
        print(
            f"ERRORE: LeadResult non trovato in '{lr_path}'.",
            file=sys.stderr,
        )
        print(
            "Eseguire prima: python -m mercury_foundry.leads --run-latest "
            f"--output {_DEFAULT_LEAD_RESULT_PATH}",
            file=sys.stderr,
        )
        return 1

    leads_count = len(lead_result.get("leads", []))
    print("Mercury Lead Enrichment Agent — avvio arricchimento...")
    print(f"Lead grezzi da elaborare: {leads_count}")
    opp = lead_result.get("opportunity_summary", {})
    print(f"Target: {opp.get('target_customer', 'N/A')}")
    print(f"Offerta: {str(opp.get('proposed_offer', 'N/A'))[:80]}")

    if _has_real_provider():
        print("Provider AI: reale")
        try:
            agent = EnrichmentAgent.with_real_provider()
        except RuntimeError as exc:
            print(f"\nERRORE provider AI: {exc}", file=sys.stderr)
            return 1
    else:
        print("Provider AI: non configurato → classificazione con provider AI non disponibile")
        agent = EnrichmentAgent()

    result = agent.run(lead_result)
    _print_result(result)

    output_path = Path(args.output) if args.output else _DEFAULT_OUTPUT_PATH
    saved = result.save(output_path)
    print(f"\nRisultato salvato in: {saved}")

    return 0 if result.status in (
        EnrichedLeadResultStatus.COMPLETED,
        EnrichedLeadResultStatus.COMPLETED_WITH_REVIEW,
    ) else 1


if __name__ == "__main__":
    sys.exit(main())

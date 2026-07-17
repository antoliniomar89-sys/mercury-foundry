"""CLI per il Lead Agent.

Utilizzo:
    python -m mercury_foundry.leads --run-latest
    python -m mercury_foundry.leads --opportunity-file PATH/TO/opportunity.json
    python -m mercury_foundry.leads --run-latest --output output/leads.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mercury_foundry.leads.agent import LeadAgent
from mercury_foundry.leads.models import LeadResult, LeadResultStatus, LeadStatus

_DEFAULT_OPPORTUNITY_PATH = Path("output/opportunity/latest.json")


def _load_opportunity(path: Path) -> dict | None:
    """Carica un OpportunityResult da file JSON."""
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


def _print_result(result: LeadResult) -> None:
    print()
    print(f"STATO: {result.status.value}")
    print(f"TIMESTAMP: {result.timestamp}")

    if result.status != LeadResultStatus.COMPLETED:
        print()
        print(f"BLOCCO:\n  {result.block_reason}")
        print()
        print(f"PROSSIMA AZIONE:\n  {result.next_action}")
        return

    print()
    print(f"LEAD TROVATI: {len(result.leads)} totali | {result.qualified_count} qualificati | {result.rejected_count} rifiutati | {result.duplicates_discarded} duplicati scartati")

    qualified = [l for l in result.leads if l.status == LeadStatus.QUALIFIED]
    if qualified:
        print()
        print("LEAD QUALIFICATI:")
        for i, lead in enumerate(qualified, 1):
            print(f"\n  {i}. [{lead.priority.value}] {lead.name}")
            print(f"     Segmento: {lead.segment}")
            print(f"     Sito:     {lead.website}")
            print(f"     Contatto: {lead.public_contact} ({lead.contact_type})")
            print(f"     Location: {lead.location or 'N/D'}")
            print(f"     Motivo:   {lead.fit_reason}")
            print(f"     Evidenza: {lead.evidence[:150]}")

    rejected = [l for l in result.leads if l.status == LeadStatus.REJECTED]
    if rejected:
        print()
        print(f"LEAD RIFIUTATI ({len(rejected)}):")
        for l in rejected:
            print(f"  - {l.name}: {l.rejection_reason or 'non qualificato'}")

    print()
    print(f"QUERY USATE: {', '.join(result.search_queries)}")
    print(f"FONTI: {', '.join(result.sources_used)}")
    print()
    print(f"PROSSIMA AZIONE:\n  {result.next_action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mercury_foundry.leads",
        description="Mercury Lead Agent — trova lead reali da un OpportunityResult.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--run-latest",
        action="store_true",
        help=f"Usa l'ultimo OpportunityResult salvato in {_DEFAULT_OPPORTUNITY_PATH}.",
    )
    group.add_argument(
        "--opportunity-file",
        type=str,
        default=None,
        metavar="PATH",
        help="Percorso di un OpportunityResult JSON da usare come input.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Percorso file JSON in cui salvare i lead trovati.",
    )

    args = parser.parse_args(argv)

    if not args.run_latest and not args.opportunity_file:
        parser.print_help()
        return 1

    # Carica opportunity
    opp_path = (
        Path(args.opportunity_file)
        if args.opportunity_file
        else _DEFAULT_OPPORTUNITY_PATH
    )
    opportunity = _load_opportunity(opp_path)
    if opportunity is None:
        print(f"ERRORE: OpportunityResult non trovato in '{opp_path}'.", file=sys.stderr)
        print(
            "Eseguire prima: python -m mercury_foundry.opportunity --run "
            f"--output {_DEFAULT_OPPORTUNITY_PATH}",
            file=sys.stderr,
        )
        return 1

    print("Mercury Lead Agent — avvio ricerca lead...")
    print(f"Target: {opportunity.get('target_customer', 'N/A')}")
    print(f"Offerta: {opportunity.get('proposed_offer', 'N/A')[:80]}")

    if _has_real_provider():
        print("Provider AI: reale")
        try:
            agent = LeadAgent.with_real_provider()
        except RuntimeError as exc:
            print(f"\nERRORE provider AI: {exc}", file=sys.stderr)
            return 1
    else:
        print("Provider AI: non configurato → analisi con provider AI non disponibile")
        agent = LeadAgent()

    result = agent.run(opportunity)
    _print_result(result)

    if args.output:
        saved = result.save(args.output)
        print(f"\nRisultato salvato in: {saved}")

    return 0 if result.status == LeadResultStatus.COMPLETED else 1


if __name__ == "__main__":
    sys.exit(main())

"""CLI per l'Opportunity Agent.

Utilizzo:
    python -m mercury_foundry.opportunity --run
    python -m mercury_foundry.opportunity --run --mandate "Trova opportunità nel settore ristorativo"
    python -m mercury_foundry.opportunity --run --output output/opportunity.json
"""
from __future__ import annotations

import argparse
import os
import sys

from mercury_foundry.opportunity.agent import DEFAULT_MANDATE, OpportunityAgent
from mercury_foundry.opportunity.models import OpportunityResult, OpportunityStatus


def _has_real_provider() -> bool:
    """Controlla se le variabili d'ambiente per il provider AI sono configurate."""
    return bool(
        os.environ.get("MERCURY_AI_PROVIDER")
        and os.environ.get("MERCURY_AI_API_KEY")
    )


def _print_result(result: OpportunityResult) -> None:
    print()
    print(f"STATO: {result.status.value}")
    print(f"TIMESTAMP: {result.timestamp}")

    if result.status != OpportunityStatus.COMPLETED:
        print()
        print(f"BLOCCO:\n  {result.block_reason}")
        print()
        print(f"PROSSIMA AZIONE:\n  {result.next_action}")
        return

    print()
    print(f"PROBLEMA:\n  {result.problem}")
    print()
    print(f"TARGET:\n  {result.target_customer}")

    if result.evidence:
        print()
        print("EVIDENZE:")
        for i, ev in enumerate(result.evidence, 1):
            print(f"  {i}. [{ev.source_url}]")
            print(f"     {ev.text[:250]}")

    print()
    print(f"OFFERTA:\n  {result.proposed_offer}")
    print()
    print(f"FORMATO:\n  {result.delivery_format}")
    print()
    print(f"PREZZO:\n  {result.initial_price}")
    print()
    print(f"PERCHÉ TESTABILE RAPIDAMENTE:\n  {result.why_testable_fast}")

    if result.risks:
        print()
        print("RISCHI:")
        for r in result.risks:
            print(f"  - {r}")

    print()
    print(f"PROSSIMA AZIONE:\n  {result.next_action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mercury_foundry.opportunity",
        description="Mercury Opportunity Agent — ricerca autonoma di opportunità di mercato.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Avvia una singola ricerca di opportunità.",
    )
    parser.add_argument(
        "--mandate",
        type=str,
        default=None,
        help=f"Mandato personalizzato. Default: '{DEFAULT_MANDATE}'",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Percorso file JSON in cui salvare il risultato.",
    )

    args = parser.parse_args(argv)

    if not args.run:
        parser.print_help()
        return 1

    print("Mercury Opportunity Agent — avvio ricerca...")
    print(f"Mandato: {args.mandate or DEFAULT_MANDATE}")

    if _has_real_provider():
        print("Provider AI: reale (MERCURY_AI_PROVIDER configurato)")
        try:
            agent = OpportunityAgent.with_real_provider()
        except RuntimeError as exc:
            print(f"\nERRORE provider AI: {exc}", file=sys.stderr)
            return 1
    else:
        print("Provider AI: non configurato → solo fetch segnali (analisi AI non disponibile)")
        agent = OpportunityAgent()

    result = agent.run(mandate=args.mandate)
    _print_result(result)

    if args.output:
        saved = result.save(args.output)
        print(f"\nRisultato salvato in: {saved}")

    return 0 if result.status == OpportunityStatus.COMPLETED else 1


if __name__ == "__main__":
    sys.exit(main())

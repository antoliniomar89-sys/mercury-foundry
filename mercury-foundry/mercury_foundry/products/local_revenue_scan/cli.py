"""CLI per il Revenue Scan.

Equivalente a:
    python -m mercury_foundry.products.local_revenue_scan \
        --input examples/revenue_scan_demo.json \
        --output-dir output/revenue_scan_demo

Flusso:
  1. Legge il brief JSON da --input
  2. Valida il brief (RevenueScanBrief.from_dict)
  3. Carica il provider AI reale (fallisce esplicitamente se non configurato)
  4. Genera il report
  5. Esegue il quality gate
  6. Salva JSON, Markdown e HTML in --output-dir
  7. Stampa i percorsi e lo stato finale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m mercury_foundry.products.local_revenue_scan",
        description="Mercury Revenue Scan — Audit operativo per locali di hospitality",
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="BRIEF_JSON",
        help="percorso al file JSON del brief cliente",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        metavar="DIR",
        help="cartella dove salvare JSON, Markdown e HTML",
    )
    return parser


def cmd_revenue_scan(args: argparse.Namespace) -> int:
    from mercury_foundry.ai.provider_config import ProviderConfigError
    from mercury_foundry.products.local_revenue_scan.models import RevenueScanBrief
    from mercury_foundry.products.local_revenue_scan.renderer import save_outputs
    from mercury_foundry.products.local_revenue_scan.service import (
        RevenueScanService,
        _build_real_generate_fn,
    )

    # 1. Lettura brief
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERRORE: file brief non trovato: {input_path}", file=sys.stderr)
        return 1

    try:
        raw_brief = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERRORE: brief JSON non valido: {exc}", file=sys.stderr)
        return 1

    # 2. Validazione brief
    try:
        brief = RevenueScanBrief.from_dict(raw_brief)
    except (KeyError, ValueError) as exc:
        print(f"ERRORE: brief non valido — {exc}", file=sys.stderr)
        return 1

    # 3. Provider AI
    try:
        generate_fn = _build_real_generate_fn()
    except (RuntimeError, ProviderConfigError) as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        print(
            "\nPer usare un provider AI reale, configura le seguenti variabili:\n"
            "  MERCURY_AI_PROVIDER=openai_compatible\n"
            "  MERCURY_AI_API_KEY=<chiave API>\n"
            "  MERCURY_AI_MODEL=<nome modello>\n"
            "  MERCURY_AI_BASE_URL=<endpoint> (opzionale per OpenAI nativo)\n",
            file=sys.stderr,
        )
        return 1

    # 4. Generazione + quality gate
    service = RevenueScanService(generate_fn=generate_fn)
    report = service.generate(brief)

    # 5. Salvataggio output
    output_dir = Path(args.output_dir)
    paths = save_outputs(report, output_dir)

    # 6. Output CLI
    print(f"\n✓ Revenue Scan completato")
    print(f"  Attività:   {report.business_name}")
    print(f"  Punteggio:  {report.overall_score}/100")
    print(f"  Confidenza: {report.confidence_level}/100")
    print(f"  Stato:      {report.status.value}")
    print(f"\nFile generati:")
    for fmt, path in sorted(paths.items()):
        print(f"  {fmt:10s} → {path}")

    if report.quality_issues:
        print(f"\n⚠️  Quality gate: {len(report.quality_issues)} problema/i", file=sys.stderr)
        for issue in report.quality_issues:
            print(f"    - {issue}", file=sys.stderr)
        return 2

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return cmd_revenue_scan(args)

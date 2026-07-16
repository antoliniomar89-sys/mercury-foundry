"""CLI per MF-VERIFY-001 — Adaptive Verification and Development Cost Governor.

Comandi:
    python -m mercury_foundry.verification plan
    python -m mercury_foundry.verification run
    python -m mercury_foundry.verification run --level targeted
    python -m mercury_foundry.verification run --level full
    python -m mercury_foundry.verification status
    python -m mercury_foundry.verification doctor

Il comando plan mostra:
    - file modificati
    - rischio
    - test selezionati
    - livello
    - motivazione
    - costo stimato in op-unit
    - budget residuo (se --mission-id fornito)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog        = "python -m mercury_foundry.verification",
        description = "MF-VERIFY-001 — Adaptive Verification and Development Cost Governor",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- plan ---
    p_plan = sub.add_parser("plan", help="Mostra il piano di verifica senza eseguire")
    p_plan.add_argument(
        "--files", nargs="*", metavar="FILE",
        help="File modificati da analizzare (default: git diff HEAD)"
    )
    p_plan.add_argument("--level", choices=["static", "targeted", "impacted", "full"],
                        help="Forza un livello specifico")
    p_plan.add_argument("--mission-id", metavar="ID",
                        help="ID missione per mostrare il budget residuo")
    p_plan.add_argument("--trigger", action="append", dest="triggers", metavar="TRIGGER",
                        help="Trigger aggiuntivi (es. milestone, schema_changed)")

    # --- run ---
    p_run = sub.add_parser("run", help="Esegue i test selezionati adattativamente")
    p_run.add_argument(
        "--files", nargs="*", metavar="FILE",
        help="File modificati da analizzare (default: git diff HEAD)"
    )
    p_run.add_argument("--level", choices=["static", "targeted", "impacted", "full"],
                       help="Forza un livello specifico")
    p_run.add_argument("--mission-id", metavar="ID",
                       help="ID missione per tracciare il budget")
    p_run.add_argument("--trigger", action="append", dest="triggers", metavar="TRIGGER",
                       help="Trigger aggiuntivi (es. milestone)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Mostra il piano senza eseguire")
    p_run.add_argument("--max-iterations", type=int, default=8,
                       help="Budget: iterazioni massime (default 8)")
    p_run.add_argument("--max-test-runs", type=int, default=12,
                       help="Budget: test run massimi (default 12)")
    p_run.add_argument("--max-full-suite-runs", type=int, default=1,
                       help="Budget: suite complete massime (default 1)")

    # --- status ---
    p_status = sub.add_parser("status", help="Mostra lo stato del budget per una missione")
    p_status.add_argument("--mission-id", required=True, metavar="ID",
                          help="ID missione")

    # --- doctor ---
    sub.add_parser("doctor", help="Esegui i check del Verification Layer")

    return parser.parse_args()


def cmd_plan(args: argparse.Namespace) -> int:
    from mercury_foundry.verification.runner import VerificationRunner
    from mercury_foundry.verification.models import VerificationLevel

    runner = VerificationRunner()

    force_level = None
    if args.level:
        force_level = VerificationLevel.from_str(args.level)

    triggers = set(args.triggers or [])

    plan = runner.plan(
        changed_files = args.files,
        force_level   = force_level,
        triggers      = triggers,
        mission_id    = getattr(args, "mission_id", None),
    )

    print(plan.summary())

    if getattr(args, "mission_id", None):
        try:
            status = runner.status(args.mission_id)
            print()
            print(f"Budget residuo [{args.mission_id}]:")
            print(f"  Iterazioni usate: {status.iterations_used}")
            print(f"  Test run usati:   {status.test_runs_used}")
            print(f"  Full suite usate: {status.full_suite_runs_used}")
            print(f"  Budget esaurito:  {status.exhausted}")
        except Exception:
            pass

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from mercury_foundry.verification.runner import VerificationRunner
    from mercury_foundry.verification.budget import BudgetExhaustedError
    from mercury_foundry.verification.models import CostBudget, VerificationLevel

    runner = VerificationRunner()
    mission_id = getattr(args, "mission_id", None)

    # Inizializza budget se mission_id fornito
    if mission_id:
        budget = CostBudget(
            mission_id                    = mission_id,
            max_iterations                = args.max_iterations,
            max_test_runs                 = args.max_test_runs,
            max_full_suite_runs           = args.max_full_suite_runs,
            stop_on_budget_exhaustion     = True,
        )
        runner.start_mission(budget)

    force_level = None
    if args.level:
        force_level = VerificationLevel.from_str(args.level)

    triggers = set(args.triggers or [])

    plan = runner.plan(
        changed_files = args.files,
        force_level   = force_level,
        triggers      = triggers,
        mission_id    = mission_id,
    )

    print(plan.summary())
    print()

    if args.dry_run:
        print("[dry-run] Nessun test eseguito.")
        return 0

    print(f"Avvio esecuzione livello {plan.level.label()}...")
    try:
        record = runner.run(plan, mission_id=mission_id)
    except BudgetExhaustedError as e:
        print(f"STOP: {e}")
        if mission_id:
            report = runner.get_escalation_report(mission_id)
            print()
            print(report.render())
            if runner.should_propose_rollback(mission_id):
                print()
                print(
                    "ROLLBACK SUGGERITO: il sistema ha esaurito il budget senza miglioramento. "
                    "Usare 'git stash' o tornare all'ultimo checkpoint per ripristinare "
                    "lo stato stabile. Nessun reset automatico eseguito."
                )
        return 1

    print()
    print(f"Risultato [{plan.level.label()}]:")
    print(f"  Passed:   {record.passed}")
    print(f"  Failed:   {record.failed}")
    print(f"  Errors:   {record.errors}")
    print(f"  Durata:   {record.duration_seconds:.1f}s")
    print(f"  Da cache: {record.from_cache}")

    if record.failed_test_ids:
        print(f"  Test falliti: {record.failed_test_ids[:5]}")

    return 0 if record.failed == 0 and record.errors == 0 else 1


def cmd_status(args: argparse.Namespace) -> int:
    from mercury_foundry.verification.runner import VerificationRunner
    from mercury_foundry.verification.budget import MissionNotStartedError

    runner = VerificationRunner()
    try:
        status = runner.status(args.mission_id)
    except MissionNotStartedError as e:
        print(f"ERRORE: {e}")
        return 1

    print(f"Budget status — Missione: {args.mission_id}")
    print(f"  Iterazioni usate:            {status.iterations_used}")
    print(f"  Test run usati:              {status.test_runs_used}")
    print(f"  Full suite run usati:        {status.full_suite_runs_used}")
    print(f"  Falliti senza miglioramento: {status.failed_runs_without_improvement}")
    print(f"  Tempo trascorso:             {status.elapsed_seconds:.1f}s")
    print(f"  Budget esaurito:             {status.exhausted}")
    if status.exhaustion_reason:
        print(f"  Motivo:                      {status.exhaustion_reason}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from mercury_foundry import config
    from mercury_foundry.verification.diagnostics import run_verification_checks

    results = run_verification_checks(config.BASE_DIR)
    for r in results:
        print(repr(r))
    failed = [r for r in results if not r.ok]
    print()
    if not failed:
        print("Verification Layer: OK")
    else:
        print(f"Verification Layer: {len(failed)} check falliti")
    return 0 if not failed else 1


def main() -> int:
    args = _parse_args()
    if args.command == "plan":
        return cmd_plan(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    print(f"Comando sconosciuto: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

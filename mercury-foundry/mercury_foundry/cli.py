"""Interfaccia iniziale minimale (CLI) di Mercury Foundry V0.

Comandi:
  submit "<obiettivo>"           sottomette un obiettivo ed esegue il ciclo completo
  status [--goal ID]             mostra lo stato di goal/task/candidate/attempts
  approve <candidate_id>         approva una candidate (Approval Gate umano)
  reject <candidate_id>          rifiuta una candidate
  export-candidate <id>          esporta il pacchetto di una candidate per revisione (senza promuovere)
  audit [--entity-type T --entity-id N] [--limit N]   mostra l'audit log
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mercury_foundry.ai.errors import ProviderExecutionError
from mercury_foundry.ai.provider_factory import ProviderUnavailableError, get_provider
from mercury_foundry.approval import gate
from mercury_foundry.approval.human_gate import (
    ApprovalChannelDisabledError,
    ApprovalSecretMissingError,
    HumanApprovalToken,
    RuntimeApprovalBlockedError,
    approve_candidate as human_approve_candidate,
    export_candidate_package,
)
from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.diagnostics import run_doctor
from mercury_foundry.policy.literal_constraints import LiteralConstraints
from mercury_foundry.state import models
from mercury_foundry.wiring import build_foundry


def _load_literal_constraints(path: str | None) -> LiteralConstraints | None:
    """Carica un `LiteralConstraints` da un file JSON, se `path` è fornito.

    Nessun default silenzioso: un percorso inesistente o un JSON malformato
    fa fallire subito il comando (`FileNotFoundError`/`json.JSONDecodeError`),
    invece di sottomettere il goal senza i vincoli richiesti.
    """
    if not path:
        return None
    raw = Path(path).read_text(encoding="utf-8")
    return LiteralConstraints.from_dict(json.loads(raw))


def cmd_check_provider(args: argparse.Namespace) -> int:
    """Verifica di connettività ESPLICITA verso un provider reale.

    Non scrive nulla in target_project/ (non usa Builder/Workspace): fa solo
    UNA chiamata di pianificazione minimale, se e solo se `--confirm` è
    passato esplicitamente. Senza `--confirm` non viene eseguita alcuna
    chiamata (nessuna chiamata a pagamento automatica).
    """
    provider_name = args.provider or "openai"

    if not args.confirm:
        print(
            f"[check-provider] provider richiesto: '{provider_name}'. Nessuna chiamata eseguita: "
            "ripetere il comando con --confirm per autorizzare esplicitamente una chiamata reale "
            "(può avere un costo)."
        )
        return 0

    try:
        provider = get_provider(provider_name)
    except ProviderUnavailableError as exc:
        print(f"[check-provider] provider non configurabile in modo sicuro: {exc}")
        return 1

    if provider.is_simulated:
        print(
            f"[check-provider] provider '{provider.name}' è SIMULATO (is_simulated=True): "
            "nessuna chiamata di rete da verificare."
        )
        return 0

    print(f"[check-provider] provider reale confermato: '{provider.name}'. Eseguo una chiamata di prova...")
    try:
        result = provider.check_connectivity(
            "Rispondi con status='ok' e un breve messaggio che confermi la connettività."
        )
    except ProviderExecutionError as exc:
        record = provider.last_call_record
        print(f"[check-provider] chiamata fallita: {type(exc).__name__}: {exc}")
        if record is not None:
            print(
                f"  provider={record.provider_name} model={record.model} "
                f"call_number={record.call_number} success={record.success} "
                f"usage={record.usage} estimated_cost_usd={record.estimated_cost_usd} "
                f"error={record.error_summary}"
            )
        return 1

    record = provider.last_call_record
    print(f"[check-provider] chiamata riuscita (Structured Outputs, schema stretto). Risposta: {result}")
    if record is not None:
        print(
            f"  provider={record.provider_name} model={record.model} "
            f"usage={record.usage} estimated_cost_usd={record.estimated_cost_usd}"
        )
    return 0


def _simulated_tag(is_simulated: bool) -> str:
    return "[SIMULATO]" if is_simulated else "[REALE]"


def cmd_doctor(args: argparse.Namespace) -> int:
    report = run_doctor(db_path=args.db, sandbox_root=args.sandbox, provider_name=args.provider)
    print(report.render())
    return 0 if not report.has_errors() else 1


def cmd_submit(args: argparse.Namespace) -> int:
    foundry = build_foundry(db_path=args.db, sandbox_root=args.sandbox, provider_name=args.provider)
    print(
        f"[provider] {foundry.ai_provider.name} "
        f"{_simulated_tag(foundry.ai_provider.is_simulated)} (is_simulated={foundry.ai_provider.is_simulated})"
    )

    literal_constraints = _load_literal_constraints(args.literal_constraints)
    if literal_constraints is not None:
        print(f"[literal_constraints] caricati da {args.literal_constraints}: {literal_constraints.to_dict()}")

    goal_id = foundry.orchestrator.submit_goal(args.description, literal_constraints=literal_constraints)
    print(f"[goal] creato goal_id={goal_id}: {args.description}")

    goal_run = foundry.orchestrator.run_goal(goal_id)
    for outcome in goal_run.task_outcomes:
        print(
            f"[task {outcome.task_id}] stato={outcome.status} "
            f"tentativi_usati={outcome.attempts_used} candidate_id={outcome.candidate_id}"
        )
    print(f"[goal {goal_id}] stato finale={goal_run.final_status}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    foundry = build_foundry(db_path=args.db, sandbox_root=args.sandbox)
    conn = foundry.conn

    goals = [models.get_goal(conn, args.goal)] if args.goal else models.list_goals(conn)
    for goal in goals:
        if goal is None:
            continue
        print(f"GOAL {goal['id']} [{goal['status']}] {goal['description']}")
        for task in models.get_tasks_for_goal(conn, goal["id"]):
            print(f"  TASK {task['id']} [{task['status']}] {task['description']}")
            for attempt in models.get_attempts_for_task(conn, task["id"]):
                print(
                    f"    ATTEMPT {attempt['id']} #{attempt['attempt_number']} "
                    f"phase={attempt['phase']} status={attempt['status']} "
                    f"provider={attempt['provider_name']} simulated={bool(attempt['is_simulated'])}"
                )
        for candidate in models.list_candidates(conn, goal["id"]):
            tag = _simulated_tag(bool(candidate["is_simulated"]))
            print(
                f"  CANDIDATE {candidate['id']} [{candidate['status']}] {tag} "
                f"provider={candidate['provider_name']} {candidate['summary']}"
            )
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    foundry = build_foundry(db_path=args.db, sandbox_root=args.sandbox, provider_name=args.provider)
    candidate = models.get_candidate(foundry.conn, args.candidate_id)
    if candidate is not None and bool(candidate["is_simulated"]):
        print(
            f"ATTENZIONE: la candidate {args.candidate_id} è stata generata dal provider "
            f"SIMULATO '{candidate['provider_name']}' — non è codice scritto da un'AI reale."
        )

    expected_confirmation = f"APPROVE-{args.candidate_id}-CONFIRMED"
    if not args.confirm_id:
        print(
            f"[approve] Per approvare la candidate {args.candidate_id}, ripetere il comando "
            f"con --confirm-id {expected_confirmation!r}. Questo conferma che stai approvando "
            "consapevolmente questa specifica candidate e non un'altra."
        )
        return 1

    try:
        token = HumanApprovalToken(args.confirm_id)
        human_approve_candidate(foundry.conn, args.candidate_id, rationale=args.reason, token=token)
    except ApprovalChannelDisabledError as exc:
        print(f"[approve] CANALE DISABILITATO: {exc}")
        return 1
    except ApprovalSecretMissingError as exc:
        print(f"[approve] SEGRETO MANCANTE: {exc}")
        return 1
    except RuntimeApprovalBlockedError as exc:
        print(f"[approve] BLOCCATO: {exc}")
        return 1

    print(f"[candidate {args.candidate_id}] approvata da umano. rationale={args.reason!r}")
    return 0


def cmd_export_candidate(args: argparse.Namespace) -> int:
    """Esporta il pacchetto di una candidate per revisione umana esterna.

    Non promuove nulla nel target reale. Non crea decisioni. Mostra il
    manifest e lista i file dello staging; crea uno zip se --output-dir
    è specificato.
    """
    import json as _json

    foundry = build_foundry(db_path=args.db, sandbox_root=args.sandbox)
    output_dir = Path(args.output_dir) if args.output_dir else None

    result = export_candidate_package(foundry.conn, args.candidate_id, output_dir=output_dir)

    print(f"[export-candidate] candidate_id={result['candidate_id']} status={result['status']}")
    print(f"[export-candidate] provider={result['provider_name']} simulated={result['is_simulated']}")
    print(f"[export-candidate] promoted=False  target_modified=False")
    print()
    print("[manifest]")
    print(_json.dumps(result["manifest"], indent=2, ensure_ascii=False))

    if result["staging_files"]:
        print()
        print("[staging files]")
        for rel, info in result["staging_files"].items():
            print(f"  {rel}  ({info['size']} byte)  {info['path']}")
    else:
        print()
        print("[staging files] staging non più disponibile (già rimossa dopo la promozione)")

    if result["zip_path"]:
        print()
        print(f"[zip] pacchetto esportato in: {result['zip_path']}")

    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    foundry = build_foundry(db_path=args.db, sandbox_root=args.sandbox, provider_name=args.provider)
    gate.reject_candidate(foundry.conn, args.candidate_id, rationale=args.reason)
    print(f"[candidate {args.candidate_id}] rifiutata da umano. rationale={args.reason!r}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    foundry = build_foundry(db_path=args.db, sandbox_root=args.sandbox, provider_name=args.provider)
    rows = list_audit_log(
        foundry.conn, entity_type=args.entity_type, entity_id=args.entity_id, limit=args.limit
    )
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        print(
            f"[{row['id']}] {row['created_at']} actor={row['actor']} "
            f"{row['entity_type']}#{row['entity_id']} {row['action']} {json.dumps(payload, ensure_ascii=False)}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mercury-foundry", description=__doc__)
    parser.add_argument("--db", default=None, help="percorso del DB SQLite (default: data/mercury_foundry.db)")
    parser.add_argument("--sandbox", default=None, help="cartella sandbox target (default: target_project/)")
    parser.add_argument(
        "--provider",
        default=None,
        help="nome del provider AI da usare (default: 'fake', o $MERCURY_AI_PROVIDER)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_doctor = subparsers.add_parser("doctor", help="diagnostica lo stato dell'installazione")
    p_doctor.set_defaults(func=cmd_doctor)

    p_check_provider = subparsers.add_parser(
        "check-provider",
        help="verifica di connettività esplicita verso un provider reale (nessuna scrittura in target_project)",
    )
    p_check_provider.add_argument(
        "--confirm",
        action="store_true",
        help="autorizza esplicitamente l'esecuzione di UNA chiamata reale (può avere un costo)",
    )
    p_check_provider.set_defaults(func=cmd_check_provider)

    p_submit = subparsers.add_parser("submit", help="sottomette un obiettivo ed esegue il ciclo completo")
    p_submit.add_argument("description", help="descrizione testuale dell'obiettivo")
    p_submit.add_argument(
        "--literal-constraints",
        default=None,
        help=(
            "percorso a un file JSON con vincoli letterali deterministici "
            "(LiteralConstraints: exact_file_path, exact_file_content, allowed_files, "
            "forbidden_extra_files, exact_test_command, byte_exact_required). Il testo "
            "letterale qui non viene mai rigenerato dal provider AI: è applicato o "
            "verificato deterministicamente dal motore."
        ),
    )
    p_submit.set_defaults(func=cmd_submit)

    p_status = subparsers.add_parser("status", help="mostra lo stato di goal/task/candidate")
    p_status.add_argument("--goal", type=int, default=None, help="id del goal da mostrare (default: tutti)")
    p_status.set_defaults(func=cmd_status)

    p_approve = subparsers.add_parser("approve", help="approva una candidate (azione umana esplicita)")
    p_approve.add_argument("candidate_id", type=int)
    p_approve.add_argument("--reason", default=None)
    p_approve.add_argument(
        "--confirm-id",
        default=None,
        dest="confirm_id",
        metavar="APPROVE-N-CONFIRMED",
        help=(
            "Stringa di conferma esplicita obbligatoria: deve essere esattamente "
            "'APPROVE-{candidate_id}-CONFIRMED'. Impedisce approvazioni accidentali "
            "o automatizzate — un agente non può aggiungere questo flag senza conoscere "
            "l'ID specifico della candidate che intende approvare."
        ),
    )
    p_approve.set_defaults(func=cmd_approve)

    p_reject = subparsers.add_parser("reject", help="rifiuta una candidate (azione umana)")
    p_reject.add_argument("candidate_id", type=int)
    p_reject.add_argument("--reason", default=None)
    p_reject.set_defaults(func=cmd_reject)

    p_export = subparsers.add_parser(
        "export-candidate",
        help="esporta il pacchetto di una candidate per revisione umana (senza promuovere)",
    )
    p_export.add_argument("candidate_id", type=int)
    p_export.add_argument(
        "--output-dir",
        default=None,
        dest="output_dir",
        metavar="DIR",
        help="se specificato, crea uno zip del pacchetto nella cartella indicata",
    )
    p_export.set_defaults(func=cmd_export_candidate)

    p_audit = subparsers.add_parser("audit", help="mostra l'audit log append-only")
    p_audit.add_argument("--entity-type", default=None)
    p_audit.add_argument("--entity-id", type=int, default=None)
    p_audit.add_argument("--limit", type=int, default=200)
    p_audit.set_defaults(func=cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

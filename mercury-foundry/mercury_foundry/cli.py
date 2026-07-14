"""Interfaccia iniziale minimale (CLI) di Mercury Foundry V0.

Comandi:
  submit "<obiettivo>"           sottomette un obiettivo ed esegue il ciclo completo
  status [--goal ID]             mostra lo stato di goal/task/candidate/attempts
  approve <candidate_id>         approva una candidate (Approval Gate umano)
  reject <candidate_id>          rifiuta una candidate
  audit [--entity-type T --entity-id N] [--limit N]   mostra l'audit log
"""

from __future__ import annotations

import argparse
import json
import sys

from mercury_foundry.approval import gate
from mercury_foundry.audit.logger import list_audit_log
from mercury_foundry.state import models
from mercury_foundry.wiring import build_foundry


def cmd_submit(args: argparse.Namespace) -> int:
    foundry = build_foundry(db_path=args.db, sandbox_root=args.sandbox)
    print(f"[provider] {foundry.ai_provider.name} (is_simulated={foundry.ai_provider.is_simulated})")

    goal_id = foundry.orchestrator.submit_goal(args.description)
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
            print(f"  CANDIDATE {candidate['id']} [{candidate['status']}] {candidate['summary']}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    foundry = build_foundry(db_path=args.db, sandbox_root=args.sandbox)
    gate.approve_candidate(foundry.conn, args.candidate_id, rationale=args.reason)
    print(f"[candidate {args.candidate_id}] approvata da umano. rationale={args.reason!r}")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    foundry = build_foundry(db_path=args.db, sandbox_root=args.sandbox)
    gate.reject_candidate(foundry.conn, args.candidate_id, rationale=args.reason)
    print(f"[candidate {args.candidate_id}] rifiutata da umano. rationale={args.reason!r}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    foundry = build_foundry(db_path=args.db, sandbox_root=args.sandbox)
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

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_submit = subparsers.add_parser("submit", help="sottomette un obiettivo ed esegue il ciclo completo")
    p_submit.add_argument("description", help="descrizione testuale dell'obiettivo")
    p_submit.set_defaults(func=cmd_submit)

    p_status = subparsers.add_parser("status", help="mostra lo stato di goal/task/candidate")
    p_status.add_argument("--goal", type=int, default=None, help="id del goal da mostrare (default: tutti)")
    p_status.set_defaults(func=cmd_status)

    p_approve = subparsers.add_parser("approve", help="approva una candidate (azione umana)")
    p_approve.add_argument("candidate_id", type=int)
    p_approve.add_argument("--reason", default=None)
    p_approve.set_defaults(func=cmd_approve)

    p_reject = subparsers.add_parser("reject", help="rifiuta una candidate (azione umana)")
    p_reject.add_argument("candidate_id", type=int)
    p_reject.add_argument("--reason", default=None)
    p_reject.set_defaults(func=cmd_reject)

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

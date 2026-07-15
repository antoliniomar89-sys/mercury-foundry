"""Execution Loop — state machine deterministica:

SPEC -> PLAN -> BUILD -> TEST -> FIX -> VERIFY -> CANDIDATE

Il modello AI (reale o FakeModel) propone SOLO il contenuto di piano/patch.
Le transizioni di stato, il conteggio dei tentativi (max 3) e il giudizio
pass/fail sono decisi qui da codice deterministico, mai dal modello.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mercury_foundry import config
from mercury_foundry.agents.builder import Builder
from mercury_foundry.agents.evaluator import Evaluator
from mercury_foundry.ai.errors import ProviderExecutionError
from mercury_foundry.audit.logger import log_action
from mercury_foundry.policy.errors import BuildIncompleteError, LiteralConstraintViolationError
from mercury_foundry.policy.literal_constraints import LiteralConstraints, verify_literal_constraints
from mercury_foundry.sandbox.staging import compute_tree_snapshot, create_staging, diff_snapshots, discard_staging
from mercury_foundry.sandbox.test_env import sanitize_test_output
from mercury_foundry.sandbox.workspace import Workspace
from mercury_foundry.state import models


def _persist_call_record(conn, *, goal_id: int, task_id: int, attempt_id: int | None, record) -> None:
    """Persiste un ProviderCallRecord, se il provider ne ha prodotto uno.

    I provider simulati (FakeModel) non fanno chiamate esterne e lasciano
    `last_call_record = None`: in quel caso non viene scritta alcuna riga.

    `run_id` è derivato da `goal_id` (stessa convenzione di
    `Orchestrator.submit_goal`: un goal sottomesso è un run del Foundry),
    così le chiamate PLAN e BUILD dello stesso goal condividono un run_id e
    la deduplicazione in `models.create_provider_call` funziona su tutto il
    ciclo di vita del goal, non solo entro una singola fase.
    """
    models.persist_provider_call_record(
        conn,
        run_id=str(goal_id),
        goal_id=goal_id,
        task_id=task_id,
        attempt_id=attempt_id,
        record=record,
    )


@dataclass
class TaskOutcome:
    task_id: int
    status: str  # candidate_created | blocked
    attempts_used: int
    candidate_id: int | None = None


class ExecutionLoop:
    def __init__(
        self,
        conn: sqlite3.Connection,
        builder: Builder,
        evaluator: Evaluator,
        staging_base_dir: Path | None = None,
    ):
        self.conn = conn
        self.builder = builder
        self.evaluator = evaluator
        # Radice sotto cui vivono gli staging per-tentativo. Parametro
        # opzionale con default sensato (`config.STAGING_BASE_DIR`), così
        # tutti i chiamanti esistenti (`ExecutionLoop(conn, builder,
        # evaluator)`, in wiring.py e nei test) restano validi senza modifiche.
        self.staging_base_dir = staging_base_dir if staging_base_dir is not None else config.STAGING_BASE_DIR

    def run_task(self, task: sqlite3.Row) -> TaskOutcome:
        task_id = task["id"]
        goal_id = task["goal_id"]
        description = task["description"]

        # Il target REALE non viene mai scritto direttamente da questo ciclo:
        # ogni tentativo opera su una copia isolata (`staging`), creata da
        # `create_staging` come snapshot del target al momento del tentativo.
        # `self.builder.workspace` resta il riferimento al target reale
        # (immutato dal costruttore del Builder), usato qui SOLO per leggerne
        # la root — mai per scriverci.
        target_root = self.builder.workspace.root
        run_id = str(goal_id)

        goal_row = models.get_goal(self.conn, goal_id)
        literal_constraints = LiteralConstraints.from_json(
            goal_row["literal_constraints_json"] if goal_row is not None else None
        )
        parsed_command = (
            literal_constraints.parsed_test_command() if literal_constraints is not None else None
        )
        exact_test_env, exact_test_command = parsed_command if parsed_command is not None else (None, None)

        models.update_task_status(self.conn, task_id, "in_progress")
        log_action(
            self.conn,
            entity_type="task",
            entity_id=task_id,
            action="TASK_STARTED",
            actor="system",
            payload={"description": description},
        )

        previous_failure: str | None = None

        for attempt_number in range(1, config.MAX_ATTEMPTS + 1):
            attempt_id = models.create_attempt(self.conn, task_id, attempt_number, "BUILD")

            # SNAPSHOT + STAGING: ogni tentativo riceve una copia isolata e
            # fisicamente separata del target reale, PRIMA che il Builder
            # scriva un solo byte. Il target non viene toccato da nessuna
            # fase di questo tentativo (BUILD/TEST/VERIFY): solo l'Approval
            # Gate, dopo un'approvazione umana esplicita, può promuovere le
            # differenze registrate qui.
            staging = create_staging(self.staging_base_dir, run_id, attempt_id, target_root)
            staging_workspace = Workspace(staging.root)
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="STAGING_CREATED",
                actor="system",
                payload={
                    "attempt_number": attempt_number,
                    "staging_root": str(staging.root),
                    "target_snapshot_hash": staging.initial_snapshot_hash,
                },
            )

            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="BUILD_STARTED",
                actor="system",
                payload={"attempt_number": attempt_number, "previous_failure": previous_failure},
            )

            try:
                build_result = self.builder.build(
                    description, attempt_number, previous_failure, literal_constraints,
                    workspace=staging_workspace,
                )
            except LiteralConstraintViolationError as exc:
                # Fallimento in BUILD: lo staging di questo tentativo viene
                # scartato subito. Il target reale non è mai stato toccato,
                # quindi non c'è nulla da ripristinare lì — resta byte-identico
                # a prima di questo tentativo.
                discard_staging(staging.root)
                # La chiamata al provider È avvenuta con successo (altrimenti sarebbe
                # stata sollevata ProviderExecutionError sopra): va comunque registrata
                # in provider_calls. È l'ENFORCEMENT deterministico del motore, non il
                # provider, a bloccare qui — la proposta divergeva da un
                # literal_constraint e non era correggibile in modo sicuro.
                _persist_call_record(
                    self.conn,
                    goal_id=goal_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    record=self.builder.ai_provider.last_call_record,
                )
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action="LITERAL_CONSTRAINT_BLOCKED",
                    actor="system",
                    payload={"attempt_number": attempt_number, "reason": str(exc)},
                )
                models.update_attempt(
                    self.conn, attempt_id, phase="BLOCKED", status="failure", close=True
                )
                models.update_task_status(self.conn, task_id, "blocked")
                log_action(
                    self.conn,
                    entity_type="task",
                    entity_id=task_id,
                    action="TASK_BLOCKED",
                    actor="system",
                    payload={"reason": "literal_constraint_violation", "message": str(exc)},
                )
                # Fail-closed: nessuna scrittura è avvenuta in sandbox; niente da
                # correggere con un altro tentativo automatico, serve intervento umano.
                return TaskOutcome(task_id=task_id, status="blocked", attempts_used=attempt_number)
            except BuildIncompleteError as exc:
                # La chiamata al provider È avvenuta con successo: va comunque
                # registrata in provider_calls. Il blocco qui è del gate di
                # completezza del motore (non l'enforcement dei literal_constraints
                # sopra): la proposta non ha prodotto tutto ciò che serve per un
                # BUILD atomico (es. manca un file richiesto), quindi TEST non deve
                # nemmeno partire su uno stato a metà — nessun retry automatico,
                # nessuna scrittura in sandbox.
                discard_staging(staging.root)
                _persist_call_record(
                    self.conn,
                    goal_id=goal_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    record=self.builder.ai_provider.last_call_record,
                )
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action="BUILD_INCOMPLETE_BLOCKED",
                    actor="system",
                    payload={"attempt_number": attempt_number, "reason": str(exc)},
                )
                models.update_attempt(
                    self.conn, attempt_id, phase="BLOCKED", status="failure", close=True
                )
                models.update_task_status(self.conn, task_id, "blocked")
                log_action(
                    self.conn,
                    entity_type="task",
                    entity_id=task_id,
                    action="TASK_BLOCKED",
                    actor="system",
                    payload={"reason": "build_incomplete", "message": str(exc)},
                )
                return TaskOutcome(task_id=task_id, status="blocked", attempts_used=attempt_number)
            except ProviderExecutionError as exc:
                discard_staging(staging.root)
                _persist_call_record(
                    self.conn,
                    goal_id=goal_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    record=self.builder.ai_provider.last_call_record,
                )
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action="PROVIDER_CALL_BLOCKED",
                    actor="system",
                    payload={
                        "attempt_number": attempt_number,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "provider_name": self.builder.ai_provider.name,
                    },
                )
                models.update_attempt(
                    self.conn, attempt_id, phase="BLOCKED", status="failure", close=True
                )
                models.update_task_status(self.conn, task_id, "blocked")
                log_action(
                    self.conn,
                    entity_type="task",
                    entity_id=task_id,
                    action="TASK_BLOCKED",
                    actor="system",
                    payload={"reason": "provider_execution_error", "error_type": type(exc).__name__},
                )
                # Fail-closed: un errore del provider reale non consuma un tentativo
                # automatico di retry, blocca subito il task per intervento umano.
                return TaskOutcome(task_id=task_id, status="blocked", attempts_used=attempt_number)

            _persist_call_record(
                self.conn,
                goal_id=goal_id,
                task_id=task_id,
                attempt_id=attempt_id,
                record=self.builder.ai_provider.last_call_record,
            )
            models.update_attempt(
                self.conn,
                attempt_id,
                status="success",
                provider_name=build_result.proposal.provider_name,
                is_simulated=build_result.proposal.is_simulated,
                diff_summary=build_result.diff_text,
                notes=build_result.proposal.summary,
            )
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="BUILD_COMPLETED",
                actor="system",
                payload={
                    "provider_name": build_result.proposal.provider_name,
                    "is_simulated": build_result.proposal.is_simulated,
                    "summary": build_result.proposal.summary,
                    "files_changed": [fw.path for fw in build_result.file_writes],
                    "diff": build_result.diff_text,
                },
            )
            if literal_constraints is not None:
                # Audit trail dell'enforcement deterministico, anche quando non
                # ha dovuto correggere nulla (per trasparenza: si può sempre
                # verificare che l'assenza di correzioni non nasconda una
                # divergenza non rilevata).
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action="LITERAL_CONSTRAINTS_ENFORCED",
                    actor="system",
                    payload={
                        "corrected": build_result.enforcement.corrected,
                        "dropped_files": build_result.enforcement.dropped_files,
                        "notes": build_result.enforcement.notes,
                    },
                )

            models.update_attempt(self.conn, attempt_id, phase="TEST")
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="TEST_STARTED",
                actor="system",
                payload={},
            )
            # TEST gira SEMPRE dentro lo staging di questo tentativo, mai
            # contro il target reale: `cwd=staging.root` è ciò che rende
            # l'intero ciclo BUILD->TEST->VERIFY una transazione isolata.
            eval_result = self.evaluator.evaluate(
                cwd=staging.root, command=exact_test_command, env=exact_test_env
            )
            # Redazione dei segreti + troncamento PRIMA di qualunque
            # persistenza (DB o audit log): l'output reale di pytest non deve
            # mai portare con sé un valore segreto dell'ambiente host, anche
            # se quel valore fosse comparso per errore (es. in un traceback).
            safe_output, output_truncated = sanitize_test_output(eval_result.output)
            models.record_test_result(
                self.conn,
                attempt_id,
                test_name="pytest_run",
                passed=eval_result.passed,
                output=safe_output,
                duration_ms=eval_result.duration_ms,
            )
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="TEST_COMPLETED",
                actor="system",
                payload={
                    "passed": eval_result.passed,
                    "duration_ms": eval_result.duration_ms,
                    "output": safe_output,
                    "output_truncated": output_truncated,
                },
            )

            if not eval_result.passed:
                # TEST fallito: staging scartato, target reale mai toccato.
                discard_staging(staging.root)

            if eval_result.passed:
                literal_result = verify_literal_constraints(staging.root, literal_constraints)
                if literal_constraints is not None:
                    models.record_test_result(
                        self.conn,
                        attempt_id,
                        test_name="literal_constraints_check",
                        passed=literal_result.passed,
                        output="; ".join(literal_result.reasons) if literal_result.reasons else "ok",
                        duration_ms=0,
                    )
                    log_action(
                        self.conn,
                        entity_type="attempt",
                        entity_id=attempt_id,
                        action="LITERAL_VERIFICATION_COMPLETED",
                        actor="system",
                        payload={"passed": literal_result.passed, "reasons": literal_result.reasons},
                    )

                if not literal_result.passed:
                    # Trattata come un fallimento di verifica: stesso percorso di un
                    # fallimento pytest (FIX se restano tentativi, altrimenti blocco),
                    # perché la divergenza qui è stata rilevata SOLO in fase di
                    # verifica post-scrittura (es. vincolo parzialmente specificato,
                    # oppure file accessori ricomparsi dopo l'enforcement).
                    # Staging scartato: il target reale non è mai stato toccato.
                    discard_staging(staging.root)
                    models.update_attempt(self.conn, attempt_id, phase="FIX", status="failure", close=True)
                    previous_failure = (
                        "Verifica letterale deterministica fallita: " + "; ".join(literal_result.reasons)
                    )
                    log_action(
                        self.conn,
                        entity_type="attempt",
                        entity_id=attempt_id,
                        action="FIX_REQUIRED" if attempt_number < config.MAX_ATTEMPTS else "MAX_ATTEMPTS_REACHED",
                        actor="system",
                        payload={
                            "attempt_number": attempt_number,
                            "max_attempts": config.MAX_ATTEMPTS,
                            "reason": "literal_verification_failed",
                        },
                    )
                    continue

                models.update_attempt(self.conn, attempt_id, phase="VERIFY", status="success", close=True)
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action="VERIFY_PASSED",
                    actor="system",
                    payload={"attempt_number": attempt_number},
                )
                models.update_task_status(self.conn, task_id, "passed")

                # Manifest completo della candidate: diff dello staging rispetto
                # al target al momento della creazione, più tutto ciò che serve
                # per ricostruire il contesto della decisione senza tornare al
                # codice. Lo staging NON viene scartato qui: sopravvive finché
                # un umano non approva (promozione) o rifiuta (pulizia) la
                # candidate — è il riferimento immutabile che approve/reject
                # useranno.
                final_snapshot = compute_tree_snapshot(staging.root)
                diff = diff_snapshots(staging.initial_snapshot, final_snapshot, staging.root)
                provider_calls_for_task = models.list_provider_calls_for_task(self.conn, task_id)
                total_tokens = sum(
                    (json.loads(c["usage_json"]) or {}).get("total_tokens", 0)
                    for c in provider_calls_for_task
                    if c["usage_json"]
                )
                total_cost = sum(
                    c["estimated_cost_usd"] for c in provider_calls_for_task if c["estimated_cost_usd"] is not None
                )
                last_record = self.builder.ai_provider.last_call_record
                manifest = {
                    "run_id": run_id,
                    "attempt_id": attempt_id,
                    "task_id": task_id,
                    "goal_id": goal_id,
                    "provider_name": build_result.proposal.provider_name,
                    "model": last_record.model if last_record is not None else None,
                    "is_simulated": build_result.proposal.is_simulated,
                    "target_snapshot_hash": staging.initial_snapshot_hash,
                    "staging_reference": str(staging.root),
                    "target_root": str(staging.target_root),
                    "files": diff.to_dict(),
                    "test_result": {
                        "passed": eval_result.passed,
                        "duration_ms": eval_result.duration_ms,
                        "output": safe_output,
                        "output_truncated": output_truncated,
                    },
                    "verify_result": {"passed": literal_result.passed, "reasons": literal_result.reasons},
                    "literal_constraints_applied": literal_constraints.to_dict()
                    if literal_constraints is not None
                    else None,
                    "provider_call_ids": [c["id"] for c in provider_calls_for_task],
                    "tokens_total": total_tokens,
                    "estimated_cost_usd_total": total_cost,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }

                candidate_id = models.create_candidate(
                    self.conn,
                    goal_id,
                    task_id,
                    summary=build_result.proposal.summary,
                    provider_name=build_result.proposal.provider_name,
                    is_simulated=build_result.proposal.is_simulated,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    staging_root=str(staging.root),
                    target_snapshot_hash=staging.initial_snapshot_hash,
                    manifest_json=json.dumps(manifest, ensure_ascii=False),
                )
                log_action(
                    self.conn,
                    entity_type="candidate",
                    entity_id=candidate_id,
                    action="CANDIDATE_CREATED",
                    actor="system",
                    payload={
                        "task_id": task_id,
                        "goal_id": goal_id,
                        "status": "pending_review",
                        "requires_human_approval": True,
                        "provider_name": build_result.proposal.provider_name,
                        "is_simulated": build_result.proposal.is_simulated,
                        "staging_root": str(staging.root),
                        "target_snapshot_hash": staging.initial_snapshot_hash,
                        "files_created": diff.created,
                        "files_modified": diff.modified,
                        "files_deleted": diff.deleted,
                        "target_untouched": True,
                    },
                )
                models.associate_candidate_provider_calls(self.conn, task_id, candidate_id)
                return TaskOutcome(
                    task_id=task_id,
                    status="candidate_created",
                    attempts_used=attempt_number,
                    candidate_id=candidate_id,
                )

            # Test falliti: entra in FIX se restano tentativi, altrimenti si blocca
            # (lo staging è già stato scartato sopra: il target reale non è mai
            # stato toccato).
            models.update_attempt(
                self.conn, attempt_id, phase="FIX", status="failure", close=True
            )
            previous_failure = safe_output
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="FIX_REQUIRED" if attempt_number < config.MAX_ATTEMPTS else "MAX_ATTEMPTS_REACHED",
                actor="system",
                payload={"attempt_number": attempt_number, "max_attempts": config.MAX_ATTEMPTS},
            )

        models.update_task_status(self.conn, task_id, "blocked")
        log_action(
            self.conn,
            entity_type="task",
            entity_id=task_id,
            action="TASK_BLOCKED",
            actor="system",
            payload={"reason": "max_attempts_reached", "max_attempts": config.MAX_ATTEMPTS},
        )
        return TaskOutcome(task_id=task_id, status="blocked", attempts_used=config.MAX_ATTEMPTS)

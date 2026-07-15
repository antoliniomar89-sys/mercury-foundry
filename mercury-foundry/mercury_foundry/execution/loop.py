"""Execution Loop — state machine deterministica:

SPEC -> PLAN -> BUILD -> TEST -> FIX -> VERIFY -> CANDIDATE

Il modello AI (reale o FakeModel) propone SOLO il contenuto di piano/patch.
Le transizioni di stato, il conteggio dei tentativi (max 3) e il giudizio
pass/fail sono decisi qui da codice deterministico, mai dal modello.
"""

from __future__ import annotations

import shlex
import sqlite3
from dataclasses import dataclass

from mercury_foundry import config
from mercury_foundry.agents.builder import Builder
from mercury_foundry.agents.evaluator import Evaluator
from mercury_foundry.ai.errors import ProviderExecutionError
from mercury_foundry.audit.logger import log_action
from mercury_foundry.policy.errors import LiteralConstraintViolationError
from mercury_foundry.policy.literal_constraints import LiteralConstraints, verify_literal_constraints
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
    def __init__(self, conn: sqlite3.Connection, builder: Builder, evaluator: Evaluator):
        self.conn = conn
        self.builder = builder
        self.evaluator = evaluator

    def run_task(self, task: sqlite3.Row) -> TaskOutcome:
        task_id = task["id"]
        goal_id = task["goal_id"]
        description = task["description"]

        goal_row = models.get_goal(self.conn, goal_id)
        literal_constraints = LiteralConstraints.from_json(
            goal_row["literal_constraints_json"] if goal_row is not None else None
        )
        exact_test_command = (
            shlex.split(literal_constraints.exact_test_command)
            if literal_constraints is not None and literal_constraints.exact_test_command
            else None
        )

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
                    description, attempt_number, previous_failure, literal_constraints
                )
            except LiteralConstraintViolationError as exc:
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
            except ProviderExecutionError as exc:
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
            eval_result = self.evaluator.evaluate(command=exact_test_command)
            models.record_test_result(
                self.conn,
                attempt_id,
                test_name="pytest_run",
                passed=eval_result.passed,
                output=eval_result.output,
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
                    "output": eval_result.output,
                },
            )

            if eval_result.passed:
                literal_result = verify_literal_constraints(self.builder.workspace.root, literal_constraints)
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

                candidate_id = models.create_candidate(
                    self.conn,
                    goal_id,
                    task_id,
                    summary=build_result.proposal.summary,
                    provider_name=build_result.proposal.provider_name,
                    is_simulated=build_result.proposal.is_simulated,
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
                    },
                )
                models.attach_candidate_to_provider_calls(self.conn, task_id, candidate_id)
                return TaskOutcome(
                    task_id=task_id,
                    status="candidate_created",
                    attempts_used=attempt_number,
                    candidate_id=candidate_id,
                )

            # Test falliti: entra in FIX se restano tentativi, altrimenti si blocca.
            models.update_attempt(
                self.conn, attempt_id, phase="FIX", status="failure", close=True
            )
            previous_failure = eval_result.output
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

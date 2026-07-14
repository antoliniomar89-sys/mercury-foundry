"""Execution Loop — state machine deterministica:

SPEC -> PLAN -> BUILD -> TEST -> FIX -> VERIFY -> CANDIDATE

Il modello AI (reale o FakeModel) propone SOLO il contenuto di piano/patch.
Le transizioni di stato, il conteggio dei tentativi (max 3) e il giudizio
pass/fail sono decisi qui da codice deterministico, mai dal modello.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from mercury_foundry import config
from mercury_foundry.agents.builder import Builder
from mercury_foundry.agents.evaluator import Evaluator
from mercury_foundry.audit.logger import log_action
from mercury_foundry.state import models


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

            build_result = self.builder.build(description, attempt_number, previous_failure)
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

            models.update_attempt(self.conn, attempt_id, phase="TEST")
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="TEST_STARTED",
                actor="system",
                payload={},
            )
            eval_result = self.evaluator.evaluate()
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

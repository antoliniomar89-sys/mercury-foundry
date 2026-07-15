"""Orchestrator — intake obiettivo, scomposizione in task, assegnazione, stato."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from mercury_foundry.ai.errors import ProviderExecutionError
from mercury_foundry.ai.provider import AIProvider
from mercury_foundry.audit.logger import log_action
from mercury_foundry.execution.loop import ExecutionLoop, TaskOutcome
from mercury_foundry.orchestrator.decomposition import decompose_goal
from mercury_foundry.policy.literal_constraints import LiteralConstraints
from mercury_foundry.state import models


@dataclass
class GoalRun:
    goal_id: int
    task_outcomes: list[TaskOutcome]
    final_status: str


class Orchestrator:
    def __init__(self, conn: sqlite3.Connection, ai_provider: AIProvider, execution_loop: ExecutionLoop):
        self.conn = conn
        self.ai_provider = ai_provider
        self.execution_loop = execution_loop

    def submit_goal(self, description: str, literal_constraints: LiteralConstraints | None = None) -> int:
        goal_id = models.create_goal(
            self.conn,
            description,
            literal_constraints_json=literal_constraints.to_json() if literal_constraints is not None else None,
        )
        # Un goal sottomesso è, in questo sistema, un "run" del Foundry: PLAN
        # (qui) e BUILD (in ExecutionLoop, sullo stesso goal) condividono lo
        # stesso run_id, così ogni chiamata reale del provider durante
        # l'intero ciclo di vita del goal è riconducibile a un unico run in
        # `provider_calls`.
        run_id = str(goal_id)
        log_action(
            self.conn,
            entity_type="goal",
            entity_id=goal_id,
            action="GOAL_SUBMITTED",
            actor="human",
            payload={"description": description},
        )

        try:
            task_descriptions = decompose_goal(description, self.ai_provider)
        except ProviderExecutionError as exc:
            models.persist_provider_call_record(
                self.conn,
                run_id=run_id,
                goal_id=goal_id,
                record=self.ai_provider.last_call_record,
            )
            log_action(
                self.conn,
                entity_type="goal",
                entity_id=goal_id,
                action="PROVIDER_CALL_BLOCKED",
                actor="system",
                payload={
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "provider_name": self.ai_provider.name,
                    "phase": "PLAN",
                },
            )
            models.update_goal_status(self.conn, goal_id, "blocked")
            log_action(
                self.conn,
                entity_type="goal",
                entity_id=goal_id,
                action="GOAL_BLOCKED",
                actor="system",
                payload={"reason": "provider_execution_error_during_plan", "error_type": type(exc).__name__},
            )
            raise

        # Chiamata di pianificazione RIUSCITA: va persistita esattamente come
        # quella fallita sopra, altrimenti l'audit trail di provider_calls
        # sottostimerebbe la spesa reale ogni volta che il piano riesce al
        # primo colpo (il gap che questo fix corregge).
        models.persist_provider_call_record(
            self.conn,
            run_id=run_id,
            goal_id=goal_id,
            record=self.ai_provider.last_call_record,
        )

        for index, task_description in enumerate(task_descriptions):
            task_id = models.create_task(
                self.conn, goal_id, index, task_description, assigned_to="builder"
            )
            log_action(
                self.conn,
                entity_type="task",
                entity_id=task_id,
                action="TASK_CREATED",
                actor="system",
                payload={"goal_id": goal_id, "order_index": index, "description": task_description},
            )

        models.update_goal_status(self.conn, goal_id, "in_progress")
        return goal_id

    def run_goal(self, goal_id: int) -> GoalRun:
        tasks = models.get_tasks_for_goal(self.conn, goal_id)
        outcomes: list[TaskOutcome] = []

        for task in tasks:
            outcome = self.execution_loop.run_task(task)
            outcomes.append(outcome)
            if outcome.status == "blocked":
                models.update_goal_status(self.conn, goal_id, "blocked")
                log_action(
                    self.conn,
                    entity_type="goal",
                    entity_id=goal_id,
                    action="GOAL_BLOCKED",
                    actor="system",
                    payload={"blocked_task_id": task["id"]},
                )
                return GoalRun(goal_id=goal_id, task_outcomes=outcomes, final_status="blocked")

        models.update_goal_status(self.conn, goal_id, "awaiting_approval")
        log_action(
            self.conn,
            entity_type="goal",
            entity_id=goal_id,
            action="GOAL_AWAITING_APPROVAL",
            actor="system",
            payload={"candidate_ids": [o.candidate_id for o in outcomes]},
        )
        return GoalRun(goal_id=goal_id, task_outcomes=outcomes, final_status="awaiting_approval")

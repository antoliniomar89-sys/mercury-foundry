"""Approval Gate — le candidate diventano definitive solo con un'azione umana esplicita."""

from __future__ import annotations

import sqlite3

from mercury_foundry.audit.logger import log_action
from mercury_foundry.state import models


class CandidateNotFoundError(ValueError):
    pass


class InvalidCandidateStateError(ValueError):
    pass


def approve_candidate(conn: sqlite3.Connection, candidate_id: int, rationale: str | None = None) -> None:
    candidate = models.get_candidate(conn, candidate_id)
    if candidate is None:
        raise CandidateNotFoundError(f"Candidate {candidate_id} non trovata")
    if candidate["status"] != "pending_review":
        raise InvalidCandidateStateError(
            f"Candidate {candidate_id} è in stato '{candidate['status']}', non 'pending_review'"
        )

    models.update_candidate_status(conn, candidate_id, "approved")
    models.create_decision(
        conn,
        task_id=candidate["task_id"],
        candidate_id=candidate_id,
        decision_type="approve",
        actor="human",
        rationale=rationale,
    )
    log_action(
        conn,
        entity_type="candidate",
        entity_id=candidate_id,
        action="CANDIDATE_APPROVED",
        actor="human",
        payload={"rationale": rationale},
    )
    models.maybe_complete_goal(conn, candidate["goal_id"])


def reject_candidate(conn: sqlite3.Connection, candidate_id: int, rationale: str | None = None) -> None:
    candidate = models.get_candidate(conn, candidate_id)
    if candidate is None:
        raise CandidateNotFoundError(f"Candidate {candidate_id} non trovata")
    if candidate["status"] != "pending_review":
        raise InvalidCandidateStateError(
            f"Candidate {candidate_id} è in stato '{candidate['status']}', non 'pending_review'"
        )

    models.update_candidate_status(conn, candidate_id, "rejected")
    models.create_decision(
        conn,
        task_id=candidate["task_id"],
        candidate_id=candidate_id,
        decision_type="reject",
        actor="human",
        rationale=rationale,
    )
    log_action(
        conn,
        entity_type="candidate",
        entity_id=candidate_id,
        action="CANDIDATE_REJECTED",
        actor="human",
        payload={"rationale": rationale},
    )
    models.update_goal_status(conn, candidate["goal_id"], "blocked")

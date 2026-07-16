"""Registry CRUD per il layer Outcome Governance — MF-OUTCOME-001.

Nessun ORM: raw sqlite3, coerente con i pattern del repository.

Invarianti:
- Nessuna cancellazione distruttiva.
- Optimistic locking su `version` per gli aggiornamenti.
- Idempotency key UNIQUE dove applicabile.
- Tutti i timestamp UTC ISO 8601.
- Record decisionali immutabili dopo INSERT.
"""

from __future__ import annotations

import json
import sqlite3

from mercury_foundry.outcome.models import (
    ConsumptionIdempotencyReplay,
    EconomicOutcomePlan,
    OutcomeDecision,
    OutcomeMetricSnapshot,
    OutcomePlanNotFoundError,
    OutcomeVersionConflict,
    ReservationAlreadyConsumedError,
    ReservationAlreadyReleasedError,
    ReservationIdempotencyReplay,
    ReservationNotFoundError,
    ReservationStatus,
    ResourceConsumption,
    ResourceEnvelope,
    ResourceReservation,
    _new_id,
    _now_iso,
)


# ---------------------------------------------------------------------------
# EconomicOutcomePlan
# ---------------------------------------------------------------------------

def create_outcome_plan(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    correlation_id: str,
    objective: str,
    primary_metric: str,
    target_value: float,
    target_operator: str,
    maximum_cost_minor: int,
    maximum_duration_seconds: int,
    review_interval_seconds: int,
    kill_deadline: str,
    minimum_evidence_count: int,
    strategic_value_score: float,
    learning_value_score: float,
    reversibility: str,
    created_by: str,
    currency: str | None = None,
    expected_revenue_minor: int | None = None,
    expected_profit_minor: int | None = None,
    scale_threshold: float | None = None,
    stop_threshold: float | None = None,
    rollback_plan: str | None = None,
    priority_class: str = "normal",
    metadata: dict | None = None,
) -> EconomicOutcomePlan:
    """Crea un nuovo EconomicOutcomePlan nel DB. Ritorna il piano creato."""
    now = _now_iso()
    plan_id = _new_id()
    meta = metadata or {}

    conn.execute(
        """
        INSERT INTO economic_outcome_plans
               (outcome_plan_id, mission_id, correlation_id, objective,
                primary_metric, target_value, target_operator,
                maximum_cost_minor, maximum_duration_seconds,
                review_interval_seconds, kill_deadline, minimum_evidence_count,
                strategic_value_score, learning_value_score, reversibility,
                created_by, created_at, updated_at, version,
                currency, expected_revenue_minor, expected_profit_minor,
                scale_threshold, stop_threshold, rollback_plan,
                priority_class, status, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)
        """,
        (
            plan_id, mission_id, correlation_id, objective,
            primary_metric, target_value, target_operator,
            maximum_cost_minor, maximum_duration_seconds,
            review_interval_seconds, kill_deadline, minimum_evidence_count,
            strategic_value_score, learning_value_score, reversibility,
            created_by, now, now,
            currency, expected_revenue_minor, expected_profit_minor,
            scale_threshold, stop_threshold, rollback_plan,
            priority_class, "planned", json.dumps(meta),
        ),
    )
    conn.commit()

    return EconomicOutcomePlan(
        outcome_plan_id          = plan_id,
        mission_id               = mission_id,
        correlation_id           = correlation_id,
        objective                = objective,
        primary_metric           = primary_metric,
        target_value             = target_value,
        target_operator          = target_operator,
        maximum_cost_minor       = maximum_cost_minor,
        maximum_duration_seconds = maximum_duration_seconds,
        review_interval_seconds  = review_interval_seconds,
        kill_deadline            = kill_deadline,
        minimum_evidence_count   = minimum_evidence_count,
        strategic_value_score    = strategic_value_score,
        learning_value_score     = learning_value_score,
        reversibility            = reversibility,
        created_by               = created_by,
        created_at               = now,
        updated_at               = now,
        version                  = 1,
        currency                 = currency,
        expected_revenue_minor   = expected_revenue_minor,
        expected_profit_minor    = expected_profit_minor,
        scale_threshold          = scale_threshold,
        stop_threshold           = stop_threshold,
        rollback_plan            = rollback_plan,
        priority_class           = priority_class,
        metadata                 = meta,
    )


def get_outcome_plan(conn: sqlite3.Connection, outcome_plan_id: str) -> EconomicOutcomePlan:
    """Ritorna il piano per outcome_plan_id. Solleva OutcomePlanNotFoundError se assente."""
    row = conn.execute(
        "SELECT * FROM economic_outcome_plans WHERE outcome_plan_id = ?",
        (outcome_plan_id,),
    ).fetchone()
    if row is None:
        raise OutcomePlanNotFoundError(outcome_plan_id)
    return _row_to_plan(row)


def get_outcome_plan_for_mission(
    conn: sqlite3.Connection,
    mission_id: str,
) -> EconomicOutcomePlan | None:
    """Ritorna il piano attivo per mission_id (il più recente), o None se assente."""
    row = conn.execute(
        "SELECT * FROM economic_outcome_plans WHERE mission_id = ? ORDER BY created_at DESC LIMIT 1",
        (mission_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_plan(row)


def list_outcome_plans(
    conn: sqlite3.Connection,
    *,
    mission_id: str | None = None,
    status: str | None = None,
    priority_class: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[EconomicOutcomePlan]:
    query = "SELECT * FROM economic_outcome_plans WHERE 1=1"
    params: list = []
    if mission_id:
        query += " AND mission_id = ?"
        params.append(mission_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    if priority_class:
        query += " AND priority_class = ?"
        params.append(priority_class)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    return [_row_to_plan(r) for r in rows]


def _row_to_plan(row: sqlite3.Row) -> EconomicOutcomePlan:
    return EconomicOutcomePlan(
        outcome_plan_id          = row["outcome_plan_id"],
        mission_id               = row["mission_id"],
        correlation_id           = row["correlation_id"],
        objective                = row["objective"],
        primary_metric           = row["primary_metric"],
        target_value             = float(row["target_value"]),
        target_operator          = row["target_operator"],
        maximum_cost_minor       = int(row["maximum_cost_minor"]),
        maximum_duration_seconds = int(row["maximum_duration_seconds"]),
        review_interval_seconds  = int(row["review_interval_seconds"]),
        kill_deadline            = row["kill_deadline"],
        minimum_evidence_count   = int(row["minimum_evidence_count"]),
        strategic_value_score    = float(row["strategic_value_score"]),
        learning_value_score     = float(row["learning_value_score"]),
        reversibility            = row["reversibility"],
        created_by               = row["created_by"],
        created_at               = row["created_at"],
        updated_at               = row["updated_at"],
        version                  = int(row["version"]),
        currency                 = row["currency"],
        expected_revenue_minor   = row["expected_revenue_minor"],
        expected_profit_minor    = row["expected_profit_minor"],
        scale_threshold          = row["scale_threshold"],
        stop_threshold           = row["stop_threshold"],
        rollback_plan            = row["rollback_plan"],
        priority_class           = row["priority_class"],
        status                   = row["status"],
        metadata                 = json.loads(row["metadata_json"] or "{}"),
    )


# ---------------------------------------------------------------------------
# OutcomeMetricSnapshot
# ---------------------------------------------------------------------------

def create_metric_snapshot(
    conn: sqlite3.Connection,
    *,
    outcome_plan_id: str,
    mission_id: str,
    revenue_minor: int,
    cost_minor: int,
    profit_minor: int,
    elapsed_seconds: int,
    evidence_count: int,
    customer_count: int,
    knowledge_gain_score: float,
    risk_score: float,
    conversion_rate: float | None = None,
    delivery_success_rate: float | None = None,
    metadata: dict | None = None,
) -> OutcomeMetricSnapshot:
    now = _now_iso()
    snap_id = _new_id()
    meta = metadata or {}
    conn.execute(
        """
        INSERT INTO outcome_metric_snapshots
               (snapshot_id, outcome_plan_id, mission_id, measured_at,
                revenue_minor, cost_minor, profit_minor, elapsed_seconds,
                evidence_count, customer_count, knowledge_gain_score, risk_score,
                conversion_rate, delivery_success_rate, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            snap_id, outcome_plan_id, mission_id, now,
            revenue_minor, cost_minor, profit_minor, elapsed_seconds,
            evidence_count, customer_count, knowledge_gain_score, risk_score,
            conversion_rate, delivery_success_rate, json.dumps(meta),
        ),
    )
    conn.commit()
    return OutcomeMetricSnapshot(
        snapshot_id           = snap_id,
        outcome_plan_id       = outcome_plan_id,
        mission_id            = mission_id,
        measured_at           = now,
        revenue_minor         = revenue_minor,
        cost_minor            = cost_minor,
        profit_minor          = profit_minor,
        elapsed_seconds       = elapsed_seconds,
        evidence_count        = evidence_count,
        customer_count        = customer_count,
        knowledge_gain_score  = knowledge_gain_score,
        risk_score            = risk_score,
        conversion_rate       = conversion_rate,
        delivery_success_rate = delivery_success_rate,
        metadata              = meta,
    )


def get_latest_snapshot(
    conn: sqlite3.Connection,
    outcome_plan_id: str,
) -> OutcomeMetricSnapshot | None:
    row = conn.execute(
        """
        SELECT * FROM outcome_metric_snapshots
         WHERE outcome_plan_id = ?
         ORDER BY measured_at DESC
         LIMIT 1
        """,
        (outcome_plan_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_snapshot(row)


def list_snapshots(
    conn: sqlite3.Connection,
    outcome_plan_id: str,
    limit: int = 100,
) -> list[OutcomeMetricSnapshot]:
    rows = conn.execute(
        """
        SELECT * FROM outcome_metric_snapshots
         WHERE outcome_plan_id = ?
         ORDER BY measured_at DESC
         LIMIT ?
        """,
        (outcome_plan_id, limit),
    ).fetchall()
    return [_row_to_snapshot(r) for r in rows]


def _row_to_snapshot(row: sqlite3.Row) -> OutcomeMetricSnapshot:
    return OutcomeMetricSnapshot(
        snapshot_id           = row["snapshot_id"],
        outcome_plan_id       = row["outcome_plan_id"],
        mission_id            = row["mission_id"],
        measured_at           = row["measured_at"],
        revenue_minor         = int(row["revenue_minor"]),
        cost_minor            = int(row["cost_minor"]),
        profit_minor          = int(row["profit_minor"]),
        elapsed_seconds       = int(row["elapsed_seconds"]),
        evidence_count        = int(row["evidence_count"]),
        customer_count        = int(row["customer_count"]),
        knowledge_gain_score  = float(row["knowledge_gain_score"]),
        risk_score            = float(row["risk_score"]),
        conversion_rate       = row["conversion_rate"],
        delivery_success_rate = row["delivery_success_rate"],
        metadata              = json.loads(row["metadata_json"] or "{}"),
    )


# ---------------------------------------------------------------------------
# ResourceEnvelope
# ---------------------------------------------------------------------------

def create_resource_envelope(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    budget_minor: int,
    compute_units: int,
    llm_token_limit: int,
    external_service_limit_minor: int,
    human_minutes_limit: int,
    deadline: str,
    allocated_by: str,
    metadata: dict | None = None,
) -> ResourceEnvelope:
    """Crea un ResourceEnvelope per la Mission. Solleva se budget < 0."""
    from mercury_foundry.outcome.models import ResourceAllocationError
    if budget_minor < 0:
        raise ResourceAllocationError(
            f"budget_minor deve essere >= 0, ricevuto: {budget_minor}"
        )
    now = _now_iso()
    env_id = _new_id()
    meta = metadata or {}
    conn.execute(
        """
        INSERT INTO resource_envelopes
               (envelope_id, mission_id, budget_minor, compute_units,
                llm_token_limit, external_service_limit_minor,
                human_minutes_limit, deadline, allocated_at, allocated_by,
                version, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,1,?)
        """,
        (
            env_id, mission_id, budget_minor, compute_units,
            llm_token_limit, external_service_limit_minor,
            human_minutes_limit, deadline, now, allocated_by,
            json.dumps(meta),
        ),
    )
    conn.commit()
    return ResourceEnvelope(
        envelope_id                  = env_id,
        mission_id                   = mission_id,
        budget_minor                 = budget_minor,
        compute_units                = compute_units,
        llm_token_limit              = llm_token_limit,
        external_service_limit_minor = external_service_limit_minor,
        human_minutes_limit          = human_minutes_limit,
        deadline                     = deadline,
        allocated_at                 = now,
        allocated_by                 = allocated_by,
        version                      = 1,
        metadata                     = meta,
    )


def get_resource_envelope(
    conn: sqlite3.Connection,
    envelope_id: str,
) -> ResourceEnvelope | None:
    row = conn.execute(
        "SELECT * FROM resource_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone()
    if row is None:
        return None
    return ResourceEnvelope.from_dict({
        "envelope_id":                  row["envelope_id"],
        "mission_id":                   row["mission_id"],
        "budget_minor":                 row["budget_minor"],
        "compute_units":                row["compute_units"],
        "llm_token_limit":              row["llm_token_limit"],
        "external_service_limit_minor": row["external_service_limit_minor"],
        "human_minutes_limit":          row["human_minutes_limit"],
        "deadline":                     row["deadline"],
        "allocated_at":                 row["allocated_at"],
        "allocated_by":                 row["allocated_by"],
        "version":                      row["version"],
        "metadata":                     json.loads(row["metadata_json"] or "{}"),
    })


def get_resource_envelope_for_mission(
    conn: sqlite3.Connection,
    mission_id: str,
) -> ResourceEnvelope | None:
    row = conn.execute(
        "SELECT * FROM resource_envelopes WHERE mission_id = ? ORDER BY allocated_at DESC LIMIT 1",
        (mission_id,),
    ).fetchone()
    if row is None:
        return None
    return ResourceEnvelope.from_dict({
        "envelope_id":                  row["envelope_id"],
        "mission_id":                   row["mission_id"],
        "budget_minor":                 row["budget_minor"],
        "compute_units":                row["compute_units"],
        "llm_token_limit":              row["llm_token_limit"],
        "external_service_limit_minor": row["external_service_limit_minor"],
        "human_minutes_limit":          row["human_minutes_limit"],
        "deadline":                     row["deadline"],
        "allocated_at":                 row["allocated_at"],
        "allocated_by":                 row["allocated_by"],
        "version":                      row["version"],
        "metadata":                     json.loads(row["metadata_json"] or "{}"),
    })


# ---------------------------------------------------------------------------
# ResourceConsumption
# ---------------------------------------------------------------------------

def record_consumption(
    conn: sqlite3.Connection,
    *,
    envelope_id: str,
    mission_id: str,
    cost_minor: int,
    compute_units: int,
    llm_tokens: int,
    external_service_cost_minor: int,
    human_minutes: int,
    source_ref: str,
    idempotency_key: str,
    metadata: dict | None = None,
) -> ResourceConsumption:
    """Registra un consumo di risorse. Idempotente su idempotency_key."""
    existing = conn.execute(
        "SELECT consumption_id FROM resource_consumptions WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if existing is not None:
        raise ConsumptionIdempotencyReplay(existing["consumption_id"])

    now = _now_iso()
    cons_id = _new_id()
    meta = metadata or {}
    conn.execute(
        """
        INSERT INTO resource_consumptions
               (consumption_id, envelope_id, mission_id, cost_minor,
                compute_units, llm_tokens, external_service_cost_minor,
                human_minutes, recorded_at, source_ref, idempotency_key, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            cons_id, envelope_id, mission_id, cost_minor,
            compute_units, llm_tokens, external_service_cost_minor,
            human_minutes, now, source_ref, idempotency_key, json.dumps(meta),
        ),
    )
    conn.commit()
    return ResourceConsumption(
        consumption_id              = cons_id,
        envelope_id                 = envelope_id,
        mission_id                  = mission_id,
        cost_minor                  = cost_minor,
        compute_units               = compute_units,
        llm_tokens                  = llm_tokens,
        external_service_cost_minor = external_service_cost_minor,
        human_minutes               = human_minutes,
        recorded_at                 = now,
        source_ref                  = source_ref,
        idempotency_key             = idempotency_key,
        metadata                    = meta,
    )


def get_total_consumption(
    conn: sqlite3.Connection,
    envelope_id: str,
) -> dict[str, int]:
    """Ritorna il totale dei consumi per l'envelope."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(cost_minor), 0)                 AS total_cost_minor,
               COALESCE(SUM(compute_units), 0)              AS total_compute_units,
               COALESCE(SUM(llm_tokens), 0)                 AS total_llm_tokens,
               COALESCE(SUM(external_service_cost_minor), 0) AS total_external_cost_minor,
               COALESCE(SUM(human_minutes), 0)              AS total_human_minutes
          FROM resource_consumptions
         WHERE envelope_id = ?
        """,
        (envelope_id,),
    ).fetchone()
    return {
        "cost_minor":                 int(row["total_cost_minor"]),
        "compute_units":              int(row["total_compute_units"]),
        "llm_tokens":                 int(row["total_llm_tokens"]),
        "external_service_cost_minor": int(row["total_external_cost_minor"]),
        "human_minutes":              int(row["total_human_minutes"]),
    }


# ---------------------------------------------------------------------------
# OutcomeDecision (immutabile)
# ---------------------------------------------------------------------------

def persist_outcome_decision(
    conn: sqlite3.Connection,
    decision: OutcomeDecision,
) -> None:
    """Persiste una OutcomeDecision. Immutabile dopo INSERT."""
    conn.execute(
        """
        INSERT INTO outcome_decisions
               (decision_id, mission_id, outcome_plan_id, decision_type,
                score, confidence, reasons_json, blockers_json,
                required_actions_json, decided_at, correlation_id,
                authority_decision_id, constitutional_validation_id,
                metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            decision.decision_id,
            decision.mission_id,
            decision.outcome_plan_id,
            decision.decision_type,
            decision.score,
            decision.confidence,
            json.dumps(decision.reasons),
            json.dumps(decision.blockers),
            json.dumps(decision.required_actions),
            decision.decided_at,
            decision.correlation_id,
            decision.authority_decision_id,
            decision.constitutional_validation_id,
            json.dumps(decision.metadata),
        ),
    )
    conn.commit()


def get_latest_decision(
    conn: sqlite3.Connection,
    outcome_plan_id: str,
) -> OutcomeDecision | None:
    row = conn.execute(
        """
        SELECT * FROM outcome_decisions
         WHERE outcome_plan_id = ?
         ORDER BY decided_at DESC
         LIMIT 1
        """,
        (outcome_plan_id,),
    ).fetchone()
    if row is None:
        return None
    return OutcomeDecision(
        decision_id                  = row["decision_id"],
        mission_id                   = row["mission_id"],
        outcome_plan_id              = row["outcome_plan_id"],
        decision_type                = row["decision_type"],
        score                        = float(row["score"]),
        confidence                   = float(row["confidence"]),
        reasons                      = json.loads(row["reasons_json"] or "[]"),
        blockers                     = json.loads(row["blockers_json"] or "[]"),
        required_actions             = json.loads(row["required_actions_json"] or "[]"),
        decided_at                   = row["decided_at"],
        correlation_id               = row["correlation_id"],
        authority_decision_id        = row["authority_decision_id"],
        constitutional_validation_id = row["constitutional_validation_id"],
        metadata                     = json.loads(row["metadata_json"] or "{}"),
    )


# ---------------------------------------------------------------------------
# ResourceReservation CRUD (MF-ECO-001)
# ---------------------------------------------------------------------------

def _row_to_reservation(row: sqlite3.Row) -> ResourceReservation:
    return ResourceReservation(
        reservation_id  = row["reservation_id"],
        mission_id      = row["mission_id"],
        envelope_id     = row["envelope_id"],
        amount_minor    = int(row["amount_minor"]),
        currency        = row["currency"],
        status          = row["status"],
        idempotency_key = row["idempotency_key"],
        reason          = row["reason"],
        created_at      = row["created_at"],
        updated_at      = row["updated_at"],
        released_at     = row["released_at"],
        consumed_at     = row["consumed_at"],
    )


def create_reservation(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    envelope_id: str,
    amount_minor: int,
    idempotency_key: str,
    currency: str = "EUR",
    reason: str | None = None,
) -> ResourceReservation:
    """Crea una reservation di risorse. Idempotente su (envelope_id, idempotency_key).

    Raises:
        ReservationIdempotencyReplay: se esiste già una reservation con
            la stessa coppia (envelope_id, idempotency_key).
    """
    existing = conn.execute(
        "SELECT reservation_id FROM resource_reservations "
        "WHERE envelope_id = ? AND idempotency_key = ?",
        (envelope_id, idempotency_key),
    ).fetchone()
    if existing is not None:
        raise ReservationIdempotencyReplay(existing["reservation_id"])

    now = _now_iso()
    res_id = _new_id()
    conn.execute(
        """
        INSERT INTO resource_reservations
               (reservation_id, mission_id, envelope_id, amount_minor, currency,
                status, idempotency_key, reason, created_at, updated_at,
                released_at, consumed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL)
        """,
        (
            res_id, mission_id, envelope_id, amount_minor, currency,
            ReservationStatus.RESERVED.value, idempotency_key, reason,
            now, now,
        ),
    )
    conn.commit()
    return ResourceReservation(
        reservation_id  = res_id,
        mission_id      = mission_id,
        envelope_id     = envelope_id,
        amount_minor    = amount_minor,
        currency        = currency,
        status          = ReservationStatus.RESERVED.value,
        idempotency_key = idempotency_key,
        reason          = reason,
        created_at      = now,
        updated_at      = now,
    )


def get_reservation(
    conn: sqlite3.Connection,
    reservation_id: str,
) -> ResourceReservation | None:
    """Ritorna la reservation con il dato reservation_id, o None."""
    row = conn.execute(
        "SELECT * FROM resource_reservations WHERE reservation_id = ?",
        (reservation_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_reservation(row)


def get_reservation_by_idempotency_key(
    conn: sqlite3.Connection,
    envelope_id: str,
    idempotency_key: str,
) -> ResourceReservation | None:
    """Ritorna la reservation con la data (envelope_id, idempotency_key), o None."""
    row = conn.execute(
        "SELECT * FROM resource_reservations "
        "WHERE envelope_id = ? AND idempotency_key = ?",
        (envelope_id, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    return _row_to_reservation(row)


def list_active_reservations(
    conn: sqlite3.Connection,
    envelope_id: str,
) -> list[ResourceReservation]:
    """Ritorna tutte le reservations in status 'reserved' per l'envelope."""
    rows = conn.execute(
        "SELECT * FROM resource_reservations "
        "WHERE envelope_id = ? AND status = ? ORDER BY created_at ASC",
        (envelope_id, ReservationStatus.RESERVED.value),
    ).fetchall()
    return [_row_to_reservation(r) for r in rows]


def get_total_reserved(
    conn: sqlite3.Connection,
    envelope_id: str,
) -> int:
    """Somma degli amount_minor di tutte le reservations attive (status='reserved')."""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_minor), 0) AS total "
        "FROM resource_reservations "
        "WHERE envelope_id = ? AND status = ?",
        (envelope_id, ReservationStatus.RESERVED.value),
    ).fetchone()
    return int(row["total"])


def release_reservation(
    conn: sqlite3.Connection,
    reservation_id: str,
) -> ResourceReservation:
    """Transita la reservation a status 'released'. Idempotente se già released.

    Raises:
        ReservationNotFoundError: se non trovata.
        ReservationAlreadyConsumedError: se già consumata.
    """
    row = conn.execute(
        "SELECT * FROM resource_reservations WHERE reservation_id = ?",
        (reservation_id,),
    ).fetchone()
    if row is None:
        raise ReservationNotFoundError(reservation_id)

    status = row["status"]
    if status == ReservationStatus.RELEASED.value:
        return _row_to_reservation(row)  # idempotente
    if status == ReservationStatus.CONSUMED.value:
        raise ReservationAlreadyConsumedError(
            f"Reservation {reservation_id} già consumata — impossibile rilasciare"
        )

    now = _now_iso()
    conn.execute(
        "UPDATE resource_reservations "
        "SET status = ?, released_at = ?, updated_at = ? "
        "WHERE reservation_id = ?",
        (ReservationStatus.RELEASED.value, now, now, reservation_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM resource_reservations WHERE reservation_id = ?",
        (reservation_id,),
    ).fetchone()
    return _row_to_reservation(row)


def consume_reservation(
    conn: sqlite3.Connection,
    reservation_id: str,
) -> ResourceReservation:
    """Transita la reservation a status 'consumed'.

    Raises:
        ReservationNotFoundError: se non trovata.
        ReservationAlreadyConsumedError: se già consumata.
        ReservationAlreadyReleasedError: se già rilasciata.
    """
    row = conn.execute(
        "SELECT * FROM resource_reservations WHERE reservation_id = ?",
        (reservation_id,),
    ).fetchone()
    if row is None:
        raise ReservationNotFoundError(reservation_id)

    status = row["status"]
    if status == ReservationStatus.CONSUMED.value:
        raise ReservationAlreadyConsumedError(
            f"Reservation {reservation_id} già consumata"
        )
    if status == ReservationStatus.RELEASED.value:
        from mercury_foundry.outcome.models import ReservationAlreadyReleasedError as _RAR
        raise _RAR(f"Reservation {reservation_id} già rilasciata — impossibile consumare")

    now = _now_iso()
    conn.execute(
        "UPDATE resource_reservations "
        "SET status = ?, consumed_at = ?, updated_at = ? "
        "WHERE reservation_id = ?",
        (ReservationStatus.CONSUMED.value, now, now, reservation_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM resource_reservations WHERE reservation_id = ?",
        (reservation_id,),
    ).fetchone()
    return _row_to_reservation(row)


def get_total_consumption_for_mission(
    conn: sqlite3.Connection,
    mission_id: str,
) -> dict[str, int]:
    """Somma totale dei consumi per tutti gli envelope di una Mission (multi-envelope).

    Utile per calcolare il consumo aggregato quando una Mission ha più envelope.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(cost_minor), 0)                  AS total_cost_minor,
               COALESCE(SUM(compute_units), 0)               AS total_compute_units,
               COALESCE(SUM(llm_tokens), 0)                  AS total_llm_tokens,
               COALESCE(SUM(external_service_cost_minor), 0) AS total_external_cost_minor,
               COALESCE(SUM(human_minutes), 0)               AS total_human_minutes
          FROM resource_consumptions
         WHERE mission_id = ?
        """,
        (mission_id,),
    ).fetchone()
    return {
        "cost_minor":                  int(row["total_cost_minor"]),
        "compute_units":               int(row["total_compute_units"]),
        "llm_tokens":                  int(row["total_llm_tokens"]),
        "external_service_cost_minor": int(row["total_external_cost_minor"]),
        "human_minutes":               int(row["total_human_minutes"]),
    }

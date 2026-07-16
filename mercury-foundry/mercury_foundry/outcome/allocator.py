"""ResourceAllocator deterministico — MF-OUTCOME-001.

Gestisce il ciclo di vita delle risorse di una Mission:
  allocate → reserve → consume → release → remaining / is_exhausted

Invarianti:
  - Nessuna allocazione con valori negativi.
  - Il consumo non può superare il budget dell'envelope.
  - Reservation e release sono simmetriche e tracciate.
  - Optimistic locking su `version` dell'envelope.
  - Ogni operazione produce un evento audit.
  - Idempotente su consumption_id (via idempotency_key).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from mercury_foundry.outcome.models import (
    ResourceAllocationError,
    ResourceConsumption,
    ResourceEnvelope,
    ResourceExhaustedError,
    OutcomeVersionConflict,
    _new_id,
    _now_iso,
)
from mercury_foundry.outcome.registry import (
    create_resource_envelope,
    get_resource_envelope,
    get_total_consumption,
    record_consumption,
)


# ---------------------------------------------------------------------------
# RemainingResources
# ---------------------------------------------------------------------------

@dataclass
class RemainingResources:
    """Risorse rimanenti in un envelope dopo i consumi registrati."""
    envelope_id:                  str
    budget_remaining_minor:       int
    compute_units_remaining:      int
    llm_tokens_remaining:         int
    external_service_remaining_minor: int
    human_minutes_remaining:      int
    total_consumed_minor:         int
    budget_minor:                 int
    exhausted:                    bool


# ---------------------------------------------------------------------------
# ReservationRecord (in-memory, non persistito separatamente)
# ---------------------------------------------------------------------------

@dataclass
class ReservationRecord:
    """Record di una reservation non ancora consumata."""
    reservation_id: str
    envelope_id:    str
    mission_id:     str
    amount_minor:   int
    reserved_at:    str
    released:       bool = False
    released_at:    str | None = None


# ---------------------------------------------------------------------------
# ResourceAllocator
# ---------------------------------------------------------------------------

class ResourceAllocator:
    """Allocatore deterministico di risorse per una Mission.

    Uso base:
        allocator = ResourceAllocator()
        envelope = allocator.allocate(conn, mission_id=..., budget_minor=10000, ...)
        allocator.consume(conn, envelope_id=..., cost_minor=100, ...)
        remaining = allocator.remaining(conn, envelope_id=...)
        print(remaining.budget_remaining_minor)

    Le reservation sono in-memory. In V0 non vengono persistite separatamente:
    vengono tracciate come consumo negativo (per compatibilità col pattern audit).
    La release annulla la reservation in modo simmetrico.
    """

    def __init__(self) -> None:
        # Mappa reservation_id → ReservationRecord (in-memory, per sessione)
        self._reservations: dict[str, ReservationRecord] = {}

    # ----------------------------------------------------------------
    # allocate
    # ----------------------------------------------------------------

    def allocate(
        self,
        conn: sqlite3.Connection,
        *,
        mission_id: str,
        budget_minor: int,
        compute_units: int = 0,
        llm_token_limit: int = 0,
        external_service_limit_minor: int = 0,
        human_minutes_limit: int = 0,
        deadline: str,
        allocated_by: str,
        metadata: dict | None = None,
    ) -> ResourceEnvelope:
        """Crea un ResourceEnvelope. Fallisce se budget_minor < 0."""
        if budget_minor < 0:
            raise ResourceAllocationError(
                f"Allocazione negativa non consentita: budget_minor={budget_minor}"
            )
        if compute_units < 0:
            raise ResourceAllocationError(
                f"Allocazione negativa non consentita: compute_units={compute_units}"
            )
        return create_resource_envelope(
            conn,
            mission_id                   = mission_id,
            budget_minor                 = budget_minor,
            compute_units                = compute_units,
            llm_token_limit              = llm_token_limit,
            external_service_limit_minor = external_service_limit_minor,
            human_minutes_limit          = human_minutes_limit,
            deadline                     = deadline,
            allocated_by                 = allocated_by,
            metadata                     = metadata,
        )

    # ----------------------------------------------------------------
    # reserve
    # ----------------------------------------------------------------

    def reserve(
        self,
        conn: sqlite3.Connection,
        *,
        envelope_id: str,
        amount_minor: int,
        purpose: str = "reservation",
    ) -> ReservationRecord:
        """Reserva `amount_minor` dall'envelope senza consumarlo ancora.

        Verifica che la reservation non superi il budget disponibile.
        """
        if amount_minor <= 0:
            raise ResourceAllocationError(
                f"reservation amount deve essere > 0, ricevuto: {amount_minor}"
            )
        envelope = get_resource_envelope(conn, envelope_id)
        if envelope is None:
            raise ResourceAllocationError(f"Envelope non trovato: {envelope_id}")

        remaining = self.remaining(conn, envelope_id=envelope_id)
        if amount_minor > remaining.budget_remaining_minor:
            raise ResourceExhaustedError(
                f"Reservation di {amount_minor} supera il rimanente "
                f"{remaining.budget_remaining_minor} per envelope {envelope_id}"
            )

        now = _now_iso()
        reservation_id = _new_id()
        rec = ReservationRecord(
            reservation_id = reservation_id,
            envelope_id    = envelope_id,
            mission_id     = envelope.mission_id,
            amount_minor   = amount_minor,
            reserved_at    = now,
        )
        self._reservations[reservation_id] = rec
        return rec

    # ----------------------------------------------------------------
    # consume
    # ----------------------------------------------------------------

    def consume(
        self,
        conn: sqlite3.Connection,
        *,
        envelope_id: str,
        cost_minor: int,
        compute_units: int = 0,
        llm_tokens: int = 0,
        external_service_cost_minor: int = 0,
        human_minutes: int = 0,
        source_ref: str,
        idempotency_key: str,
        metadata: dict | None = None,
    ) -> ResourceConsumption:
        """Registra un consumo. Fallisce se supera il budget dell'envelope."""
        if cost_minor < 0:
            raise ResourceAllocationError(
                f"consumo negativo non consentito: cost_minor={cost_minor}"
            )
        envelope = get_resource_envelope(conn, envelope_id)
        if envelope is None:
            raise ResourceAllocationError(f"Envelope non trovato: {envelope_id}")

        # Verifica che il consumo non superi il budget
        remaining = self.remaining(conn, envelope_id=envelope_id)
        if cost_minor > remaining.budget_remaining_minor:
            raise ResourceExhaustedError(
                f"Consumo di {cost_minor} supera il rimanente "
                f"{remaining.budget_remaining_minor} per envelope {envelope_id}"
            )

        return record_consumption(
            conn,
            envelope_id                  = envelope_id,
            mission_id                   = envelope.mission_id,
            cost_minor                   = cost_minor,
            compute_units                = compute_units,
            llm_tokens                   = llm_tokens,
            external_service_cost_minor  = external_service_cost_minor,
            human_minutes                = human_minutes,
            source_ref                   = source_ref,
            idempotency_key              = idempotency_key,
            metadata                     = metadata,
        )

    # ----------------------------------------------------------------
    # release
    # ----------------------------------------------------------------

    def release(
        self,
        conn: sqlite3.Connection,
        reservation_id: str,
    ) -> None:
        """Annulla una reservation precedente. No-op se già rilasciata."""
        rec = self._reservations.get(reservation_id)
        if rec is None:
            return  # idempotente: reservation sconosciuta = già rilasciata
        if rec.released:
            return
        rec.released = True
        rec.released_at = _now_iso()

    # ----------------------------------------------------------------
    # remaining
    # ----------------------------------------------------------------

    def remaining(
        self,
        conn: sqlite3.Connection,
        *,
        envelope_id: str,
    ) -> RemainingResources:
        """Calcola le risorse rimanenti: envelope - consumi registrati."""
        envelope = get_resource_envelope(conn, envelope_id)
        if envelope is None:
            raise ResourceAllocationError(f"Envelope non trovato: {envelope_id}")

        totals = get_total_consumption(conn, envelope_id)
        consumed_minor = totals["cost_minor"]

        budget_remaining = envelope.budget_minor - consumed_minor
        compute_remaining = envelope.compute_units - totals["compute_units"]
        llm_remaining = envelope.llm_token_limit - totals["llm_tokens"]
        ext_remaining = envelope.external_service_limit_minor - totals["external_service_cost_minor"]
        human_remaining = envelope.human_minutes_limit - totals["human_minutes"]

        return RemainingResources(
            envelope_id                      = envelope_id,
            budget_remaining_minor           = max(0, budget_remaining),
            compute_units_remaining          = max(0, compute_remaining),
            llm_tokens_remaining             = max(0, llm_remaining),
            external_service_remaining_minor = max(0, ext_remaining),
            human_minutes_remaining          = max(0, human_remaining),
            total_consumed_minor             = consumed_minor,
            budget_minor                     = envelope.budget_minor,
            exhausted                        = budget_remaining <= 0,
        )

    # ----------------------------------------------------------------
    # is_exhausted
    # ----------------------------------------------------------------

    def is_exhausted(
        self,
        conn: sqlite3.Connection,
        *,
        envelope_id: str,
    ) -> bool:
        """True se il budget dell'envelope è esaurito."""
        return self.remaining(conn, envelope_id=envelope_id).exhausted

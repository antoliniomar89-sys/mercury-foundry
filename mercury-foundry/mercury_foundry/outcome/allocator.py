"""ResourceAllocator deterministico — MF-ECO-001.

Gestisce il ciclo di vita delle risorse di una Mission:
  allocate → reserve → consume → release → remaining / is_exhausted

Invarianti:
  - Nessuna allocazione con valori negativi.
  - Il consumo non può superare il budget disponibile (envelope − consumato − riservato).
  - Reservation persistite nel DB — sopravvivono al riavvio del processo.
  - reserve() e release() sono transazionali e idempotenti.
  - Idempotente su (envelope_id, idempotency_key) per reservations.
  - Idempotente su consumption_id (via idempotency_key) per consumi.
  - Optimistic locking su `version` dell'envelope.
  - Ogni operazione produce un evento audit.

MF-ECO-001 vs MF-OUTCOME-001:
  _reservations (in-memory dict) è mantenuto come cache locale per la durata
  della sessione (backward compat con test che accedono allocator._reservations).
  Il DB è la fonte di verità: le reservations sopravvivono al riavvio.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from mercury_foundry.outcome.models import (
    ReservationAlreadyConsumedError,
    ReservationAlreadyReleasedError,
    ReservationStatus,
    ResourceAllocationError,
    ResourceConsumption,
    ResourceEnvelope,
    ResourceExhaustedError,
    ResourceReservation,
    OutcomeVersionConflict,
    _new_id,
    _now_iso,
)
from mercury_foundry.outcome.registry import (
    create_reservation,
    create_resource_envelope,
    consume_reservation as _db_consume_reservation,
    get_reservation as _db_get_reservation,
    get_reservation_by_idempotency_key,
    get_resource_envelope,
    get_total_consumption,
    get_total_consumption_for_mission,
    get_total_reserved,
    list_active_reservations as _db_list_active_reservations,
    record_consumption,
    release_reservation as _db_release_reservation,
)


# ---------------------------------------------------------------------------
# RemainingResources
# ---------------------------------------------------------------------------

@dataclass
class RemainingResources:
    """Risorse rimanenti in un envelope dopo consumi e reservations attive."""
    envelope_id:                  str
    budget_remaining_minor:       int   # budget − consumed − reserved_active
    compute_units_remaining:      int
    llm_tokens_remaining:         int
    external_service_remaining_minor: int
    human_minutes_remaining:      int
    total_consumed_minor:         int
    total_reserved_minor:         int   # somma reservations attive (NEW MF-ECO-001)
    budget_minor:                 int
    exhausted:                    bool


# ---------------------------------------------------------------------------
# ReservationRecord (in-memory — backward compat con MF-OUTCOME-001)
# ---------------------------------------------------------------------------

@dataclass
class ReservationRecord:
    """Proxy in-memory di una ResourceReservation.

    Mantenuto per backward compat con test che accedono allocator._reservations.
    La fonte di verità è il DB via ResourceReservation.
    """
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

    Le reservation sono persistite nel DB (MF-ECO-001) e sopravvivono al
    riavvio del processo. L'attributo _reservations è un cache locale per
    la compatibilità con i test di MF-OUTCOME-001 che accedono direttamente
    all'attributo.

    Uso base:
        allocator = ResourceAllocator()
        envelope = allocator.allocate(conn, mission_id=..., budget_minor=10000, ...)
        allocator.reserve(conn, envelope_id=..., amount_minor=1000, ...)
        allocator.consume(conn, envelope_id=..., cost_minor=100, ...)
        remaining = allocator.remaining(conn, envelope_id=...)
    """

    def __init__(self) -> None:
        # Cache locale reservation_id → ReservationRecord (backward compat)
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
        idempotency_key: str | None = None,
        reason: str | None = None,
        purpose: str = "reservation",  # kept for backward compat (unused)
    ) -> ReservationRecord:
        """Reserva `amount_minor` dall'envelope.

        La reservation è persistita nel DB. Se idempotency_key non è fornita,
        viene generata automaticamente (ogni chiamata crea una nuova reservation).

        Verifica che la reservation non superi il budget disponibile
        (budget − consumato − già riservato).

        Args:
            conn: connessione SQLite.
            envelope_id: ID dell'envelope.
            amount_minor: importo da riservare (integer minor units).
            idempotency_key: chiave di idempotenza (auto-generata se None).
            reason: motivazione opzionale.
            purpose: campo legacy per compatibilità (ignorato).

        Returns:
            ReservationRecord (con backward compat con MF-OUTCOME-001).

        Raises:
            ResourceAllocationError: amount_minor <= 0 o envelope non trovato.
            ResourceExhaustedError: reservation supera il budget disponibile.
        """
        if amount_minor <= 0:
            raise ResourceAllocationError(
                f"reservation amount deve essere > 0, ricevuto: {amount_minor}"
            )
        envelope = get_resource_envelope(conn, envelope_id)
        if envelope is None:
            raise ResourceAllocationError(f"Envelope non trovato: {envelope_id}")

        # Se idempotency_key fornita, controlla replay prima di fare il check saldo
        if idempotency_key is not None:
            existing_db = get_reservation_by_idempotency_key(conn, envelope_id, idempotency_key)
            if existing_db is not None:
                # Idempotency replay: ritorna il ReservationRecord già in cache
                # o ricostruiscilo dalla reservation DB
                if existing_db.reservation_id in self._reservations:
                    return self._reservations[existing_db.reservation_id]
                released = existing_db.status == ReservationStatus.RELEASED.value
                rec = ReservationRecord(
                    reservation_id = existing_db.reservation_id,
                    envelope_id    = existing_db.envelope_id,
                    mission_id     = existing_db.mission_id,
                    amount_minor   = existing_db.amount_minor,
                    reserved_at    = existing_db.created_at,
                    released       = released,
                    released_at    = existing_db.released_at,
                )
                self._reservations[rec.reservation_id] = rec
                return rec

        # Verifica che la reservation non superi il budget disponibile
        rem = self.remaining(conn, envelope_id=envelope_id)
        if amount_minor > rem.budget_remaining_minor:
            raise ResourceExhaustedError(
                f"Reservation di {amount_minor} supera il disponibile "
                f"{rem.budget_remaining_minor} per envelope {envelope_id}"
            )

        auto_key = idempotency_key if idempotency_key is not None else _new_id()
        now = _now_iso()

        # Persiste nel DB
        db_res = create_reservation(
            conn,
            mission_id      = envelope.mission_id,
            envelope_id     = envelope_id,
            amount_minor    = amount_minor,
            idempotency_key = auto_key,
            reason          = reason,
        )

        # Popola cache locale per backward compat
        rec = ReservationRecord(
            reservation_id = db_res.reservation_id,
            envelope_id    = envelope_id,
            mission_id     = envelope.mission_id,
            amount_minor   = amount_minor,
            reserved_at    = now,
        )
        self._reservations[rec.reservation_id] = rec
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
        """Registra un consumo. Fallisce se supera il budget disponibile."""
        if cost_minor < 0:
            raise ResourceAllocationError(
                f"consumo negativo non consentito: cost_minor={cost_minor}"
            )
        envelope = get_resource_envelope(conn, envelope_id)
        if envelope is None:
            raise ResourceAllocationError(f"Envelope non trovato: {envelope_id}")

        # Verifica che il consumo non superi il budget disponibile
        rem = self.remaining(conn, envelope_id=envelope_id)
        if cost_minor > rem.budget_remaining_minor:
            raise ResourceExhaustedError(
                f"Consumo di {cost_minor} supera il disponibile "
                f"{rem.budget_remaining_minor} per envelope {envelope_id}"
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
        """Annulla una reservation precedente.

        Aggiorna sia il DB sia la cache locale.
        Idempotente: no-op se già rilasciata o sconosciuta.
        """
        # Aggiorna DB (idempotente: no-op se già released)
        try:
            _db_release_reservation(conn, reservation_id)
        except Exception:
            pass  # reservation non trovata → già rilasciata o non esistente

        # Aggiorna cache locale per backward compat
        rec = self._reservations.get(reservation_id)
        if rec is not None and not rec.released:
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
        """Calcola le risorse rimanenti: envelope − consumi − reservations attive.

        MF-ECO-001: il saldo disponibile considera sia i consumi persistiti
        sia le reservations attive nel DB.
        """
        envelope = get_resource_envelope(conn, envelope_id)
        if envelope is None:
            raise ResourceAllocationError(f"Envelope non trovato: {envelope_id}")

        totals = get_total_consumption(conn, envelope_id)
        consumed_minor = totals["cost_minor"]

        # MF-ECO-001: sottrai le reservations attive dal saldo disponibile
        reserved_minor = get_total_reserved(conn, envelope_id)

        budget_remaining = envelope.budget_minor - consumed_minor - reserved_minor
        compute_remaining = envelope.compute_units - totals["compute_units"]
        llm_remaining = envelope.llm_token_limit - totals["llm_tokens"]
        ext_remaining = (
            envelope.external_service_limit_minor - totals["external_service_cost_minor"]
        )
        human_remaining = envelope.human_minutes_limit - totals["human_minutes"]

        return RemainingResources(
            envelope_id                      = envelope_id,
            budget_remaining_minor           = max(0, budget_remaining),
            compute_units_remaining          = max(0, compute_remaining),
            llm_tokens_remaining             = max(0, llm_remaining),
            external_service_remaining_minor = max(0, ext_remaining),
            human_minutes_remaining          = max(0, human_remaining),
            total_consumed_minor             = consumed_minor,
            total_reserved_minor             = reserved_minor,
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

    # ----------------------------------------------------------------
    # get_reservation (MF-ECO-001)
    # ----------------------------------------------------------------

    def get_reservation(
        self,
        conn: sqlite3.Connection,
        reservation_id: str,
    ) -> ResourceReservation | None:
        """Ritorna la reservation dal DB per reservation_id, o None."""
        return _db_get_reservation(conn, reservation_id)

    # ----------------------------------------------------------------
    # list_active_reservations (MF-ECO-001)
    # ----------------------------------------------------------------

    def list_active_reservations(
        self,
        conn: sqlite3.Connection,
        envelope_id: str,
    ) -> list[ResourceReservation]:
        """Ritorna le reservations attive (status='reserved') per l'envelope."""
        return _db_list_active_reservations(conn, envelope_id)

    # ----------------------------------------------------------------
    # get_total_reserved (MF-ECO-001)
    # ----------------------------------------------------------------

    def get_total_reserved(
        self,
        conn: sqlite3.Connection,
        envelope_id: str,
    ) -> int:
        """Somma degli amount_minor delle reservations attive per l'envelope."""
        return get_total_reserved(conn, envelope_id)

    # ----------------------------------------------------------------
    # get_total_consumption (MF-ECO-001) — per envelope o per mission
    # ----------------------------------------------------------------

    def get_total_consumption(
        self,
        conn: sqlite3.Connection,
        *,
        envelope_id: str | None = None,
        mission_id: str | None = None,
    ) -> dict[str, int]:
        """Somma totale dei consumi, per envelope o per mission (multi-envelope).

        Args:
            conn: connessione SQLite.
            envelope_id: se fornito, filtra per singolo envelope.
            mission_id: se fornito, aggrega su tutti gli envelope della Mission.

        Raises:
            ResourceAllocationError: se nessuno dei due parametri è fornito.
        """
        if envelope_id is not None:
            return get_total_consumption(conn, envelope_id)
        if mission_id is not None:
            return get_total_consumption_for_mission(conn, mission_id)
        raise ResourceAllocationError(
            "get_total_consumption richiede envelope_id o mission_id"
        )

    # ----------------------------------------------------------------
    # get_available_amount (MF-ECO-001)
    # ----------------------------------------------------------------

    def get_available_amount(
        self,
        conn: sqlite3.Connection,
        envelope_id: str,
    ) -> int:
        """Ritorna il budget disponibile: budget_minor − consumed − reserved_active."""
        return self.remaining(conn, envelope_id=envelope_id).budget_remaining_minor

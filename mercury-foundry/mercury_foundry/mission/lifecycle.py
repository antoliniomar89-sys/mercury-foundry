"""State machine esplicita per il ciclo di vita delle Mission — MF-MISSION-001.

Le transizioni sono deterministiche, validate, idempotenti e immutabili.
Nessun agente può mutare `status` direttamente: ogni cambiamento passa per
`transition_mission()`, che produce un MissionTransitionRecord e un evento audit.

Struttura:
  - ALLOWED_TRANSITIONS: grafo esplicito delle transizioni consentite
  - can_transition(): verifica senza effetti collaterali
  - transition_mission(): applica la transizione, crea il record, emette eventi
  - transizioni speciali (activate, complete, promote, archive) che aggiornano
    i timestamp dedicati (accepted_at, activated_at, ecc.)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from mercury_foundry.mission.models import (
    MissionStatus,
    MissionTransitionError,
    MissionTransitionRecord,
    new_transition_id,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Grafo delle transizioni
# ---------------------------------------------------------------------------

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    MissionStatus.DRAFT.value: frozenset({
        MissionStatus.SUBMITTED.value,
    }),
    MissionStatus.SUBMITTED.value: frozenset({
        MissionStatus.UNDER_REVIEW.value,
    }),
    MissionStatus.UNDER_REVIEW.value: frozenset({
        MissionStatus.ACCEPTED.value,
        MissionStatus.REJECTED.value,
    }),
    MissionStatus.ACCEPTED.value: frozenset({
        MissionStatus.READY.value,
    }),
    MissionStatus.READY.value: frozenset({
        MissionStatus.ACTIVE.value,
    }),
    MissionStatus.ACTIVE.value: frozenset({
        MissionStatus.PAUSED.value,
        MissionStatus.BLOCKED.value,
        MissionStatus.COMPLETED.value,
        MissionStatus.FAILED.value,
        MissionStatus.TERMINATED.value,
    }),
    MissionStatus.PAUSED.value: frozenset({
        MissionStatus.ACTIVE.value,
        MissionStatus.TERMINATED.value,
    }),
    MissionStatus.BLOCKED.value: frozenset({
        MissionStatus.ACTIVE.value,
        MissionStatus.TERMINATED.value,
    }),
    MissionStatus.COMPLETED.value: frozenset({
        MissionStatus.ARCHIVED.value,
        MissionStatus.PROMOTED_TO_BUSINESS_CELL.value,
    }),
    MissionStatus.FAILED.value: frozenset({
        MissionStatus.ARCHIVED.value,
    }),
    MissionStatus.TERMINATED.value: frozenset({
        MissionStatus.ARCHIVED.value,
    }),
    MissionStatus.PROMOTED_TO_BUSINESS_CELL.value: frozenset({
        MissionStatus.ARCHIVED.value,
    }),
    # Stati terminali: nessuna transizione uscente
    MissionStatus.REJECTED.value: frozenset(),
    MissionStatus.ARCHIVED.value: frozenset(),
}

# Stati terminali (no transizioni uscenti)
TERMINAL_STATUSES: frozenset[str] = frozenset({
    MissionStatus.REJECTED.value,
    MissionStatus.ARCHIVED.value,
})

# Transizioni che aggiornano timestamp specializzati
_TIMESTAMP_FIELDS: dict[str, str] = {
    MissionStatus.ACCEPTED.value:                  "accepted_at",
    MissionStatus.ACTIVE.value:                    "activated_at",
    MissionStatus.COMPLETED.value:                 "completed_at",
    MissionStatus.TERMINATED.value:                "terminated_at",
    MissionStatus.FAILED.value:                    "terminated_at",  # condivide il campo
}

# Transizioni che richiedono authority esplicita (non solo proposal)
AUTHORITY_REQUIRED_TRANSITIONS: frozenset[str] = frozenset({
    MissionStatus.ACCEPTED.value,
    MissionStatus.ACTIVE.value,
    MissionStatus.TERMINATED.value,
})


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------

def can_transition(from_status: str, to_status: str) -> bool:
    """Verifica se la transizione è consentita senza effetti collaterali."""
    allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
    return to_status in allowed


def validate_transition(from_status: str, to_status: str) -> None:
    """Solleva MissionTransitionError se la transizione non è consentita."""
    if not can_transition(from_status, to_status):
        allowed = sorted(ALLOWED_TRANSITIONS.get(from_status, frozenset()))
        raise MissionTransitionError(
            f"Transizione da {from_status!r} a {to_status!r} non consentita. "
            f"Transizioni valide da {from_status!r}: {allowed}"
        )


def apply_transition(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    current_status: str,
    current_version: int,
    to_status: str,
    requested_by: str,
    reason: str,
    correlation_id: str,
    evidence_refs: list[str] | None = None,
    authorized_by: str | None = None,
    authority_decision_id: str | None = None,
    constitutional_validation_id: str | None = None,
    metadata: dict | None = None,
) -> MissionTransitionRecord:
    """Applica una transizione di stato alla Mission nel DB.

    Produce:
    - UPDATE atomico su `missions` (status + version + timestamp specializzato)
    - INSERT in `mission_transitions` (record immutabile)

    Utilizza optimistic locking: fallisce se `version` non corrisponde.
    Non produce eventi audit (responsabilità di `events.py`/chiamante).

    Solleva:
    - `MissionTransitionError` se la transizione non è nel grafo
    - `MissionVersionConflict` se la versione non corrisponde
    - `MissionNotFoundError` se mission_id non esiste
    """
    from mercury_foundry.mission.models import MissionNotFoundError, MissionVersionConflict

    validate_transition(current_status, to_status)

    now = _now_iso()
    transition_id = new_transition_id()
    evidence = evidence_refs or []

    # Calcola il timestamp specializzato (se necessario)
    ts_field = _TIMESTAMP_FIELDS.get(to_status)

    # Aggiorna la Mission con optimistic locking
    set_clauses = [
        "status = ?",
        "version = version + 1",
        "updated_at = ?",
    ]
    params: list = [to_status, now]

    if ts_field:
        set_clauses.append(f"{ts_field} = ?")
        params.append(now)

    params.extend([mission_id, current_version])

    sql = f"""
        UPDATE missions
        SET {', '.join(set_clauses)}
        WHERE mission_id = ? AND version = ?
    """
    cur = conn.execute(sql, params)

    if cur.rowcount == 0:
        # Zero righe aggiornate: verifica se la missione esiste
        row = conn.execute(
            "SELECT mission_id, version FROM missions WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()
        if row is None:
            raise MissionNotFoundError(f"Mission {mission_id!r} non trovata")
        raise MissionVersionConflict(
            f"Mission {mission_id!r}: versione attesa {current_version}, "
            f"trovata {row['version']}. Ricaricare e riprovare."
        )

    import json
    # Crea il transition record (immutabile)
    conn.execute(
        """
        INSERT INTO mission_transitions (
            transition_id, mission_id, from_status, to_status,
            requested_by, requested_at, authorized_by, reason,
            evidence_refs_json, authority_decision_id,
            constitutional_validation_id, correlation_id, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transition_id,
            mission_id,
            current_status,
            to_status,
            requested_by,
            now,
            authorized_by,
            reason,
            json.dumps(evidence, ensure_ascii=False),
            authority_decision_id,
            constitutional_validation_id,
            correlation_id,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )

    conn.commit()

    return MissionTransitionRecord(
        transition_id=transition_id,
        mission_id=mission_id,
        from_status=MissionStatus(current_status),
        to_status=MissionStatus(to_status),
        requested_by=requested_by,
        requested_at=now,
        reason=reason,
        correlation_id=correlation_id,
        evidence_refs=evidence,
        authorized_by=authorized_by,
        authority_decision_id=authority_decision_id,
        constitutional_validation_id=constitutional_validation_id,
        metadata=metadata or {},
    )


def list_transitions(
    conn: sqlite3.Connection,
    mission_id: str,
) -> list[MissionTransitionRecord]:
    """Carica tutti i transition record di una Mission, in ordine cronologico."""
    import json
    rows = conn.execute(
        """
        SELECT * FROM mission_transitions
        WHERE mission_id = ?
        ORDER BY rowid ASC
        """,
        (mission_id,),
    ).fetchall()
    records = []
    for row in rows:
        records.append(
            MissionTransitionRecord(
                transition_id=row["transition_id"],
                mission_id=row["mission_id"],
                from_status=MissionStatus(row["from_status"]),
                to_status=MissionStatus(row["to_status"]),
                requested_by=row["requested_by"],
                requested_at=row["requested_at"],
                reason=row["reason"],
                correlation_id=row["correlation_id"],
                evidence_refs=json.loads(row["evidence_refs_json"] or "[]"),
                authorized_by=row["authorized_by"],
                authority_decision_id=row["authority_decision_id"],
                constitutional_validation_id=row["constitutional_validation_id"],
                metadata=json.loads(row["metadata_json"] or "{}"),
            )
        )
    return records


def get_promotion_proposal_event(mission_id: str) -> dict:
    """Produce un evento preparatorio per PROMOTED_TO_BUSINESS_CELL.

    Non crea nessuna Business Cell. È un intent record che documenta
    la proposta senza eseguirla. Il chiamante è responsabile di scriverlo
    nell'audit log tramite emit_mission_event().
    """
    return {
        "event": "mission.business_cell_promotion.proposed",
        "mission_id": mission_id,
        "note": (
            "PROPOSED ONLY — Nessuna Business Cell è stata creata. "
            "Questa proposta richiede approvazione umana fuori banda. "
            "V0: MISSION_PROMOTE_TO_BUSINESS_CELL è forbidden per MISSION_CONTROL."
        ),
    }

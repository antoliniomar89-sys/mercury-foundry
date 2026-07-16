"""State machine del Genesis lifecycle — MF-REPL-001.

Regole:
- ALLOWED_TRANSITIONS è il grafo esplicito: source of truth.
- V0_BLOCKED: ready_for_provisioning → provisioning e provisioning → activated
  non sono raggiungibili automaticamente. Sono presenti nel grafo ma bloccate
  da feature flag (REPLICATION_ACTIVATION_ENABLED, REPLICATION_PROVISIONING_ENABLED)
  e dal mandato GENESIS_ACTIVATE=forbidden in REPLICATION_GOVERNANCE.
- Ogni transizione produce un DedicatedMercuryGenesisTransitionRecord immutabile.
- Optimistic locking su `version`.
"""

from __future__ import annotations

import json
import sqlite3

from mercury_foundry.replication.models import (
    DedicatedMercuryGenesisTransitionRecord,
    GenesisRequestNotFoundError,
    GenesisStatus,
    GenesisTransitionError,
    GenesisVersionConflict,
    ActivationBlockedError,
    _new_id,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Grafo delle transizioni
# ---------------------------------------------------------------------------

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    GenesisStatus.DRAFT.value: frozenset({
        GenesisStatus.PROPOSED.value,
    }),
    GenesisStatus.PROPOSED.value: frozenset({
        GenesisStatus.UNDER_REVIEW.value,
    }),
    GenesisStatus.UNDER_REVIEW.value: frozenset({
        GenesisStatus.APPROVED.value,
        GenesisStatus.REJECTED.value,
    }),
    GenesisStatus.APPROVED.value: frozenset({
        GenesisStatus.PACKAGING.value,
    }),
    GenesisStatus.PACKAGING.value: frozenset({
        GenesisStatus.READY_FOR_PROVISIONING.value,
        GenesisStatus.FAILED.value,
    }),
    GenesisStatus.READY_FOR_PROVISIONING.value: frozenset({
        GenesisStatus.ABORTED.value,
        # V0: provisioning è nel grafo ma bloccato da feature flag
        GenesisStatus.PROVISIONING.value,
    }),
    # V0: questi stati esistono ma non raggiungibili automaticamente
    GenesisStatus.PROVISIONING.value: frozenset({
        GenesisStatus.ACTIVATED.value,
    }),
    # Terminali
    GenesisStatus.ACTIVATED.value:  frozenset(),
    GenesisStatus.REJECTED.value:   frozenset({GenesisStatus.ARCHIVED.value}),
    GenesisStatus.FAILED.value:     frozenset({GenesisStatus.ARCHIVED.value}),
    GenesisStatus.ABORTED.value:    frozenset({GenesisStatus.ARCHIVED.value}),
    GenesisStatus.SUSPENDED.value:  frozenset({GenesisStatus.ARCHIVED.value}),
    GenesisStatus.ARCHIVED.value:   frozenset(),
}

# Transizioni bloccate in V0 da feature flag
V0_BLOCKED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    (GenesisStatus.READY_FOR_PROVISIONING.value, GenesisStatus.PROVISIONING.value),
    (GenesisStatus.PROVISIONING.value, GenesisStatus.ACTIVATED.value),
})

TERMINAL_STATUSES: frozenset[str] = frozenset({
    GenesisStatus.ACTIVATED.value,
    GenesisStatus.ARCHIVED.value,
})


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())


def validate_transition(
    from_status: str,
    to_status: str,
    *,
    check_v0_block: bool = True,
) -> None:
    """Solleva GenesisTransitionError o ActivationBlockedError."""
    if not can_transition(from_status, to_status):
        allowed = sorted(ALLOWED_TRANSITIONS.get(from_status, frozenset()))
        raise GenesisTransitionError(
            f"Transizione da {from_status!r} a {to_status!r} non consentita. "
            f"Valide da {from_status!r}: {allowed}"
        )
    if check_v0_block and (from_status, to_status) in V0_BLOCKED_TRANSITIONS:
        raise ActivationBlockedError(
            f"Transizione {from_status!r} → {to_status!r} bloccata in V0. "
            "L'activation è forbidden (REPLICATION_ACTIVATION_ENABLED=False, "
            "GENESIS_ACTIVATE mandate=forbidden)."
        )


def apply_genesis_transition(
    conn: sqlite3.Connection,
    *,
    genesis_request_id: str,
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
) -> DedicatedMercuryGenesisTransitionRecord:
    """Applica una transizione al genesis request nel DB.

    Produce:
    - UPDATE atomico su dedicated_mercury_genesis_requests
    - INSERT su dedicated_mercury_genesis_transitions (immutabile)

    Solleva:
    - GenesisTransitionError se non nel grafo
    - ActivationBlockedError se transizione V0-bloccata
    - GenesisVersionConflict se version non corrisponde
    - GenesisRequestNotFoundError se non esiste
    """
    validate_transition(current_status, to_status)

    now = _now_iso()
    transition_id = _new_id()
    evidence = evidence_refs or []

    cur = conn.execute(
        """
        UPDATE dedicated_mercury_genesis_requests
        SET status = ?, version = version + 1, updated_at = ?
        WHERE genesis_request_id = ? AND version = ?
        """,
        (to_status, now, genesis_request_id, current_version),
    )

    if cur.rowcount == 0:
        row = conn.execute(
            "SELECT genesis_request_id, version FROM dedicated_mercury_genesis_requests "
            "WHERE genesis_request_id = ?",
            (genesis_request_id,),
        ).fetchone()
        if row is None:
            raise GenesisRequestNotFoundError(
                f"Genesis request {genesis_request_id!r} non trovata"
            )
        raise GenesisVersionConflict(
            f"Genesis request {genesis_request_id!r}: versione attesa {current_version}, "
            f"trovata {row['version']}."
        )

    conn.execute(
        """
        INSERT INTO dedicated_mercury_genesis_transitions (
            transition_id, genesis_request_id, from_status, to_status,
            requested_by, requested_at, authorized_by, reason,
            evidence_refs_json, authority_decision_id,
            constitutional_validation_id, correlation_id, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transition_id, genesis_request_id, current_status, to_status,
            requested_by, now, authorized_by, reason,
            json.dumps(evidence, ensure_ascii=False),
            authority_decision_id, constitutional_validation_id,
            correlation_id, json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    conn.commit()

    return DedicatedMercuryGenesisTransitionRecord(
        transition_id=transition_id,
        genesis_request_id=genesis_request_id,
        from_status=GenesisStatus(current_status),
        to_status=GenesisStatus(to_status),
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


def list_genesis_transitions(
    conn: sqlite3.Connection,
    genesis_request_id: str,
) -> list[DedicatedMercuryGenesisTransitionRecord]:
    rows = conn.execute(
        """
        SELECT * FROM dedicated_mercury_genesis_transitions
        WHERE genesis_request_id = ? ORDER BY rowid ASC
        """,
        (genesis_request_id,),
    ).fetchall()
    return [
        DedicatedMercuryGenesisTransitionRecord(
            transition_id=r["transition_id"],
            genesis_request_id=r["genesis_request_id"],
            from_status=GenesisStatus(r["from_status"]),
            to_status=GenesisStatus(r["to_status"]),
            requested_by=r["requested_by"],
            requested_at=r["requested_at"],
            reason=r["reason"],
            correlation_id=r["correlation_id"],
            evidence_refs=json.loads(r["evidence_refs_json"] or "[]"),
            authorized_by=r["authorized_by"],
            authority_decision_id=r["authority_decision_id"],
            constitutional_validation_id=r["constitutional_validation_id"],
            metadata=json.loads(r["metadata_json"] or "{}"),
        )
        for r in rows
    ]

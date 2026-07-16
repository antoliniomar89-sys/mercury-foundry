"""Seeding di REPLICATION_GOVERNANCE — MF-REPL-001.

`seed_replication_governance` è IDEMPOTENTE.
Chiamata da `state.db.init_schema` dopo `seed_mission_control`.

REPLICATION_GOVERNANCE — Missione:
  Proteggere l'integrità del processo di genesis, packaging e distacco delle
  Dedicated Mercury, garantendo che nessuna replica sia creata o attivata
  senza le necessarie verifiche deterministiche e approvazioni umane.

Mandati V0 (modalità conservativa):
  - GENESIS_PROPOSE:                proposal
  - GENESIS_APPROVE:                escalation_required
  - PACKAGE_CREATE:                 proposal
  - READY_FOR_PROVISIONING:         escalation_required
  - GENESIS_ABORT:                  proposal
  - GENESIS_ACTIVATE:               forbidden      ← V0 invariante
  - GENETIC_PACKAGE_UPDATE:         escalation_required
  - INDEPENDENCE_CONTRACT_CHANGE:   escalation_required
"""

from __future__ import annotations

import sqlite3

from mercury_foundry.autonomy.models import (
    create_mandate,
    create_organ,
    get_mandate,
    get_organ_by_key,
)

REPLICATION_GOVERNANCE_KEY     = "REPLICATION_GOVERNANCE"
REPLICATION_GOVERNANCE_NAME    = "Replication Governance"
REPLICATION_GOVERNANCE_MISSION = (
    "Proteggere l'integrità del processo di genesis, packaging e distacco delle "
    "Dedicated Mercury, garantendo che nessuna replica sia creata o attivata "
    "senza le necessarie verifiche deterministiche e approvazioni umane."
)

INITIAL_MANDATES: list[tuple[str, str]] = [
    ("GENESIS_PROPOSE",               "proposal"),
    ("GENESIS_APPROVE",               "escalation_required"),
    ("PACKAGE_CREATE",                "proposal"),
    ("READY_FOR_PROVISIONING",        "escalation_required"),
    ("GENESIS_ABORT",                 "proposal"),
    ("GENESIS_ACTIVATE",              "forbidden"),
    ("GENETIC_PACKAGE_UPDATE",        "escalation_required"),
    ("INDEPENDENCE_CONTRACT_CHANGE",  "escalation_required"),
]


def seed_replication_governance(conn: sqlite3.Connection) -> None:
    """Crea REPLICATION_GOVERNANCE e i suoi 8 mandati iniziali se non esistono."""
    organ = get_organ_by_key(conn, REPLICATION_GOVERNANCE_KEY)
    if organ is None:
        create_organ(
            conn,
            organ_key=REPLICATION_GOVERNANCE_KEY,
            name=REPLICATION_GOVERNANCE_NAME,
            mission=REPLICATION_GOVERNANCE_MISSION,
        )
        organ = get_organ_by_key(conn, REPLICATION_GOVERNANCE_KEY)

    organ_id: int = organ["id"]

    for decision_type, authority_mode in INITIAL_MANDATES:
        if get_mandate(conn, organ_id, decision_type) is None:
            create_mandate(
                conn,
                organ_id=organ_id,
                decision_type=decision_type,
                authority_mode=authority_mode,
            )

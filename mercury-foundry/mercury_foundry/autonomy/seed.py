"""Seeding dell'organo pilota FOUNDRY_GOVERNANCE — MF-ARCH-008.

`seed_foundry_governance` è IDEMPOTENTE: se l'organo e i mandati esistono già,
non vengono duplicati. Viene chiamata da `state.db.init_schema` all'avvio.

FOUNDRY_GOVERNANCE — Missione:
  Proteggere integrità, autorizzazione e tracciabilità delle transizioni
  critiche della Foundry.

Configurazione iniziale prudente (modalità shadow):
  - GOAL_STATUS_TRANSITION  → proposal
  - CANDIDATE_APPROVAL      → escalation_required
  - APPROVAL_REVOCATION     → escalation_required
  - PRODUCTION_DB_MUTATION  → forbidden

Nessun organo può autoassegnarsi autorità: il seeding è eseguito da codice
di sistema (init_schema), non dall'organo stesso.
"""

from __future__ import annotations

import sqlite3

from mercury_foundry.autonomy.models import (
    create_mandate,
    create_organ,
    get_mandate,
    get_organ_by_key,
)

GOVERNANCE_ORGAN_KEY = "FOUNDRY_GOVERNANCE"
GOVERNANCE_NAME = "Foundry Governance"
GOVERNANCE_MISSION = (
    "Proteggere integrità, autorizzazione e tracciabilità delle transizioni "
    "critiche della Foundry."
)

# Mandati iniziali: decision_type → authority_mode
INITIAL_MANDATES: list[tuple[str, str]] = [
    ("GOAL_STATUS_TRANSITION", "proposal"),
    ("CANDIDATE_APPROVAL",     "escalation_required"),
    ("APPROVAL_REVOCATION",    "escalation_required"),
    ("PRODUCTION_DB_MUTATION", "forbidden"),
]


def seed_foundry_governance(conn: sqlite3.Connection) -> None:
    """Crea l'organo FOUNDRY_GOVERNANCE e i suoi 4 mandati iniziali se non esistono.

    Idempotente: ogni CREATE è condizionato a un check preventivo con GET.
    Non usa ON CONFLICT per preservare la leggibilità e la compatibilità con
    la gestione delle FK in SQLite.
    """
    # 1. Crea l'organo se assente.
    organ = get_organ_by_key(conn, GOVERNANCE_ORGAN_KEY)
    if organ is None:
        create_organ(
            conn,
            organ_key=GOVERNANCE_ORGAN_KEY,
            name=GOVERNANCE_NAME,
            mission=GOVERNANCE_MISSION,
        )
        organ = get_organ_by_key(conn, GOVERNANCE_ORGAN_KEY)

    organ_id: int = organ["id"]

    # 2. Crea i mandati mancanti.
    for decision_type, authority_mode in INITIAL_MANDATES:
        if get_mandate(conn, organ_id, decision_type) is None:
            create_mandate(
                conn,
                organ_id=organ_id,
                decision_type=decision_type,
                authority_mode=authority_mode,
            )

"""Seeding di MISSION_CONTROL — MF-MISSION-001.

`seed_mission_control` è IDEMPOTENTE: se l'organo e i mandati esistono già,
non vengono duplicati. Viene chiamata da `state.db.init_schema` dopo
`seed_foundry_governance`.

MISSION_CONTROL — Missione:
  Coordinare la presa in carico, validazione e lifecycle delle Mission
  entro i confini dell'autorità delegata.

Mandati iniziali (V0 — modalità conservativa):
  - MISSION_CREATE                   → proposal
  - MISSION_SUBMIT                   → proposal
  - MISSION_ACCEPT                   → escalation_required
  - MISSION_ACTIVATE                 → escalation_required
  - MISSION_PAUSE                    → proposal
  - MISSION_TERMINATE                → escalation_required
  - MISSION_COMPLETE                 → proposal
  - MISSION_PROMOTE_TO_BUSINESS_CELL → forbidden
  - MISSION_AUTHORITY_CHANGE         → forbidden

La promozione a Business Cell e il cambio di authority restano forbidden in V0:
nessun agente può eseguirle autonomamente.
"""

from __future__ import annotations

import sqlite3

from mercury_foundry.autonomy.models import (
    create_mandate,
    create_organ,
    get_mandate,
    get_organ_by_key,
)

MISSION_CONTROL_KEY   = "MISSION_CONTROL"
MISSION_CONTROL_NAME  = "Mission Control"
MISSION_CONTROL_MISSION = (
    "Coordinare la presa in carico, validazione e lifecycle delle Mission "
    "entro i confini dell'autorità delegata."
)

# decision_type → authority_mode
INITIAL_MANDATES: list[tuple[str, str]] = [
    ("MISSION_CREATE",                   "proposal"),
    ("MISSION_SUBMIT",                   "proposal"),
    ("MISSION_ACCEPT",                   "escalation_required"),
    ("MISSION_ACTIVATE",                 "escalation_required"),
    ("MISSION_PAUSE",                    "proposal"),
    ("MISSION_TERMINATE",                "escalation_required"),
    ("MISSION_COMPLETE",                 "proposal"),
    ("MISSION_PROMOTE_TO_BUSINESS_CELL", "forbidden"),
    ("MISSION_AUTHORITY_CHANGE",         "forbidden"),
]


def seed_mission_control(conn: sqlite3.Connection) -> None:
    """Crea MISSION_CONTROL e i suoi 9 mandati iniziali se non esistono.

    Idempotente: ogni CREATE è condizionato a un check preventivo con GET.
    """
    organ = get_organ_by_key(conn, MISSION_CONTROL_KEY)
    if organ is None:
        create_organ(
            conn,
            organ_key=MISSION_CONTROL_KEY,
            name=MISSION_CONTROL_NAME,
            mission=MISSION_CONTROL_MISSION,
        )
        organ = get_organ_by_key(conn, MISSION_CONTROL_KEY)

    organ_id: int = organ["id"]

    for decision_type, authority_mode in INITIAL_MANDATES:
        if get_mandate(conn, organ_id, decision_type) is None:
            create_mandate(
                conn,
                organ_id=organ_id,
                decision_type=decision_type,
                authority_mode=authority_mode,
            )

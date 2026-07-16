"""Seeding di ECONOMIC_GOVERNANCE — MF-OUTCOME-001.

`seed_economic_governance` è IDEMPOTENTE: se l'organo e i mandati esistono già,
non vengono duplicati. Viene chiamata da `state.db.init_schema` dopo
`seed_replication_governance`.

ECONOMIC_GOVERNANCE — Missione:
  Garantire che ogni Mission operi entro budget, tempo e criteri di evidenza
  esplicitamente approvati. Nessun aumento automatico di budget. Nessuno
  scale autonomo. Ogni decisione di stop richiede escalation.

Mandati V0 (modalità conservativa):
  - OUTCOME_PLAN_CREATE      → proposal
  - RESOURCE_ALLOCATE        → escalation_required
  - RESOURCE_CONSUME         → proposal
  - OUTCOME_EVALUATE         → proposal
  - OUTCOME_PAUSE            → proposal
  - OUTCOME_STOP             → escalation_required
  - OUTCOME_SCALE_PROPOSE    → proposal
  - OUTCOME_BUDGET_INCREASE  → forbidden  ← invariante V0
"""

from __future__ import annotations

import sqlite3

from mercury_foundry.autonomy.models import (
    create_mandate,
    create_organ,
    get_mandate,
    get_organ_by_key,
)

ECONOMIC_GOVERNANCE_KEY     = "ECONOMIC_GOVERNANCE"
ECONOMIC_GOVERNANCE_NAME    = "Economic Governance"
ECONOMIC_GOVERNANCE_MISSION = (
    "Garantire che ogni Mission operi entro budget, tempo e criteri di evidenza "
    "esplicitamente approvati. Nessun aumento automatico di budget, nessuno "
    "scale autonomo, ogni decisione critica richiede supervisione umana."
)

# decision_type → authority_mode
ECONOMIC_MANDATES: list[tuple[str, str]] = [
    ("OUTCOME_PLAN_CREATE",     "proposal"),
    ("RESOURCE_ALLOCATE",       "escalation_required"),
    ("RESOURCE_CONSUME",        "proposal"),
    ("OUTCOME_EVALUATE",        "proposal"),
    ("OUTCOME_PAUSE",           "proposal"),
    ("OUTCOME_STOP",            "escalation_required"),
    ("OUTCOME_SCALE_PROPOSE",   "proposal"),
    ("OUTCOME_BUDGET_INCREASE", "forbidden"),   # invariante V0
]


def seed_economic_governance(conn: sqlite3.Connection) -> None:
    """Crea ECONOMIC_GOVERNANCE e i suoi 8 mandati iniziali se non esistono.

    Idempotente: ogni CREATE è condizionato a un check preventivo con GET.
    """
    organ = get_organ_by_key(conn, ECONOMIC_GOVERNANCE_KEY)
    if organ is None:
        create_organ(
            conn,
            organ_key = ECONOMIC_GOVERNANCE_KEY,
            name      = ECONOMIC_GOVERNANCE_NAME,
            mission   = ECONOMIC_GOVERNANCE_MISSION,
        )
        organ = get_organ_by_key(conn, ECONOMIC_GOVERNANCE_KEY)

    organ_id: int = organ["id"]

    for decision_type, authority_mode in ECONOMIC_MANDATES:
        if get_mandate(conn, organ_id, decision_type) is None:
            create_mandate(
                conn,
                organ_id       = organ_id,
                decision_type  = decision_type,
                authority_mode = authority_mode,
            )

"""Contratto Expedition — MF-MISSION-001.

Definisce solo le strutture dati e la funzione di valutazione della readiness.
Nessuna Expedition viene creata realmente in questa milestone.

Quando una Mission passa a `ready` o `active`:
  - si calcola `ExpeditionReadinessResult`;
  - si emette `expedition.requested` (ready) o `expedition.not_ready` (blocchi);
  - si collega il risultato alla Mission nell'audit.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from mercury_foundry.mission.models import (
    Mission,
    MissionStatus,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Contratti (usati da MF-EXP-001)
# ---------------------------------------------------------------------------

@dataclass
class ExpeditionRequest:
    """Contratto che verrà utilizzato da MF-EXP-001 per avviare un'Expedition.

    In V0 è solo un'intenzione strutturata: nessun runtime viene avviato.
    """
    expedition_request_id: str
    mission_id: str
    required_capabilities: list[str]           # capability_id strings
    authority_scope: str                        # authority_mode richiesto
    budget_envelope: float                      # importo disponibile
    knowledge_scope: str
    requested_runtime_profile: str             # es. "minimal" | "standard"
    requested_at: str
    requested_by: str
    correlation_id: str

    def to_dict(self) -> dict:
        return {
            "expedition_request_id": self.expedition_request_id,
            "mission_id": self.mission_id,
            "required_capabilities": self.required_capabilities,
            "authority_scope": self.authority_scope,
            "budget_envelope": self.budget_envelope,
            "knowledge_scope": self.knowledge_scope,
            "requested_runtime_profile": self.requested_runtime_profile,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "correlation_id": self.correlation_id,
        }


@dataclass
class ExpeditionReadinessResult:
    """Risultato della valutazione di readiness di una Mission per una Expedition."""
    mission_id: str
    ready: bool
    evaluated_at: str
    missing_capabilities: list[str] = field(default_factory=list)
    unresolved_authority: list[str] = field(default_factory=list)
    budget_valid: bool = True
    constitutional_status: str = "pass"
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "ready": self.ready,
            "evaluated_at": self.evaluated_at,
            "missing_capabilities": self.missing_capabilities,
            "unresolved_authority": self.unresolved_authority,
            "budget_valid": self.budget_valid,
            "constitutional_status": self.constitutional_status,
            "blockers": self.blockers,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Valutazione readiness
# ---------------------------------------------------------------------------

def assess_expedition_readiness(
    conn: sqlite3.Connection,
    mission: Mission,
    *,
    capability_provider=None,
) -> ExpeditionReadinessResult:
    """Valuta la readiness di una Mission per avviare una Expedition.

    Non crea nessuna Expedition. Produce solo una valutazione strutturata.
    I provider possono essere None (usa NullProvider di default).
    """
    from mercury_foundry.mission.capability_contracts import (
        NullCapabilityProvider,
    )
    cap_provider = capability_provider or NullCapabilityProvider()

    blockers: list[str] = []
    warnings: list[str] = []

    # 1. Verifica stato Mission
    if mission.status not in (MissionStatus.READY, MissionStatus.ACTIVE):
        blockers.append(
            f"Mission in stato {mission.status.value!r}: deve essere ready o active "
            "per procedere con una Expedition."
        )

    # 2. Capability
    cap_report = cap_provider.check_capability_availability(mission.required_capabilities)
    mandatory_gaps = cap_report.mandatory_gaps(mission.required_capabilities)
    optional_gaps = cap_report.optional_gaps(mission.required_capabilities)

    missing_caps = [g.capability_id for g in mandatory_gaps]
    if missing_caps:
        blockers.append(
            f"Capability obbligatorie non disponibili: {missing_caps}"
        )
    if optional_gaps:
        warnings.append(
            f"Capability opzionali non disponibili: {[g.capability_id for g in optional_gaps]}"
        )

    # 3. Budget
    budget_valid = mission.budget.approved_amount >= 0
    if not budget_valid:
        blockers.append("Budget non valido: approved_amount negativo.")

    # 4. Authority
    unresolved: list[str] = []
    authority_mode = mission.authority_request.requested_mode
    if authority_mode == "autonomous":
        warnings.append(
            "authority_mode=autonomous richiederà verifica mandate MISSION_ACTIVATE "
            "prima dell'avvio effettivo."
        )

    # 5. Risk profile
    if mission.risk_profile.risk_level == "critical":
        warnings.append(
            "risk_level=critical: l'attivazione richiede escalation e approvazione umana."
        )

    ready = len(blockers) == 0

    return ExpeditionReadinessResult(
        mission_id=mission.mission_id,
        ready=ready,
        evaluated_at=_now_iso(),
        missing_capabilities=missing_caps,
        unresolved_authority=unresolved,
        budget_valid=budget_valid,
        constitutional_status="pass",
        blockers=blockers,
        warnings=warnings,
    )


def emit_expedition_event(
    conn: sqlite3.Connection,
    mission: Mission,
    readiness: ExpeditionReadinessResult,
    correlation_id: str,
) -> None:
    """Emette expedition.requested o expedition.not_ready nell'audit log.

    Non crea una Expedition reale. È un intent record.
    """
    from mercury_foundry.mission.events import emit_mission_event

    action = "expedition.requested" if readiness.ready else "expedition.not_ready"
    emit_mission_event(
        conn,
        action=action,
        mission_db_id=mission.id,
        mission_id=mission.mission_id,
        actor_id="system",
        correlation_id=correlation_id,
        metadata={
            "expedition_request_id": str(uuid.uuid4()),
            "readiness": readiness.to_dict(),
            "note": (
                "INTENT ONLY — Nessuna Expedition è stata creata. "
                "Questo è un evento preparatorio per MF-EXP-001."
            ),
        },
    )

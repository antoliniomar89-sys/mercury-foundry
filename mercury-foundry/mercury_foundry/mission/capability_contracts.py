"""Contratti di capability per il Mission Layer — MF-MISSION-001.

Definisce interfacce (Protocol) per i provider di capability, knowledge,
discovery e delivery. In V0 viene fornito solo il NullProvider che restituisce
NOT_AVAILABLE senza eseguire alcuna attività reale.

Design:
  - I Protocol sono strutturali (duck typing): nessun ABC, nessuna
    registrazione di classe.
  - I NullProvider non bloccano la creazione di una Mission se la capability
    non è obbligatoria.
  - I NullProvider producono un capability gap se la capability è obbligatoria.
  - I provider reali vengono iniettati tramite dependency injection al momento
    della costruzione di MissionIntakeService.
  - Nessun LLM, nessuna chiamata di rete, nessun effetto collaterale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from mercury_foundry.mission.models import (
    Mission,
    RequiredCapability,
)


# ---------------------------------------------------------------------------
# Enumerazioni di stato dei provider
# ---------------------------------------------------------------------------

class ProviderStatus(str, Enum):
    AVAILABLE        = "available"
    NOT_AVAILABLE    = "not_available"
    NOT_IMPLEMENTED  = "not_implemented"
    DEGRADED         = "degraded"


# ---------------------------------------------------------------------------
# Strutture dati di risposta
# ---------------------------------------------------------------------------

@dataclass
class CapabilityResolution:
    """Risultato della risoluzione di una singola RequiredCapability."""
    capability_id: str
    status: ProviderStatus
    available_version: str | None = None
    gap_reason: str | None = None

    @property
    def is_gap(self) -> bool:
        return self.status != ProviderStatus.AVAILABLE


@dataclass
class CapabilityAvailabilityReport:
    """Report complessivo per l'insieme delle capability richieste da una Mission."""
    resolutions: list[CapabilityResolution] = field(default_factory=list)

    @property
    def has_mandatory_gaps(self) -> bool:
        """True se almeno una capability obbligatoria non è disponibile."""
        # NB: chiamato dal servizio intake che conosce la mandatory flag
        return any(r.is_gap for r in self.resolutions)

    def mandatory_gaps(
        self, requirements: list[RequiredCapability]
    ) -> list[CapabilityResolution]:
        req_map = {r.capability_id: r for r in requirements}
        return [
            res for res in self.resolutions
            if res.is_gap and req_map.get(res.capability_id, None) is not None
            and req_map[res.capability_id].mandatory
        ]

    def optional_gaps(
        self, requirements: list[RequiredCapability]
    ) -> list[CapabilityResolution]:
        req_map = {r.capability_id: r for r in requirements}
        return [
            res for res in self.resolutions
            if res.is_gap and req_map.get(res.capability_id, None) is not None
            and not req_map[res.capability_id].mandatory
        ]


@dataclass
class KnowledgeResolution:
    knowledge_scope: str
    accessible_refs: list[str] = field(default_factory=list)
    status: ProviderStatus = ProviderStatus.NOT_AVAILABLE


@dataclass
class LearningRecord:
    mission_id: str
    learning_type: str
    content: dict = field(default_factory=dict)


@dataclass
class DiscoveryContext:
    mission_id: str
    context_refs: list[str] = field(default_factory=list)
    status: ProviderStatus = ProviderStatus.NOT_AVAILABLE


@dataclass
class DeliveryReadiness:
    mission_id: str
    ready: bool = False
    blockers: list[str] = field(default_factory=list)
    status: ProviderStatus = ProviderStatus.NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Protocol definitions
# ---------------------------------------------------------------------------

@runtime_checkable
class CapabilityProvider(Protocol):
    def resolve_required_capabilities(
        self, mission: Mission
    ) -> CapabilityAvailabilityReport: ...

    def check_capability_availability(
        self, requirements: list[RequiredCapability]
    ) -> CapabilityAvailabilityReport: ...

    def get_capability_version(
        self, capability_id: str
    ) -> str | None: ...

    def report_capability_gap(
        self, mission_id: str, requirement: RequiredCapability
    ) -> None: ...


@runtime_checkable
class KnowledgeProvider(Protocol):
    def resolve_knowledge_scope(self, mission: Mission) -> KnowledgeResolution: ...
    def get_accessible_knowledge_refs(self, mission: Mission) -> list[str]: ...
    def register_mission_learning(
        self, mission_id: str, learning_record: LearningRecord
    ) -> None: ...


@runtime_checkable
class DiscoveryProvider(Protocol):
    def request_discovery_context(self, mission: Mission) -> DiscoveryContext: ...
    def report_discovery_evidence(
        self, mission_id: str, evidence: dict
    ) -> None: ...


@runtime_checkable
class DeliveryProvider(Protocol):
    def assess_delivery_readiness(self, mission: Mission) -> DeliveryReadiness: ...
    def report_delivery_result(
        self, mission_id: str, result: dict
    ) -> None: ...


# ---------------------------------------------------------------------------
# Null Providers (default, non bloccanti)
# ---------------------------------------------------------------------------

class NullCapabilityProvider:
    """Provider di capability null: nessuna capability è disponibile.

    - Non blocca la creazione della Mission se la capability non è obbligatoria.
    - Restituisce gap per ogni capability richiesta (status NOT_IMPLEMENTED).
    - È sostituibile tramite DI.
    """

    def resolve_required_capabilities(
        self, mission: Mission
    ) -> CapabilityAvailabilityReport:
        return self.check_capability_availability(mission.required_capabilities)

    def check_capability_availability(
        self, requirements: list[RequiredCapability]
    ) -> CapabilityAvailabilityReport:
        resolutions = [
            CapabilityResolution(
                capability_id=req.capability_id,
                status=ProviderStatus.NOT_IMPLEMENTED,
                gap_reason="NullCapabilityProvider: nessun motore di capability disponibile in V0",
            )
            for req in requirements
        ]
        return CapabilityAvailabilityReport(resolutions=resolutions)

    def get_capability_version(self, capability_id: str) -> str | None:
        return None

    def report_capability_gap(
        self, mission_id: str, requirement: RequiredCapability
    ) -> None:
        # No-op: nessun sistema di notifica in V0
        pass


class NullKnowledgeProvider:
    def resolve_knowledge_scope(self, mission: Mission) -> KnowledgeResolution:
        return KnowledgeResolution(
            knowledge_scope=mission.knowledge_scope.value,
            status=ProviderStatus.NOT_IMPLEMENTED,
        )

    def get_accessible_knowledge_refs(self, mission: Mission) -> list[str]:
        return []

    def register_mission_learning(
        self, mission_id: str, learning_record: LearningRecord
    ) -> None:
        pass


class NullDiscoveryProvider:
    def request_discovery_context(self, mission: Mission) -> DiscoveryContext:
        return DiscoveryContext(
            mission_id=mission.mission_id,
            status=ProviderStatus.NOT_IMPLEMENTED,
        )

    def report_discovery_evidence(self, mission_id: str, evidence: dict) -> None:
        pass


class NullDeliveryProvider:
    def assess_delivery_readiness(self, mission: Mission) -> DeliveryReadiness:
        return DeliveryReadiness(
            mission_id=mission.mission_id,
            ready=False,
            blockers=["NullDeliveryProvider: nessun motore di delivery disponibile in V0"],
            status=ProviderStatus.NOT_IMPLEMENTED,
        )

    def report_delivery_result(self, mission_id: str, result: dict) -> None:
        pass

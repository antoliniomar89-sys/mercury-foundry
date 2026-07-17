"""Modelli di dominio per il Lead Agent."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class LeadStatus(str, Enum):
    NEW = "NEW"
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"


class LeadPriority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class LeadResultStatus(str, Enum):
    COMPLETED = "COMPLETED"
    BLOCKED_NO_WEB_ACCESS = "BLOCKED_NO_WEB_ACCESS"
    BLOCKED_INSUFFICIENT_LEADS = "BLOCKED_INSUFFICIENT_LEADS"
    BLOCKED_INVALID_OPPORTUNITY = "BLOCKED_INVALID_OPPORTUNITY"
    FAILED = "FAILED"


@dataclass
class Lead:
    """Un singolo lead qualificato o rifiutato.

    Invarianti:
    - source_url non può essere vuoto (causa REJECTED se mancante).
    - id generato automaticamente se non fornito.
    """

    name: str
    segment: str
    website: str
    public_contact: str
    contact_type: str
    location: str
    fit_reason: str
    evidence: str
    source_url: str
    priority: LeadPriority
    status: LeadStatus
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    rejection_reason: str = ""

    def __post_init__(self) -> None:
        # Converti enum se necessario
        if isinstance(self.priority, str):
            try:
                self.priority = LeadPriority(self.priority.upper())
            except ValueError:
                self.priority = LeadPriority.LOW
        if isinstance(self.status, str):
            try:
                self.status = LeadStatus(self.status.upper())
            except ValueError:
                self.status = LeadStatus.NEW

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "segment": self.segment,
            "website": self.website,
            "public_contact": self.public_contact,
            "contact_type": self.contact_type,
            "location": self.location,
            "fit_reason": self.fit_reason,
            "evidence": self.evidence,
            "source_url": self.source_url,
            "priority": self.priority.value,
            "status": self.status.value,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class LeadResult:
    """Risultato completo di una singola esecuzione del Lead Agent.

    Invarianti:
    - leads: max 10 (tagliato in __post_init__)
    - next_action: sempre non vuoto
    - qualified_count: len([l for l in leads if l.status == QUALIFIED])
    """

    status: LeadResultStatus
    opportunity_summary: dict
    next_action: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    leads: list[Lead] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    duplicates_discarded: int = 0
    rejected_count: int = 0
    qualified_count: int = 0
    block_reason: str | None = None
    discarded_leads: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.leads) > 10:
            self.leads = self.leads[:10]
        if not self.next_action:
            raise ValueError("LeadResult.next_action non può essere vuoto.")
        # Ricalcola contatori dai lead effettivi
        self.qualified_count = sum(1 for l in self.leads if l.status == LeadStatus.QUALIFIED)
        self.rejected_count = sum(1 for l in self.leads if l.status == LeadStatus.REJECTED)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "timestamp": self.timestamp,
            "opportunity_summary": self.opportunity_summary,
            "leads": [l.to_dict() for l in self.leads],
            "search_queries": self.search_queries,
            "sources_used": self.sources_used,
            "total_leads": len(self.leads),
            "qualified_count": self.qualified_count,
            "rejected_count": self.rejected_count,
            "duplicates_discarded": self.duplicates_discarded,
            "next_action": self.next_action,
            "block_reason": self.block_reason,
            "discarded_leads": self.discarded_leads,
        }

    def save(self, path: str | Path) -> Path:
        """Salva il risultato come JSON. Crea le directory intermedie."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p

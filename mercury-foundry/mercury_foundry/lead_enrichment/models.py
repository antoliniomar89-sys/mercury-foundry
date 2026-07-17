"""Modelli di dominio per il Lead Enrichment Agent."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class EnrichedLeadStatus(str, Enum):
    HIGH_FIT = "HIGH_FIT"
    PLAUSIBLE = "PLAUSIBLE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


class Contactability(str, Enum):
    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"
    NONE = "NONE"


class EnrichedLeadResultStatus(str, Enum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_REVIEW = "COMPLETED_WITH_REVIEW"
    BLOCKED_NO_WEB_ACCESS = "BLOCKED_NO_WEB_ACCESS"
    BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS = "BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS"
    BLOCKED_INVALID_LEAD_INPUT = "BLOCKED_INVALID_LEAD_INPUT"
    FAILED = "FAILED"


@dataclass
class EnrichedLead:
    """Un lead arricchito con verifica leggera e classificazione qualitativa.

    Invarianti:
    - lead_id: non vuoto.
    - contactability: DIRECT | INDIRECT | NONE.
    - qualification_status: HIGH_FIT | PLAUSIBLE | NEEDS_REVIEW | REJECTED.
    """

    lead_id: str
    name: str
    verified_role_or_business: str
    target_match: str
    primary_website: str
    public_contact: str
    contact_type: str
    secondary_profiles: list[str]
    evidence_summary: str
    source_urls: list[str]
    fit_reason: str
    contactability: Contactability
    qualification_status: EnrichedLeadStatus
    rejection_reason: str = ""
    next_action: str = ""
    # Campi aggiunti da MF-QB-CONTACT-VERIFY-001
    contact_page_url: str = ""
    verified_email: str = ""
    verified_form_url: str = ""
    verified_social_url: str = ""
    verification_evidence: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.contactability, str):
            try:
                self.contactability = Contactability(self.contactability.upper())
            except ValueError:
                self.contactability = Contactability.NONE
        if isinstance(self.qualification_status, str):
            try:
                self.qualification_status = EnrichedLeadStatus(
                    self.qualification_status.upper()
                )
            except ValueError:
                self.qualification_status = EnrichedLeadStatus.NEEDS_REVIEW

    def to_dict(self) -> dict:
        return {
            "lead_id": self.lead_id,
            "name": self.name,
            "verified_role_or_business": self.verified_role_or_business,
            "target_match": self.target_match,
            "primary_website": self.primary_website,
            "public_contact": self.public_contact,
            "contact_type": self.contact_type,
            "secondary_profiles": self.secondary_profiles,
            "evidence_summary": self.evidence_summary,
            "source_urls": self.source_urls,
            "fit_reason": self.fit_reason,
            "contactability": self.contactability.value,
            "qualification_status": self.qualification_status.value,
            "rejection_reason": self.rejection_reason,
            "next_action": self.next_action,
            # Campi verifica contatti (MF-QB-CONTACT-VERIFY-001)
            "contact_page_url": self.contact_page_url,
            "verified_email": self.verified_email,
            "verified_form_url": self.verified_form_url,
            "verified_social_url": self.verified_social_url,
            "verification_evidence": self.verification_evidence,
        }


@dataclass
class EnrichedLeadResult:
    """Risultato completo di un'esecuzione del Lead Enrichment Agent.

    Invarianti:
    - next_action: sempre non vuoto.
    - contatori ricalcolati in __post_init__.
    """

    status: EnrichedLeadResultStatus
    lead_result_summary: dict
    next_action: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    enriched_leads: list[EnrichedLead] = field(default_factory=list)
    rejected_leads: list[EnrichedLead] = field(default_factory=list)
    sources_consulted: list[str] = field(default_factory=list)
    block_reason: str | None = None
    high_fit_count: int = 0
    plausible_count: int = 0
    needs_review_count: int = 0
    rejected_count: int = 0

    def __post_init__(self) -> None:
        if not self.next_action:
            raise ValueError("EnrichedLeadResult.next_action non può essere vuoto.")
        self.high_fit_count = sum(
            1
            for l in self.enriched_leads
            if l.qualification_status == EnrichedLeadStatus.HIGH_FIT
        )
        self.plausible_count = sum(
            1
            for l in self.enriched_leads
            if l.qualification_status == EnrichedLeadStatus.PLAUSIBLE
        )
        self.needs_review_count = sum(
            1
            for l in self.enriched_leads
            if l.qualification_status == EnrichedLeadStatus.NEEDS_REVIEW
        )
        self.rejected_count = len(self.rejected_leads)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "timestamp": self.timestamp,
            "lead_result_summary": self.lead_result_summary,
            "high_fit_count": self.high_fit_count,
            "plausible_count": self.plausible_count,
            "needs_review_count": self.needs_review_count,
            "rejected_count": self.rejected_count,
            "enriched_leads": [l.to_dict() for l in self.enriched_leads],
            "rejected_leads": [l.to_dict() for l in self.rejected_leads],
            "sources_consulted": self.sources_consulted,
            "next_action": self.next_action,
            "block_reason": self.block_reason,
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

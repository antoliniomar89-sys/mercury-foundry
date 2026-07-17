"""Modelli di dominio per l'Opportunity Agent."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class OpportunityStatus(str, Enum):
    COMPLETED = "COMPLETED"
    BLOCKED_NO_WEB_ACCESS = "BLOCKED_NO_WEB_ACCESS"
    BLOCKED_NO_EVIDENCE = "BLOCKED_NO_EVIDENCE"
    FAILED = "FAILED"


@dataclass
class Evidence:
    """Una singola evidenza citabile con fonte verificabile."""

    text: str
    source_url: str

    def __post_init__(self) -> None:
        if not self.source_url:
            raise ValueError("Evidence.source_url non può essere vuoto.")
        if not self.text:
            raise ValueError("Evidence.text non può essere vuoto.")


@dataclass
class CandidateProblem:
    """Un problema candidato identificato dai segnali di mercato.

    Massimo 3 evidenze per candidato (invariante applicato in __post_init__).
    """

    problem: str
    target_customer: str
    evidence: list[Evidence] = field(default_factory=list)
    frequency_signal: str = ""
    urgency_signal: str = ""
    willingness_to_pay_signal: str = ""

    def __post_init__(self) -> None:
        if len(self.evidence) > 3:
            self.evidence = self.evidence[:3]


@dataclass
class OpportunityResult:
    """Risultato completo di una singola esecuzione dell'Opportunity Agent.

    Invarianti:
    - candidates_considered: max 3 (tagliato in __post_init__)
    - evidence: max 3 (tagliato in __post_init__)
    - next_action: sempre presente (stringa non vuota)
    - proposed_offer: al massimo 1 (campo scalare, non lista)
    """

    status: OpportunityStatus
    mandate: str
    next_action: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    problem: str | None = None
    target_customer: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    frequency_signal: str | None = None
    urgency_signal: str | None = None
    willingness_to_pay_signal: str | None = None
    proposed_offer: str | None = None
    delivery_format: str | None = None
    initial_price: str | None = None
    why_testable_fast: str | None = None
    risks: list[str] = field(default_factory=list)
    block_reason: str | None = None
    candidates_considered: list[CandidateProblem] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.candidates_considered) > 3:
            self.candidates_considered = self.candidates_considered[:3]
        if len(self.evidence) > 3:
            self.evidence = self.evidence[:3]
        if not self.next_action:
            raise ValueError("OpportunityResult.next_action non può essere vuoto.")

    # ------------------------------------------------------------------
    # Serializzazione
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        def ev_dict(e: Evidence) -> dict:
            return {"text": e.text, "source_url": e.source_url}

        def cand_dict(c: CandidateProblem) -> dict:
            return {
                "problem": c.problem,
                "target_customer": c.target_customer,
                "evidence": [ev_dict(e) for e in c.evidence],
                "frequency_signal": c.frequency_signal,
                "urgency_signal": c.urgency_signal,
                "willingness_to_pay_signal": c.willingness_to_pay_signal,
            }

        return {
            "status": self.status.value,
            "mandate": self.mandate,
            "timestamp": self.timestamp,
            "problem": self.problem,
            "target_customer": self.target_customer,
            "evidence": [ev_dict(e) for e in self.evidence],
            "source_urls": self.source_urls,
            "frequency_signal": self.frequency_signal,
            "urgency_signal": self.urgency_signal,
            "willingness_to_pay_signal": self.willingness_to_pay_signal,
            "proposed_offer": self.proposed_offer,
            "delivery_format": self.delivery_format,
            "initial_price": self.initial_price,
            "why_testable_fast": self.why_testable_fast,
            "risks": self.risks,
            "next_action": self.next_action,
            "block_reason": self.block_reason,
            "candidates_considered": [cand_dict(c) for c in self.candidates_considered],
        }

    def save(self, path: str | Path) -> Path:
        """Salva il risultato come JSON nel percorso indicato.

        Crea le directory intermedie se necessario.
        Ritorna il Path effettivo scritto.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p

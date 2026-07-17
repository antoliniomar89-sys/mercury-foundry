"""Modelli di dominio per l'Outreach Agent — MF-QB-OUTREACH-001."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class DeliveryStatus(str, Enum):
    PREPARED = "PREPARED"
    SENT = "SENT"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ResponseStatus(str, Enum):
    NONE = "NONE"
    REPLIED = "REPLIED"
    INTERESTED = "INTERESTED"
    NOT_INTERESTED = "NOT_INTERESTED"
    BOUNCED = "BOUNCED"


class OutreachResultStatus(str, Enum):
    PREPARED = "PREPARED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BLOCKED_EMAIL_PROVIDER_NOT_CONFIGURED = "BLOCKED_EMAIL_PROVIDER_NOT_CONFIGURED"
    BLOCKED_NO_DIRECT_LEADS = "BLOCKED_NO_DIRECT_LEADS"


@dataclass
class OutreachMessage:
    """Messaggio per un singolo lead, con stato di consegna e follow-up."""

    lead_id: str
    recipient: str                              # email o URL form
    subject: str
    message: str
    channel: str                                # "email" | "website_form"
    prepared_at: str
    sent_at: str = ""
    delivery_status: DeliveryStatus = DeliveryStatus.PREPARED
    provider_message_id: str = ""
    error: str = ""
    follow_up_due: str = ""
    follow_up_message: str = ""
    response_status: ResponseStatus = ResponseStatus.NONE
    next_action: str = ""

    def to_dict(self) -> dict:
        return {
            "lead_id": self.lead_id,
            "recipient": self.recipient,
            "subject": self.subject,
            "message": self.message,
            "channel": self.channel,
            "prepared_at": self.prepared_at,
            "sent_at": self.sent_at,
            "delivery_status": self.delivery_status.value
                if isinstance(self.delivery_status, DeliveryStatus)
                else str(self.delivery_status),
            "provider_message_id": self.provider_message_id,
            "error": self.error,
            "follow_up_due": self.follow_up_due,
            "follow_up_message": self.follow_up_message,
            "response_status": self.response_status.value
                if isinstance(self.response_status, ResponseStatus)
                else str(self.response_status),
            "next_action": self.next_action,
        }

    @classmethod
    def from_dict(cls, d: dict) -> OutreachMessage:
        ds_raw = d.get("delivery_status", "PREPARED")
        try:
            ds = DeliveryStatus(ds_raw)
        except ValueError:
            ds = DeliveryStatus.PREPARED

        rs_raw = d.get("response_status", "NONE")
        try:
            rs = ResponseStatus(rs_raw)
        except ValueError:
            rs = ResponseStatus.NONE

        return cls(
            lead_id=d.get("lead_id", ""),
            recipient=d.get("recipient", ""),
            subject=d.get("subject", ""),
            message=d.get("message", ""),
            channel=d.get("channel", "email"),
            prepared_at=d.get("prepared_at", ""),
            sent_at=d.get("sent_at", ""),
            delivery_status=ds,
            provider_message_id=d.get("provider_message_id", ""),
            error=d.get("error", ""),
            follow_up_due=d.get("follow_up_due", ""),
            follow_up_message=d.get("follow_up_message", ""),
            response_status=rs,
            next_action=d.get("next_action", ""),
        )


@dataclass
class OutreachResult:
    """Risultato di una sessione di outreach (prepare o send)."""

    status: OutreachResultStatus
    timestamp: str
    messages: list[OutreachMessage] = field(default_factory=list)
    next_action: str = ""
    block_reason: str | None = None
    expected_provider: str = ""
    missing_env_vars: list[str] = field(default_factory=list)
    prepared_count: int = 0
    sent_count: int = 0
    failed_count: int = 0

    def to_dict(self) -> dict:
        return {
            "status": self.status.value
                if isinstance(self.status, OutreachResultStatus)
                else str(self.status),
            "timestamp": self.timestamp,
            "messages": [m.to_dict() for m in self.messages],
            "next_action": self.next_action,
            "block_reason": self.block_reason,
            "expected_provider": self.expected_provider,
            "missing_env_vars": self.missing_env_vars,
            "prepared_count": self.prepared_count,
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
        }

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

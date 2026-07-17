"""Modelli di dominio per il Revenue Scan — brief in input e report in output."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PrimaryGoal(str, Enum):
    BOOKINGS         = "bookings"
    FOOT_TRAFFIC     = "foot_traffic"
    EVENTS           = "events"
    DELIVERY         = "delivery"
    AVERAGE_TICKET   = "average_ticket"
    REPEAT_CUSTOMERS = "repeat_customers"


class ReportStatus(str, Enum):
    READY            = "ready"
    REVIEW_REQUIRED  = "review_required"


@dataclass
class RevenueScanBrief:
    """Input del cliente per il Revenue Scan.

    I campi obbligatori bastano a generare un report di base.
    Ogni campo opzionale aggiuntivo aumenta il confidence_level.

    Nota V0: nessuno scraping automatico. I testi pubblici (recensioni,
    profilo social, sito) vengono copiati manualmente dal cliente nel brief.
    """

    # Obbligatori
    business_name:       str
    business_type:       str
    city:                str
    primary_goal:        PrimaryGoal
    idempotency_key:     str

    # Timestamp
    requested_at:        datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # Opzionali — presenza aumenta confidence_level
    website_url:          str | None = None
    instagram_url:        str | None = None
    google_maps_url:      str | None = None
    business_description: str | None = None
    target_customer:      str | None = None
    current_offer:        str | None = None
    public_reviews_text:  str | None = None
    social_profile_text:  str | None = None
    website_text:         str | None = None
    known_constraints:    str | None = None

    # Preferenze
    preferred_language: str = "it"

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RevenueScanBrief:
        """Deserializza da un dict (es. JSON caricato da file).

        Raises:
            KeyError:   campo obbligatorio mancante.
            ValueError: primary_goal non riconosciuto.
        """
        goal_raw = data.get("primary_goal", "")
        try:
            goal = PrimaryGoal(goal_raw)
        except ValueError:
            valid = [g.value for g in PrimaryGoal]
            raise ValueError(
                f"primary_goal '{goal_raw}' non valido. "
                f"Valori accettati: {', '.join(valid)}"
            )

        requested_at_raw = data.get("requested_at")
        if isinstance(requested_at_raw, str):
            requested_at = datetime.fromisoformat(requested_at_raw)
        elif isinstance(requested_at_raw, datetime):
            requested_at = requested_at_raw
        else:
            requested_at = datetime.now(timezone.utc)

        return cls(
            business_name=data["business_name"],
            business_type=data["business_type"],
            city=data["city"],
            primary_goal=goal,
            idempotency_key=data.get("idempotency_key") or str(uuid.uuid4()),
            requested_at=requested_at,
            website_url=data.get("website_url"),
            instagram_url=data.get("instagram_url"),
            google_maps_url=data.get("google_maps_url"),
            business_description=data.get("business_description"),
            target_customer=data.get("target_customer"),
            current_offer=data.get("current_offer"),
            public_reviews_text=data.get("public_reviews_text"),
            social_profile_text=data.get("social_profile_text"),
            website_text=data.get("website_text"),
            known_constraints=data.get("known_constraints"),
            preferred_language=data.get("preferred_language", "it"),
        )


@dataclass
class RevenueScanReport:
    """Output del Revenue Scan.

    Tutti i campi sono prodotti dall'analisi AI + quality gate.
    Il campo `status` diventa REVIEW_REQUIRED se il quality gate fallisce.
    """

    report_id:                 str
    business_name:             str
    generated_at:              datetime

    executive_summary:         str
    overall_score:             int        # 0-100, ponderato
    visibility_score:          int        # 0-100
    conversion_score:          int        # 0-100
    reputation_score:          int        # 0-100
    offer_score:               int        # 0-100
    retention_score:           int        # 0-100

    top_revenue_leaks:         list[str]  # max 5
    priority_actions:          list[str]  # max 10
    seven_day_plan:            list[str]  # esattamente 7
    ready_to_publish_posts:    list[str]  # esattamente 3
    promotional_offer:         str
    thirty_day_kpis:           list[str]  # max 6

    assumptions:               list[str]
    missing_information:       list[str]
    confidence_level:          int        # 0-100
    human_review_required:     bool
    estimated_delivery_status: str

    status:         ReportStatus = ReportStatus.READY
    quality_issues: list[str]   = field(default_factory=list)

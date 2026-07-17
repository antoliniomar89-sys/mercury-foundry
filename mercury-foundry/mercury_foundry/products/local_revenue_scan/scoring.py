"""Scoring trasparente per il Revenue Scan.

Pesi iniziali (somma = 100):
  visibility  20%
  conversion  25%
  reputation  20%
  offer       20%
  retention   15%

Il punteggio complessivo è la media pesata dei 5 subscore (0-100 ciascuno).
Il confidence_level riflette quante informazioni opzionali sono state fornite:
più il brief è ricco, più l'analisi è affidabile.
"""

from __future__ import annotations

from mercury_foundry.products.local_revenue_scan.models import RevenueScanBrief

# ------------------------------------------------------------------
# Pesi pubblici — documentati nel report per trasparenza
# ------------------------------------------------------------------
SCORING_WEIGHTS: dict[str, int] = {
    "visibility": 20,
    "conversion": 25,
    "reputation": 20,
    "offer":      20,
    "retention":  15,
}

_WEIGHT_TOTAL: int = sum(SCORING_WEIGHTS.values())  # 100

# Campi opzionali che contribuiscono al confidence_level
_OPTIONAL_FIELDS = (
    "business_description",
    "target_customer",
    "current_offer",
    "public_reviews_text",
    "social_profile_text",
    "website_text",
    "website_url",
    "instagram_url",
    "google_maps_url",
    "known_constraints",
)

_CONFIDENCE_BASE    = 30   # minimo con solo i campi obbligatori
_CONFIDENCE_MAX_BONUS = 60  # bonus massimo con tutti i campi opzionali
# Range risultante: 30-90. Mai 0 né 100: c'è sempre qualche incertezza.


def compute_overall_score(
    *,
    visibility: int,
    conversion: int,
    reputation: int,
    offer:      int,
    retention:  int,
) -> int:
    """Calcola il punteggio complessivo pesato. Tutti i subscore 0-100.

    Usa keyword-only args per evitare errori di ordine al call site.
    """
    raw = (
        visibility * SCORING_WEIGHTS["visibility"]
        + conversion * SCORING_WEIGHTS["conversion"]
        + reputation * SCORING_WEIGHTS["reputation"]
        + offer      * SCORING_WEIGHTS["offer"]
        + retention  * SCORING_WEIGHTS["retention"]
    ) / _WEIGHT_TOTAL
    return max(0, min(100, round(raw)))


def compute_confidence_level(brief: RevenueScanBrief) -> int:
    """Confidence 30-90 basato sul numero di campi opzionali forniti.

    Non inventa dati mancanti. Campi mancanti → confidence più bassa.
    """
    filled = sum(
        1 for f in _OPTIONAL_FIELDS
        if getattr(brief, f, None)
    )
    bonus = round((filled / len(_OPTIONAL_FIELDS)) * _CONFIDENCE_MAX_BONUS)
    return _CONFIDENCE_BASE + bonus

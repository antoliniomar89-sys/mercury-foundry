"""ProductFamilyAssessment deterministico — MF-REPL-001.

Valuta se un insieme di prodotti forma una famiglia coerente
che può essere gestita da una singola Dedicated Mercury.

Algoritmo (nessun LLM, input strutturati):
  Punteggio 0–8 basato su 8 dimensioni di condivisione.
  Ogni dimensione condivisa vale 1 punto.

  score 7-8  → single_dedicated_mercury
  score 4-6  → manual_review
  score 0-3  → separate_instances

  Eccezioni deterministiche (override del punteggio):
  - clienti diversi + mercati diversi → separate_instances
  - business model incompatibile (shared_business_model=False) → separate_instances
  - dati non condivisibili (shared_data_boundary=False) → separate_instances o manual_review
  - vincoli normativi incompatibili (shared_regulatory_boundary=False) → separate_instances

  Prodotto singolo (len(product_ids)==1) → single_dedicated_mercury senza analisi.
  Lista vuota → insufficient_evidence.
"""

from __future__ import annotations

from mercury_foundry.replication.models import (
    FamilyRecommendation,
    ProductFamilyAssessment,
    _new_id,
    _now_iso,
)


def assess_product_family(
    product_ids: list[str],
    *,
    shared_customer: bool = False,
    shared_market: bool = False,
    shared_problem_space: bool = False,
    shared_capabilities: bool = False,
    shared_distribution: bool = False,
    shared_business_model: bool = False,
    shared_data_boundary: bool = False,
    shared_regulatory_boundary: bool = False,
    evidence_refs: list[str] | None = None,
) -> ProductFamilyAssessment:
    """Produce un ProductFamilyAssessment deterministico.

    Non usa LLM. Deterministico su input strutturati.
    """
    now = _now_iso()
    conflicts: list[str] = []
    warnings: list[str] = []

    # Caso degenere: lista vuota
    if not product_ids:
        return ProductFamilyAssessment(
            assessment_id=_new_id(),
            product_ids=product_ids,
            shared_customer=False,
            shared_market=False,
            shared_problem_space=False,
            shared_capabilities=False,
            shared_distribution=False,
            shared_business_model=False,
            shared_data_boundary=False,
            shared_regulatory_boundary=False,
            coherence_score=0.0,
            conflicts=["product_ids è vuota: impossibile valutare la famiglia"],
            recommendation=FamilyRecommendation.INSUFFICIENT_EVIDENCE,
            evaluated_at=now,
            evidence_refs=evidence_refs or [],
            warnings=[],
        )

    # Prodotto singolo: sempre coerente
    if len(product_ids) == 1:
        return ProductFamilyAssessment(
            assessment_id=_new_id(),
            product_ids=product_ids,
            shared_customer=True,
            shared_market=True,
            shared_problem_space=True,
            shared_capabilities=True,
            shared_distribution=True,
            shared_business_model=True,
            shared_data_boundary=True,
            shared_regulatory_boundary=True,
            coherence_score=1.0,
            conflicts=[],
            recommendation=FamilyRecommendation.SINGLE_DEDICATED_MERCURY,
            evaluated_at=now,
            evidence_refs=evidence_refs or [],
            warnings=["Prodotto singolo: coerenza sempre 1.0 per definizione."],
        )

    # Calcolo punteggio (8 dimensioni, 1 pt ciascuna)
    dimensions = [
        ("shared_customer",           shared_customer),
        ("shared_market",             shared_market),
        ("shared_problem_space",      shared_problem_space),
        ("shared_capabilities",       shared_capabilities),
        ("shared_distribution",       shared_distribution),
        ("shared_business_model",     shared_business_model),
        ("shared_data_boundary",      shared_data_boundary),
        ("shared_regulatory_boundary",shared_regulatory_boundary),
    ]
    raw_score = sum(1 for _, v in dimensions if v)
    coherence_score = round(raw_score / 8.0, 3)

    # Conflitti deterministici
    if not shared_customer and not shared_market:
        conflicts.append(
            "Clienti e mercati diversi: le Dedicated Mercury devono essere separate "
            "per non disperdere il focus strategico."
        )
    if not shared_business_model:
        conflicts.append(
            "Business model incompatibile tra i prodotti: "
            "governance e ciclo economico non possono essere condivisi."
        )
    if not shared_data_boundary:
        conflicts.append(
            "Dati non condivisibili: i boundary dei dati sono incompatibili. "
            "Istanze separate prevengono violazioni di isolamento."
        )
    if not shared_regulatory_boundary:
        conflicts.append(
            "Vincoli normativi incompatibili: le istanze devono essere isolate "
            "per garantire conformità indipendente."
        )

    # Warnings non bloccanti
    if not shared_capabilities:
        warnings.append(
            "Capability non condivise: verificare se il bundle è effettivamente portabile "
            "in una singola istanza."
        )

    # Raccomandazione
    # Override da conflitti deterministici
    hard_separate = (
        (not shared_customer and not shared_market)
        or not shared_business_model
        or not shared_data_boundary
        or not shared_regulatory_boundary
    )

    if hard_separate:
        recommendation = FamilyRecommendation.SEPARATE_INSTANCES
    elif raw_score >= 7:
        recommendation = FamilyRecommendation.SINGLE_DEDICATED_MERCURY
    elif raw_score >= 4:
        recommendation = FamilyRecommendation.MANUAL_REVIEW
    else:
        recommendation = FamilyRecommendation.SEPARATE_INSTANCES

    return ProductFamilyAssessment(
        assessment_id=_new_id(),
        product_ids=product_ids,
        shared_customer=shared_customer,
        shared_market=shared_market,
        shared_problem_space=shared_problem_space,
        shared_capabilities=shared_capabilities,
        shared_distribution=shared_distribution,
        shared_business_model=shared_business_model,
        shared_data_boundary=shared_data_boundary,
        shared_regulatory_boundary=shared_regulatory_boundary,
        coherence_score=coherence_score,
        conflicts=conflicts,
        recommendation=recommendation,
        evaluated_at=now,
        evidence_refs=evidence_refs or [],
        warnings=warnings,
    )

"""Schemi tipizzati (Structured Outputs) per OGNI operazione del provider AI reale
che si aspetta dati machine-readable.

Ogni schema qui sotto è usato con `client.responses.parse(..., text_format=<Schema>)`
dell'SDK ufficiale `openai`, con enforcement stretto (`strict=True`, applicato
automaticamente dall'SDK per i modelli Pydantic). Il parsing avviene SEMPRE tramite
l'SDK (`response.output_parsed`): nessuna estrazione di JSON da testo libero, nessun
tentativo di "indovinare" un JSON malformato.

Questo modulo NON contiene alcuna logica di chiamata HTTP o di gestione errori
del provider: quella resta interamente dentro `real_provider.py`, come richiesto
("Keep all OpenAI-specific response handling inside the provider adapter").
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ConnectivityCheckResult(BaseModel):
    """Schema minimo per il comando CLI `check-provider` (V0.2, invariato)."""

    status: Literal["ok"]
    message: str


class PlanSchema(BaseModel):
    """Piano strutturato per un obiettivo (usato da `propose_plan`/`decompose_goal`)."""

    objective: str
    steps: list[str]
    expected_files: list[str]
    verification_criteria: list[str]
    risk_notes: list[str]


class PatchFileOperation(BaseModel):
    """Una singola operazione su un file, proposta dal Builder AI.

    `content` è obbligatorio (può essere `None` SOLO per `operation="delete"`,
    dove non ha senso un contenuto): lo schema è strict, quindi il modello deve
    fornire esplicitamente `null`, non può semplicemente omettere il campo.
    """

    path: str
    operation: Literal["create", "update", "delete"]
    content: str | None
    rationale: str
    verification_relevance: str


class PatchSchema(BaseModel):
    """Patch strutturata per un task (usata da `propose_patch`)."""

    summary: str
    files: list[PatchFileOperation]
    test_files: list[PatchFileOperation]


class EvaluationSchema(BaseModel):
    """Valutazione strutturata SUPPLEMENTARE di un esito di test.

    Nota di design: questo schema NON sostituisce mai il giudizio pass/fail,
    che resta deciso esclusivamente da codice deterministico a partire
    dall'esecuzione REALE di pytest (`Evaluator`/`ExecutionLoop`). Serve solo a
    produrre un riepilogo strutturato leggibile da umani/audit accanto
    all'esito reale (vedi `OpenAICompatibleProvider.propose_evaluation`).
    """

    passed: bool
    failures: list[str]
    evidence: list[str]
    retry_recommendation: str

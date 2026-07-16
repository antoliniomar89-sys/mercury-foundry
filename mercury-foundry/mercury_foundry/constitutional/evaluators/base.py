"""Interfaccia base per gli evaluator dei principi costituzionali.

Ogni evaluator valuta un solo principio rispetto a una richiesta.
Il risultato è sempre deterministic e machine-readable:
  - applicable=False → il principio non si applica a questa richiesta
  - passed=True      → il principio è rispettato
  - passed=False     → il principio è violato (o dati mancanti)
  - warning          → la condizione è borderline o da monitorare
  - data_missing     → il dato necessario non era presente nella richiesta
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from mercury_foundry.constitutional.models import (
    ConstitutionalPrinciple,
    ConstitutionalValidationRequest,
    PrincipleEvaluationDetail,
)


class PrincipleEvaluator(ABC):
    """Valuta un singolo principio costituzionale in modo deterministico."""

    @property
    @abstractmethod
    def principle_id(self) -> str:
        """ID del principio che questo evaluator gestisce."""
        ...

    @abstractmethod
    def evaluate(
        self,
        principle: ConstitutionalPrinciple,
        request: ConstitutionalValidationRequest,
    ) -> PrincipleEvaluationDetail:
        """Valuta il principio rispetto alla richiesta.

        Non solleva mai eccezioni: i casi di dati mancanti o non applicabili
        vengono espressi tramite i campi `applicable`, `data_missing` e
        `passed=None`.
        """
        ...

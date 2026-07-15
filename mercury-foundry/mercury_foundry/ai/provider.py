"""Interfaccia AIProvider — sostituibile.

Il Builder dipende SOLO da questa interfaccia, mai da un'implementazione
concreta. Per collegare un provider reale in futuro basta implementare
`AIProvider` e selezionarlo in `provider_factory.get_provider`: nessun altro
componente (Orchestrator, Evaluator, Execution Loop, Approval Gate) deve
cambiare.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FileChange:
    """Una singola modifica di file proposta dal provider AI."""

    path: str  # percorso relativo alla sandbox (target_project/)
    content: str


@dataclass
class PatchProposal:
    """Patch/diff ispezionabile proposta dal provider AI per un task."""

    summary: str
    files: list[FileChange] = field(default_factory=list)
    test_files: list[FileChange] = field(default_factory=list)
    provider_name: str = ""
    is_simulated: bool = True


@dataclass
class ProviderCallRecord:
    """Metadata di UNA invocazione del provider AI, per persistenza/audit.

    Popolato dai provider reali dopo ogni chiamata (riuscita o fallita).
    I provider simulati (FakeModel) non fanno alcuna chiamata esterna e
    lasciano `last_call_record = None`: nulla viene registrato come se fosse
    una chiamata reale.
    """

    provider_name: str
    model: str | None
    is_simulated: bool
    operation: str  # "PLAN" | "PATCH" | "EVALUATION" | "CONNECTIVITY_CHECK" — per l'audit trail persistito
    call_number: int
    requested_at: str
    responded_at: str | None
    success: bool
    usage: dict[str, Any] | None = None
    estimated_cost_usd: float | None = None
    error_summary: str | None = None  # SEMPRE già redatto: mai segreti/prompt completi


class AIProvider(ABC):
    """Interfaccia che ogni provider (reale o simulato) deve implementare."""

    name: str = "unnamed-provider"
    is_simulated: bool = True

    # Impostato dai provider reali dopo ogni propose_plan/propose_patch.
    # None per i provider simulati o prima della prima chiamata.
    last_call_record: ProviderCallRecord | None = None

    @abstractmethod
    def propose_plan(self, goal_description: str) -> list[str]:
        """Ritorna una lista ordinata di descrizioni di task per l'obiettivo."""

    @abstractmethod
    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        """Ritorna una patch (file da creare/modificare) per il task.

        `context` include almeno: attempt_number (int), previous_failure (str|None).
        """

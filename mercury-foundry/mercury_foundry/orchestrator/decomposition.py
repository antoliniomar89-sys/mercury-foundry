"""Scomposizione dell'obiettivo in task ordinati — regole deterministiche in V0.

Nota V0: la scomposizione è volutamente semplice (un task per capability
riconosciuta). L'evoluzione naturale è arricchire le regole qui, senza
toccare Orchestrator/Builder/Evaluator.
"""

from __future__ import annotations

from mercury_foundry.ai.provider import AIProvider


def decompose_goal(goal_description: str, ai_provider: AIProvider) -> list[str]:
    """Chiede al provider AI un piano di task ordinati per l'obiettivo.

    La scomposizione stessa passa dal provider (sostituibile), ma le
    transizioni di stato che ne conseguono restano gestite in modo
    deterministico dall'Orchestrator/Execution Loop.
    """
    plan = ai_provider.propose_plan(goal_description)
    if not plan:
        raise ValueError("Il provider AI ha restituito un piano vuoto per l'obiettivo.")
    return plan

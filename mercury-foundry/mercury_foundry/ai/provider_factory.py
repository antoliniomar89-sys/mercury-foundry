"""Selezione del provider AI da usare — punto unico di sostituzione.

Per collegare un provider reale in futuro (es. Anthropic/OpenAI) basta
aggiungere un'implementazione di AIProvider e restituirla qui in base alla
configurazione (env var / parametro CLI). Nessun altro modulo deve cambiare.
"""

from __future__ import annotations

import os
from typing import Callable

from mercury_foundry.ai.fake_model import FakeModel
from mercury_foundry.ai.provider import AIProvider


class ProviderUnavailableError(RuntimeError):
    """Sollevata quando un provider è sconosciuto o non configurabile in modo sicuro.

    Design esplicito: NON esiste alcun percorso di codice che, a fronte di un
    provider sconosciuto o mal configurato, ritorni silenziosamente FakeModel.
    Se il provider richiesto non è disponibile, l'esecuzione DEVE fermarsi qui.
    """


# Registry esplicito dei provider disponibili in questa istanza. Aggiungere un
# provider reale (es. Anthropic/OpenAI) significa aggiungere una entry qui,
# nient'altro: Orchestrator/Builder/Evaluator/ExecutionLoop/ApprovalGate non
# devono cambiare. `is_simulated` è dichiarato qui in modo esplicito e
# indipendente dall'implementazione, per poter essere ispezionato anche senza
# istanziare il provider (usato da `doctor` e dai test).
PROVIDER_REGISTRY: dict[str, Callable[[], AIProvider]] = {
    "fake": FakeModel,
}

SIMULATED_PROVIDER_NAMES: frozenset[str] = frozenset({"fake"})


def list_available_providers() -> list[str]:
    return sorted(PROVIDER_REGISTRY.keys())


def resolve_provider_name(name: str | None = None) -> str:
    """Determina quale nome di provider sarebbe usato, senza istanziarlo."""
    return (name or os.environ.get("MERCURY_AI_PROVIDER") or "fake").strip().lower()


def get_provider(name: str | None = None) -> AIProvider:
    """Ritorna l'AIProvider configurato, o si ferma con un errore esplicito.

    In questa istanza di V0.1 non è disponibile alcuna chiave API di un
    provider reale (integrazione Replit rifiutata dall'utente, nessuna
    chiave propria fornita): l'unico provider disponibile è il FakeModel
    deterministico, usato esclusivamente per esercitare realmente il ciclo
    di esecuzione con test reali. Non genera mai output che finga di
    provenire da un vero modello AI.

    Se viene richiesto un provider non presente in `PROVIDER_REGISTRY`,
    l'esecuzione si ferma con `ProviderUnavailableError`: nessun fallback
    implicito a FakeModel.
    """
    provider_name = resolve_provider_name(name)

    factory = PROVIDER_REGISTRY.get(provider_name)
    if factory is None:
        raise ProviderUnavailableError(
            f"Provider AI '{provider_name}' non riconosciuto o non configurato in questa "
            f"istanza (nessuna chiave API disponibile per provider reali). "
            f"Provider disponibili: {', '.join(list_available_providers())}. "
            "Per collegare un provider reale, implementare AIProvider e registrarlo in "
            "PROVIDER_REGISTRY — non esiste un fallback automatico a FakeModel."
        )

    provider = factory()

    # Validazione di coerenza: un provider nel gruppo "simulato" deve
    # dichiararsi is_simulated=True, e viceversa. Se non coincide, è una
    # configurazione inconsistente e va segnalata subito, non silenziata.
    expected_simulated = provider_name in SIMULATED_PROVIDER_NAMES
    if provider.is_simulated != expected_simulated:
        raise ProviderUnavailableError(
            f"Configurazione inconsistente per il provider '{provider_name}': "
            f"is_simulated={provider.is_simulated} ma il registro lo classifica come "
            f"{'simulato' if expected_simulated else 'reale'}. Esecuzione interrotta "
            "per evitare di presentare un risultato simulato come reale (o viceversa)."
        )

    return provider

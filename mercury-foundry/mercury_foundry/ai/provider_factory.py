"""Selezione del provider AI da usare — punto unico di sostituzione.

Per collegare un provider reale in futuro (es. Anthropic/OpenAI) basta
aggiungere un'implementazione di AIProvider e restituirla qui in base alla
configurazione (env var / parametro CLI). Nessun altro modulo deve cambiare.
"""

from __future__ import annotations

import os

from mercury_foundry.ai.fake_model import FakeModel
from mercury_foundry.ai.provider import AIProvider


class ProviderUnavailableError(RuntimeError):
    pass


def get_provider(name: str | None = None) -> AIProvider:
    """Ritorna l'AIProvider configurato.

    In questa istanza di V0 non è disponibile alcuna chiave API di un
    provider reale (integrazione Replit rifiutata dall'utente, nessuna
    chiave propria fornita): l'unico provider disponibile è il FakeModel
    deterministico, usato esclusivamente per esercitare realmente il ciclo
    di esecuzione con test reali. Non genera mai output che finga di
    provenire da un vero modello AI.
    """
    provider_name = (name or os.environ.get("MERCURY_AI_PROVIDER") or "fake").lower()

    if provider_name == "fake":
        return FakeModel()

    raise ProviderUnavailableError(
        f"Provider AI '{provider_name}' non configurato in questa istanza: "
        "nessuna chiave API disponibile. Usa 'fake' oppure implementa e collega "
        "un nuovo AIProvider in mercury_foundry/ai/."
    )

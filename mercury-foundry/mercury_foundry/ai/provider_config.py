"""Configurazione del provider AI reale — SOLO da variabili d'ambiente / Replit Secrets.

Nessun valore di default per credenziali, modello, endpoint o costi: se lo
sviluppatore vuole usare un provider reale, deve fornire ogni valore in modo
esplicito. Questo modulo non fa mai una chiamata di rete.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Nomi delle variabili d'ambiente / secret attese per il provider reale.
ENV_API_KEY = "MERCURY_AI_API_KEY"
ENV_MODEL = "MERCURY_AI_MODEL"
ENV_BASE_URL = "MERCURY_AI_API_BASE_URL"
ENV_TIMEOUT_SECONDS = "MERCURY_AI_TIMEOUT_SECONDS"
ENV_MAX_CALLS_PER_RUN = "MERCURY_AI_MAX_CALLS_PER_RUN"
ENV_MAX_TOKENS_PER_RUN = "MERCURY_AI_MAX_TOKENS_PER_RUN"
ENV_MAX_COST_USD_PER_RUN = "MERCURY_AI_MAX_COST_USD_PER_RUN"
ENV_COST_PER_1K_TOKENS_USD = "MERCURY_AI_COST_PER_1K_TOKENS_USD"  # opzionale

REQUIRED_ENV_VARS = (
    ENV_API_KEY,
    ENV_MODEL,
    ENV_BASE_URL,
    ENV_TIMEOUT_SECONDS,
    ENV_MAX_CALLS_PER_RUN,
    ENV_MAX_TOKENS_PER_RUN,
    ENV_MAX_COST_USD_PER_RUN,
)


class ProviderConfigError(ValueError):
    """Una o più variabili di configurazione del provider reale sono mancanti o invalide."""


@dataclass(frozen=True)
class RealProviderConfig:
    api_key: str
    model: str
    base_url: str
    timeout_seconds: float
    max_calls_per_run: int
    max_tokens_per_run: int
    max_cost_usd_per_run: float
    cost_per_1k_tokens_usd: float | None = None

    def redacted_dict(self) -> dict:
        """Rappresentazione sicura per log/diagnostica: MAI la api_key in chiaro."""
        return {
            "api_key": "***configurata***" if self.api_key else "***mancante***",
            "model": self.model,
            "base_url": self.base_url,
            "timeout_seconds": self.timeout_seconds,
            "max_calls_per_run": self.max_calls_per_run,
            "max_tokens_per_run": self.max_tokens_per_run,
            "max_cost_usd_per_run": self.max_cost_usd_per_run,
            "cost_per_1k_tokens_usd": self.cost_per_1k_tokens_usd,
        }


def missing_required_env_vars(env: dict | None = None) -> list[str]:
    """Elenca le variabili richieste assenti/vuote, senza leggerne il valore altrove."""
    source = env if env is not None else os.environ
    return [name for name in REQUIRED_ENV_VARS if not source.get(name)]


def load_real_provider_config(env: dict | None = None) -> RealProviderConfig:
    """Carica la configurazione del provider reale da env, o solleva ProviderConfigError.

    Fail-closed: qualunque valore obbligatorio mancante o non convertibile
    interrompe subito, con un messaggio che non include mai il valore delle
    credenziali.
    """
    source = env if env is not None else os.environ

    missing = missing_required_env_vars(source)
    if missing:
        raise ProviderConfigError(
            "Configurazione del provider AI reale incompleta: variabili mancanti o vuote: "
            f"{', '.join(missing)}."
        )

    try:
        timeout_seconds = float(source[ENV_TIMEOUT_SECONDS])
        max_calls_per_run = int(source[ENV_MAX_CALLS_PER_RUN])
        max_tokens_per_run = int(source[ENV_MAX_TOKENS_PER_RUN])
        max_cost_usd_per_run = float(source[ENV_MAX_COST_USD_PER_RUN])
    except (TypeError, ValueError) as exc:
        raise ProviderConfigError(
            f"Valori numerici di configurazione del provider AI reale non validi: {exc}"
        ) from exc

    cost_per_1k_raw = source.get(ENV_COST_PER_1K_TOKENS_USD)
    cost_per_1k_tokens_usd = float(cost_per_1k_raw) if cost_per_1k_raw else None

    if timeout_seconds <= 0 or max_calls_per_run <= 0 or max_tokens_per_run <= 0 or max_cost_usd_per_run <= 0:
        raise ProviderConfigError(
            "Timeout e limiti (chiamate/token/costo) del provider AI reale devono essere > 0."
        )

    return RealProviderConfig(
        api_key=source[ENV_API_KEY],
        model=source[ENV_MODEL],
        base_url=source[ENV_BASE_URL],
        timeout_seconds=timeout_seconds,
        max_calls_per_run=max_calls_per_run,
        max_tokens_per_run=max_tokens_per_run,
        max_cost_usd_per_run=max_cost_usd_per_run,
        cost_per_1k_tokens_usd=cost_per_1k_tokens_usd,
    )


def redact(text: str | None, *secrets: str) -> str | None:
    """Rimuove qualunque occorrenza dei segreti forniti da una stringa di log/errore."""
    if text is None:
        return None
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***REDACTED***")
    return redacted

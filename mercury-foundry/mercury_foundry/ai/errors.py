"""Eccezioni del livello provider AI — gerarchia esplicita per il blocco automatico.

Qualsiasi condizione qui sotto DEVE interrompere l'esecuzione del task in modo
sicuro (fail-closed): nessuna di queste viene mai silenziata o trasformata in
un fallback automatico verso FakeModel.
"""

from __future__ import annotations


class ProviderExecutionError(RuntimeError):
    """Classe base per tutti gli errori di esecuzione di un provider AI reale."""


class ProviderCredentialsMissingError(ProviderExecutionError):
    """Credenziali mancanti o vuote per il provider richiesto."""


class ProviderConfigurationError(ProviderExecutionError):
    """Configurazione del provider incompleta o incoerente (modello, base url, ecc.)."""


class ProviderTimeoutError(ProviderExecutionError):
    """La chiamata al provider ha superato il timeout configurato."""


class ProviderCallLimitExceededError(ProviderExecutionError):
    """Superato il numero massimo di chiamate consentite per questa run."""


class ProviderUsageBudgetExceededError(ProviderExecutionError):
    """Superato il budget massimo di token/usage per questa run."""


class ProviderCostBudgetExceededError(ProviderExecutionError):
    """Superato il costo stimato massimo consentito per questa run."""


class ProviderMalformedResponseError(ProviderExecutionError):
    """La risposta del provider non è nel formato atteso."""


class ProviderUnknownModelError(ProviderExecutionError):
    """Il provider ha segnalato che il modello richiesto non esiste/non è accessibile."""

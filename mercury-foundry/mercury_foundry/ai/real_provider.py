"""Primo provider AI reale: adapter OpenAI-compatibile (chat completions).

Nessuna credenziale, modello, endpoint o costo è hardcoded qui: tutto arriva
da `RealProviderConfig` (a sua volta caricata SOLO da env var / Replit
Secrets — vedi `provider_config.py`).

Testabilità: la funzione HTTP è iniettabile (`http_post`). I test devono
SEMPRE iniettare un mock: questo modulo non viene mai esercitato con una
chiamata di rete reale dalla suite automatica.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from mercury_foundry.ai.errors import (
    ProviderCallLimitExceededError,
    ProviderCostBudgetExceededError,
    ProviderMalformedResponseError,
    ProviderTimeoutError,
    ProviderUnknownModelError,
    ProviderUsageBudgetExceededError,
)
from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal, ProviderCallRecord
from mercury_foundry.ai.provider_config import RealProviderConfig, redact

HttpPostFn = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def _default_http_post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Implementazione reale via stdlib. Usata SOLO fuori dai test (mai mockata)."""
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        # Copre sia i timeout socket sia altri errori di rete: qui li normalizziamo
        # a TimeoutError, che il chiamante traduce in ProviderTimeoutError.
        raise TimeoutError(str(exc)) from exc
    return json.loads(raw)


class OpenAICompatibleProvider(AIProvider):
    """Provider reale, is_simulated=False. Implementa SOLO propose_plan/propose_patch.

    Ogni chiamata:
    - viene bloccata PRIMA di partire se supererebbe max_calls_per_run;
    - viene cronometrata (requested_at/responded_at);
    - alla fine aggiorna i contatori cumulativi di token/costo e blocca le
      chiamate SUCCESSIVE se il budget è superato;
    - popola sempre `last_call_record`, anche in caso di errore.
    """

    is_simulated = False

    def __init__(self, config: RealProviderConfig, *, http_post: HttpPostFn | None = None):
        self.config = config
        self.name = f"openai-compatible:{config.model}"
        self._http_post = http_post or _default_http_post
        self._call_count = 0
        self._tokens_used = 0
        self._cost_used_usd = 0.0

    def propose_plan(self, goal_description: str) -> list[str]:
        response_text = self._invoke(
            system_prompt=(
                "Sei il modulo di pianificazione di Mercury Foundry. Rispondi SOLO con un "
                "elenco JSON di stringhe, ciascuna una descrizione di task ordinato."
            ),
            user_prompt=goal_description,
        )
        try:
            plan = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise ProviderMalformedResponseError(
                f"Risposta del piano non è JSON valido: {exc}"
            ) from exc
        if not isinstance(plan, list) or not all(isinstance(item, str) for item in plan):
            raise ProviderMalformedResponseError(
                "Risposta del piano non è una lista JSON di stringhe."
            )
        return plan

    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        response_text = self._invoke(
            system_prompt=(
                "Sei il modulo Builder di Mercury Foundry. Rispondi SOLO con un JSON con "
                'chiavi "summary" (str), "files" (lista di {"path","content"}), '
                '"test_files" (lista di {"path","content"}).'
            ),
            user_prompt=json.dumps({"task_description": task_description, "context": context}),
        )
        try:
            payload = json.loads(response_text)
            summary = payload["summary"]
            files = [FileChange(path=f["path"], content=f["content"]) for f in payload.get("files", [])]
            test_files = [
                FileChange(path=f["path"], content=f["content"]) for f in payload.get("test_files", [])
            ]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProviderMalformedResponseError(
                f"Risposta della patch non è nel formato atteso: {exc}"
            ) from exc

        return PatchProposal(
            summary=summary,
            files=files,
            test_files=test_files,
            provider_name=self.name,
            is_simulated=False,
        )

    # -- meccanica interna di chiamata, budget e recording -----------------

    def _invoke(self, *, system_prompt: str, user_prompt: str) -> str:
        if self._call_count >= self.config.max_calls_per_run:
            raise ProviderCallLimitExceededError(
                f"Limite massimo di chiamate per run superato ({self.config.max_calls_per_run})."
            )

        self._call_count += 1
        call_number = self._call_count
        requested_at = _now()

        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"

        try:
            response = self._http_post(url, headers, body, self.config.timeout_seconds)
        except TimeoutError as exc:
            self._record_call(call_number, requested_at, success=False, error=str(exc))
            raise ProviderTimeoutError(
                f"Timeout dopo {self.config.timeout_seconds}s in attesa del provider AI."
            ) from exc

        responded_at = _now()

        error_obj = response.get("error") if isinstance(response, dict) else None
        if error_obj is not None:
            error_code = (error_obj or {}).get("code") or (error_obj or {}).get("type")
            message = redact((error_obj or {}).get("message", "errore provider"), self.config.api_key)
            if error_code in ("model_not_found", "invalid_model"):
                self._record_call(call_number, requested_at, success=False, error=message, responded_at=responded_at)
                raise ProviderUnknownModelError(
                    f"Modello '{self.config.model}' non riconosciuto dal provider: {message}"
                )
            self._record_call(call_number, requested_at, success=False, error=message, responded_at=responded_at)
            raise ProviderMalformedResponseError(f"Il provider ha risposto con un errore: {message}")

        try:
            content = response["choices"][0]["message"]["content"]
            usage = response.get("usage") or {}
        except (KeyError, IndexError, TypeError) as exc:
            self._record_call(
                call_number,
                requested_at,
                success=False,
                error=f"Struttura risposta inattesa: {exc}",
                responded_at=responded_at,
            )
            raise ProviderMalformedResponseError(f"Struttura della risposta del provider inattesa: {exc}") from exc

        total_tokens = int(usage.get("total_tokens") or 0)
        self._tokens_used += total_tokens
        call_cost = self._estimate_cost(total_tokens)
        self._cost_used_usd += call_cost or 0.0

        if self._tokens_used > self.config.max_tokens_per_run:
            self._record_call(
                call_number, requested_at, success=False, error="usage budget exceeded",
                responded_at=responded_at, usage=usage, estimated_cost_usd=call_cost,
            )
            raise ProviderUsageBudgetExceededError(
                f"Budget token per run superato: usati {self._tokens_used}, "
                f"limite {self.config.max_tokens_per_run}."
            )
        if self._cost_used_usd > self.config.max_cost_usd_per_run:
            self._record_call(
                call_number, requested_at, success=False, error="cost budget exceeded",
                responded_at=responded_at, usage=usage, estimated_cost_usd=call_cost,
            )
            raise ProviderCostBudgetExceededError(
                f"Budget di costo stimato per run superato: stimati ${self._cost_used_usd:.4f}, "
                f"limite ${self.config.max_cost_usd_per_run:.4f}."
            )

        self._record_call(
            call_number, requested_at, success=True, responded_at=responded_at,
            usage=usage, estimated_cost_usd=call_cost,
        )
        return content

    def _estimate_cost(self, total_tokens: int) -> float | None:
        if self.config.cost_per_1k_tokens_usd is None:
            return None
        return (total_tokens / 1000.0) * self.config.cost_per_1k_tokens_usd

    def _record_call(
        self,
        call_number: int,
        requested_at: str,
        *,
        success: bool,
        error: str | None = None,
        responded_at: str | None = None,
        usage: dict | None = None,
        estimated_cost_usd: float | None = None,
    ) -> None:
        self.last_call_record = ProviderCallRecord(
            provider_name=self.name,
            model=self.config.model,
            is_simulated=False,
            call_number=call_number,
            requested_at=requested_at,
            responded_at=responded_at or _now(),
            success=success,
            usage=usage,
            estimated_cost_usd=estimated_cost_usd,
            error_summary=redact(error, self.config.api_key),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

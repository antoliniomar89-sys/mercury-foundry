"""Primo provider AI reale: adapter OpenAI-compatibile.

Nessuna credenziale, modello, endpoint o costo è hardcoded qui: tutto arriva
da `RealProviderConfig` (a sua volta caricata SOLO da env var / Replit
Secrets — vedi `provider_config.py`).

Due meccaniche di chiamata coesistono in questo adapter, entrambe isolate
qui dentro (nessun altro componente di Mercury Foundry sa quale delle due
sia in uso):

- `propose_plan`/`propose_patch` (usate dal ciclo Foundry completo):
  chat completions "grezze" via `http_post` iniettabile, parsing manuale
  del JSON nel testo libero della risposta. Non toccate da questa modifica.
- `check_connectivity` (usata SOLO dal comando CLI `check-provider`):
  Responses API dell'SDK ufficiale `openai`, con Structured Outputs a
  schema JSON stretto (`strict=True`). Il parsing avviene tramite l'SDK
  (`response.output_parsed`), MAI estraendo JSON da testo libero.

Testabilità: sia `http_post` sia il client `openai` sono iniettabili. I test
devono SEMPRE iniettare un mock (per `check_connectivity`, un client con un
trasporto HTTP fittizio — vedi `tests/test_real_provider.py`): questo modulo
non viene mai esercitato con una chiamata di rete reale dalla suite
automatica.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from mercury_foundry.ai.errors import (
    ProviderCallLimitExceededError,
    ProviderCostBudgetExceededError,
    ProviderIncompleteResponseError,
    ProviderMalformedResponseError,
    ProviderRefusalError,
    ProviderTimeoutError,
    ProviderUnknownModelError,
    ProviderUsageBudgetExceededError,
)
from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal, ProviderCallRecord
from mercury_foundry.ai.provider_config import RealProviderConfig, redact

HttpPostFn = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


class ConnectivityCheckResult(BaseModel):
    """Schema minimo richiesto dal comando CLI `check-provider`.

    Volutamente il più piccolo possibile: serve solo a dimostrare che il
    provider reale rispetta Structured Outputs con schema stretto, non a
    trasportare informazioni applicative.
    """

    status: Literal["ok"]
    message: str


CHECK_PROVIDER_SCHEMA_NAME = "connectivity_check_result"


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

    def __init__(
        self,
        config: RealProviderConfig,
        *,
        http_post: HttpPostFn | None = None,
        client: OpenAI | None = None,
    ):
        self.config = config
        self.name = f"openai-compatible:{config.model}"
        self._http_post = http_post or _default_http_post
        # Client SDK ufficiale, usato SOLO da `check_connectivity`. Costruirlo
        # non esegue alcuna chiamata di rete: nessun costo/effetto finché un
        # metodo non lo invoca esplicitamente.
        self._client = client or OpenAI(
            api_key=config.api_key, base_url=config.base_url, timeout=config.timeout_seconds
        )
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

    def check_connectivity(self, prompt: str) -> dict[str, str]:
        """UNA chiamata reale via Responses API + Structured Outputs stretti.

        Isolata da `propose_plan`/`propose_patch`: usata solo dal comando CLI
        `check-provider`. Ritorna `{"status": "ok", "message": ...}` SOLO se
        il modello ha prodotto un output conforme allo schema stretto
        (`ConnectivityCheckResult`), analizzato dall'SDK ufficiale — nessuna
        estrazione di JSON da testo libero. Qualunque rifiuto, risposta
        incompleta, risposta malformata o modello non supportato per gli
        structured output blocca fail-closed (nessun retry silenzioso).
        """
        call_number = self._check_call_budget()
        requested_at = _now()

        try:
            response = self._client.responses.parse(
                model=self.config.model,
                input=[{"role": "user", "content": prompt}],
                text_format=ConnectivityCheckResult,
                timeout=self.config.timeout_seconds,
            )
        except openai.APITimeoutError as exc:
            message = redact(str(exc), self.config.api_key)
            self._record_call(call_number, requested_at, success=False, error=message)
            raise ProviderTimeoutError(
                f"Timeout dopo {self.config.timeout_seconds}s in attesa del provider AI (Responses API)."
            ) from exc
        except openai.APIStatusError as exc:
            message = redact(str(exc), self.config.api_key)
            self._record_call(call_number, requested_at, success=False, error=message)
            if _looks_like_unsupported_model(exc, message):
                raise ProviderUnknownModelError(
                    f"Modello '{self.config.model}' non supportato per gli structured output "
                    f"del provider (Responses API): {message}"
                ) from exc
            raise ProviderMalformedResponseError(
                f"Il provider ha risposto con un errore: {message}"
            ) from exc
        except openai.APIError as exc:
            message = redact(str(exc), self.config.api_key)
            self._record_call(call_number, requested_at, success=False, error=message)
            raise ProviderMalformedResponseError(
                f"Errore di comunicazione con il provider: {message}"
            ) from exc
        except ValidationError as exc:
            # L'SDK ha ricevuto una risposta HTTP valida, ma il testo generato dal
            # modello non è JSON conforme allo schema stretto richiesto: l'SDK
            # stesso (non il nostro codice) ha tentato e fallito il parsing.
            message = redact(str(exc), self.config.api_key)
            self._record_call(call_number, requested_at, success=False, error=message)
            raise ProviderMalformedResponseError(
                f"Il provider non ha prodotto un output conforme allo schema JSON stretto richiesto: {message}"
            ) from exc

        responded_at = _now()
        usage = _responses_usage_dict(getattr(response, "usage", None))

        refusal_text = _find_refusal_text(response)
        if refusal_text is not None:
            message = redact(refusal_text, self.config.api_key)
            self._record_call(
                call_number, requested_at, success=False, error=message,
                responded_at=responded_at, usage=usage,
            )
            raise ProviderRefusalError(f"Il provider ha rifiutato la richiesta: {message}")

        if response.status == "incomplete":
            reason = response.incomplete_details.reason if response.incomplete_details else None
            self._record_call(
                call_number, requested_at, success=False,
                error=f"risposta incompleta (motivo: {reason})",
                responded_at=responded_at, usage=usage,
            )
            raise ProviderIncompleteResponseError(
                f"Risposta incompleta dal provider (motivo: {reason})."
            )

        if response.status not in (None, "completed"):
            message = redact(
                str(response.error) if response.error else f"status inatteso: {response.status}",
                self.config.api_key,
            )
            self._record_call(
                call_number, requested_at, success=False, error=message,
                responded_at=responded_at, usage=usage,
            )
            raise ProviderMalformedResponseError(f"Il provider ha risposto con stato inatteso: {message}")

        parsed = response.output_parsed
        if parsed is None:
            self._record_call(
                call_number, requested_at, success=False,
                error="output_parsed è None: risposta non conforme allo schema JSON stretto",
                responded_at=responded_at, usage=usage,
            )
            raise ProviderMalformedResponseError(
                "Il provider non ha prodotto un output conforme allo schema JSON stretto richiesto."
            )

        call_cost = self._apply_usage_budget(call_number, requested_at, responded_at, usage)

        self._record_call(
            call_number, requested_at, success=True, responded_at=responded_at,
            usage=usage, estimated_cost_usd=call_cost,
        )
        return {"status": parsed.status, "message": parsed.message}

    # -- meccanica interna di chiamata, budget e recording -----------------

    def _check_call_budget(self) -> int:
        if self._call_count >= self.config.max_calls_per_run:
            raise ProviderCallLimitExceededError(
                f"Limite massimo di chiamate per run superato ({self.config.max_calls_per_run})."
            )
        self._call_count += 1
        return self._call_count

    def _apply_usage_budget(
        self, call_number: int, requested_at: str, responded_at: str, usage: dict | None
    ) -> float | None:
        """Aggiorna i contatori cumulativi e blocca fail-closed se un budget è superato.

        Condivisa da `_invoke` e `check_connectivity`: stessa logica di budget
        per qualunque meccanica di chiamata usi questo adapter.
        """
        usage = usage or {}
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
        return call_cost

    def _invoke(self, *, system_prompt: str, user_prompt: str) -> str:
        call_number = self._check_call_budget()
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

        call_cost = self._apply_usage_budget(call_number, requested_at, responded_at, usage)

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


def _responses_usage_dict(usage: Any) -> dict[str, int]:
    """Traduce `response.usage` (Responses API) in un dict semplice per persistenza/log."""
    if usage is None:
        return {}
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _find_refusal_text(response: Any) -> str | None:
    """Cerca un content item di tipo 'refusal' nei messaggi di output.

    La Responses API rappresenta un rifiuto del modello come un content item
    dedicato (non come testo libero da interpretare): qui lo individuiamo
    senza mai tentare di dedurre un rifiuto da un parsing di testo.
    """
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return getattr(content, "refusal", None) or "refusal"
    return None


def _looks_like_unsupported_model(exc: Exception, message: str) -> bool:
    """Distingue un errore 'modello non supportato/non trovato' dagli altri errori HTTP.

    Usato SOLO per instradare l'eccezione verso `ProviderUnknownModelError`
    invece del generico `ProviderMalformedResponseError`; non altera il
    comportamento fail-closed in nessun caso (entrambi bloccano comunque).
    """
    status_code = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    error_code = None
    if isinstance(body, dict):
        error_code = (body.get("error") or {}).get("code") or (body.get("error") or {}).get("type")
    if error_code in ("model_not_found", "invalid_model"):
        return True
    if status_code == 404:
        return True
    lowered = (message or "").lower()
    return "model" in lowered and any(
        keyword in lowered
        for keyword in ("not found", "does not exist", "does not support", "unsupported", "not_found")
    )

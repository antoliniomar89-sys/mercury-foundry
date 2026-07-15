"""Primo provider AI reale: adapter OpenAI-compatibile.

Nessuna credenziale, modello, endpoint o costo è hardcoded qui: tutto arriva
da `RealProviderConfig` (a sua volta caricata SOLO da env var / Replit
Secrets — vedi `provider_config.py`).

Un'UNICA meccanica di chiamata è usata per TUTTE le operazioni che si
aspettano dati machine-readable (`propose_plan`, `propose_patch`,
`propose_evaluation`, `check_connectivity`): Responses API dell'SDK
ufficiale `openai`, con Structured Outputs a schema JSON stretto
(`strict=True`, applicato automaticamente dall'SDK sui modelli Pydantic in
`schemas.py`). Il parsing avviene SEMPRE tramite l'SDK
(`response.output_parsed`), MAI estraendo JSON da testo libero: non esiste
più, in questo modulo, alcun meccanismo di chat-completions "grezze" con
parsing manuale — è stato rimosso perché è esattamente ciò che aveva causato
il fallimento fail-closed della prima run reale controllata (risposta di
piano non JSON).

Testabilità: il client `openai` è SEMPRE iniettabile. I test devono SEMPRE
iniettare un client con un trasporto HTTP fittizio (`httpx.MockTransport` —
vedi `tests/test_real_provider.py` e `tests/test_check_provider_structured_output.py`):
questo modulo non viene mai esercitato con una chiamata di rete reale dalla
suite automatica.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, TypeVar

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
    ProviderUnsafePatchError,
    ProviderUsageBudgetExceededError,
)
from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal, ProviderCallRecord
from mercury_foundry.ai.provider_config import RealProviderConfig, redact
from mercury_foundry.ai.schemas import (
    ConnectivityCheckResult,
    EvaluationSchema,
    PatchFileOperation,
    PatchSchema,
    PlanSchema,
)

CHECK_PROVIDER_SCHEMA_NAME = "connectivity_check_result"

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class OpenAICompatibleProvider(AIProvider):
    """Provider reale, is_simulated=False.

    Implementa l'interfaccia `AIProvider` (`propose_plan`/`propose_patch`) più due
    operazioni supplementari specifiche di questo adapter, non parte
    dell'interfaccia sostituibile perché non usate dal ciclo deterministico
    dell'Execution Loop:
    - `check_connectivity`: SOLO dal comando CLI `check-provider`;
    - `propose_evaluation`: valutazione strutturata SUPPLEMENTARE di un esito di
      test, MAI usata per decidere pass/fail (quella decisione resta sempre
      deterministica, a partire dall'esecuzione reale di pytest).

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
        client: OpenAI | None = None,
    ):
        self.config = config
        self.name = f"openai-compatible:{config.model}"
        # Client SDK ufficiale, unico canale di chiamata di questo adapter.
        # Costruirlo non esegue alcuna chiamata di rete: nessun costo/effetto
        # finché un metodo non lo invoca esplicitamente.
        self._client = client or OpenAI(
            api_key=config.api_key, base_url=config.base_url, timeout=config.timeout_seconds
        )
        self._call_count = 0
        self._tokens_used = 0
        self._cost_used_usd = 0.0

    # -- operazioni dell'interfaccia AIProvider -----------------------------

    def propose_plan(self, goal_description: str) -> list[str]:
        """Piano strutturato (Structured Outputs, `PlanSchema`) per l'obiettivo.

        Ritorna SOLO la lista ordinata di step (contratto richiesto da
        `decompose_goal`/`AIProvider`): le altre informazioni dello schema
        (expected_files, verification_criteria, risk_notes) sono comunque
        richieste e validate strettamente dal modello, ma non transitano oggi
        verso l'Execution Loop per non redesignare le sue transizioni di stato.
        """
        parsed = self._structured_call(
            input_messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei il modulo di pianificazione di Mercury Foundry. Rispondi SOLO con "
                        "un piano strutturato per l'obiettivo indicato: un obiettivo riformulato, "
                        "un elenco ordinato di step, i file che ti aspetti di creare o modificare, "
                        "i criteri con cui verificare il completamento, e note di rischio."
                    ),
                },
                {"role": "user", "content": goal_description},
            ],
            text_format=PlanSchema,
            operation="PLAN",
        )
        if not parsed.steps:
            raise ProviderIncompleteResponseError(
                "Il piano strutturato del provider non contiene alcuno step."
            )
        return list(parsed.steps)

    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        """Patch strutturata (Structured Outputs, `PatchSchema`) per il task.

        `context` può opzionalmente contenere `max_files` (int): se presente,
        il numero totale di operazioni sui file (principali + di test) che
        superi questo limite viene rifiutato fail-closed
        (`ProviderUnsafePatchError`), invece di essere applicato parzialmente.
        """
        parsed = self._structured_call(
            input_messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei il modulo Builder di Mercury Foundry. Rispondi SOLO con una patch "
                        'strutturata: un riepilogo, e un elenco di operazioni sui file "files" e '
                        '"test_files", ciascuna con path relativo alla sandbox, operation '
                        '(create/update/delete), il contenuto COMPLETO del file (null solo per '
                        "delete), una motivazione e la rilevanza per la verifica."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"task_description": task_description, "context": context}),
                },
            ],
            text_format=PatchSchema,
            operation="PATCH",
        )

        max_files = context.get("max_files") if isinstance(context, dict) else None
        files = _validate_and_convert_operations(parsed.files, max_files=None)
        test_files = _validate_and_convert_operations(parsed.test_files, max_files=None)
        _enforce_max_files(len(files) + len(test_files), max_files=max_files)

        return PatchProposal(
            summary=parsed.summary,
            files=files,
            test_files=test_files,
            provider_name=self.name,
            is_simulated=False,
        )

    # -- operazioni supplementari specifiche di questo adapter --------------

    def check_connectivity(self, prompt: str) -> dict[str, str]:
        """UNA chiamata reale via Responses API + Structured Outputs stretti.

        Isolata dalle altre operazioni solo nel senso che è usata unicamente
        dal comando CLI `check-provider`: la meccanica di chiamata sottostante
        (`_structured_call`) è la STESSA usata da `propose_plan`/`propose_patch`.
        Qualunque rifiuto, risposta incompleta, risposta malformata o modello
        non supportato per gli structured output blocca fail-closed (nessun
        retry silenzioso).
        """
        parsed = self._structured_call(
            input_messages=[{"role": "user", "content": prompt}],
            text_format=ConnectivityCheckResult,
            operation="CONNECTIVITY_CHECK",
        )
        return {"status": parsed.status, "message": parsed.message}

    def propose_evaluation(self, *, task_description: str, test_output: str) -> dict[str, Any]:
        """Valutazione strutturata SUPPLEMENTARE (Structured Outputs, `EvaluationSchema`).

        NON sostituisce mai il giudizio pass/fail deterministico dell'Evaluator
        (basato sull'esecuzione reale di pytest): questo metodo produce solo un
        riepilogo strutturato leggibile (fallimenti, evidenze, raccomandazione
        di retry) accanto all'esito reale, per audit/reporting futuro. Non è
        parte dell'interfaccia `AIProvider` e non è invocato dall'Execution
        Loop.
        """
        parsed = self._structured_call(
            input_messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei il modulo di valutazione di Mercury Foundry. Rispondi SOLO con una "
                        "valutazione strutturata (passed, failures, evidence, retry_recommendation) "
                        "del seguente esito REALE di esecuzione dei test. Non stai decidendo se il "
                        "task è approvato: stai solo riassumendo l'esito già osservato."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"task_description": task_description, "test_output": test_output}
                    ),
                },
            ],
            text_format=EvaluationSchema,
            operation="EVALUATION",
        )
        return {
            "passed": parsed.passed,
            "failures": list(parsed.failures),
            "evidence": list(parsed.evidence),
            "retry_recommendation": parsed.retry_recommendation,
        }

    # -- meccanica interna di chiamata, budget e recording -----------------

    def _structured_call(
        self, *, input_messages: list[dict], text_format: type[SchemaT], operation: str
    ) -> SchemaT:
        """Esegue UNA chiamata Structured Outputs e ritorna l'output tipizzato/parsato.

        Condivisa da `propose_plan`, `propose_patch`, `check_connectivity` e
        `propose_evaluation`: stessa logica di budget, timeout, rifiuto,
        risposta incompleta e schema mismatch per qualunque operazione usi
        questo adapter. Non fa MAI fallback a parsing di testo libero: se
        `response.output_parsed` è `None`, o il modello rifiuta, o la risposta
        è incompleta/malformata, blocca fail-closed.

        `operation` (es. "PLAN", "PATCH", "EVALUATION", "CONNECTIVITY_CHECK")
        identifica il tipo di chiamata per l'audit trail persistito in
        `provider_calls`: viene propagato in OGNI `ProviderCallRecord`
        prodotto da questa chiamata, riuscita o fallita.
        """
        call_number = self._check_call_budget()
        requested_at = _now()

        try:
            response = self._client.responses.parse(
                model=self.config.model,
                input=input_messages,
                text_format=text_format,
                timeout=self.config.timeout_seconds,
            )
        except openai.APITimeoutError as exc:
            message = redact(str(exc), self.config.api_key)
            self._record_call(call_number, requested_at, operation, success=False, error=message)
            raise ProviderTimeoutError(
                f"Timeout dopo {self.config.timeout_seconds}s in attesa del provider AI (Responses API)."
            ) from exc
        except openai.APIStatusError as exc:
            message = redact(str(exc), self.config.api_key)
            self._record_call(call_number, requested_at, operation, success=False, error=message)
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
            self._record_call(call_number, requested_at, operation, success=False, error=message)
            raise ProviderMalformedResponseError(f"Errore di comunicazione con il provider: {message}") from exc
        except ValidationError as exc:
            # L'SDK ha ricevuto una risposta HTTP valida, ma il testo generato dal
            # modello non è JSON conforme allo schema stretto richiesto: l'SDK
            # stesso (non il nostro codice) ha tentato e fallito il parsing.
            message = redact(str(exc), self.config.api_key)
            self._record_call(call_number, requested_at, operation, success=False, error=message)
            raise ProviderMalformedResponseError(
                f"Il provider non ha prodotto un output conforme allo schema JSON stretto richiesto: {message}"
            ) from exc

        responded_at = _now()
        usage = _responses_usage_dict(getattr(response, "usage", None))

        refusal_text = _find_refusal_text(response)
        if refusal_text is not None:
            message = redact(refusal_text, self.config.api_key)
            self._record_call(
                call_number, requested_at, operation, success=False, error=message,
                responded_at=responded_at, usage=usage,
            )
            raise ProviderRefusalError(f"Il provider ha rifiutato la richiesta: {message}")

        if response.status == "incomplete":
            reason = response.incomplete_details.reason if response.incomplete_details else None
            self._record_call(
                call_number, requested_at, operation, success=False,
                error=f"risposta incompleta (motivo: {reason})",
                responded_at=responded_at, usage=usage,
            )
            raise ProviderIncompleteResponseError(f"Risposta incompleta dal provider (motivo: {reason}).")

        if response.status not in (None, "completed"):
            message = redact(
                str(response.error) if response.error else f"status inatteso: {response.status}",
                self.config.api_key,
            )
            self._record_call(
                call_number, requested_at, operation, success=False, error=message,
                responded_at=responded_at, usage=usage,
            )
            raise ProviderMalformedResponseError(f"Il provider ha risposto con stato inatteso: {message}")

        parsed = response.output_parsed
        if parsed is None:
            self._record_call(
                call_number, requested_at, operation, success=False,
                error="output_parsed è None: risposta non conforme allo schema JSON stretto",
                responded_at=responded_at, usage=usage,
            )
            raise ProviderMalformedResponseError(
                "Il provider non ha prodotto un output conforme allo schema JSON stretto richiesto."
            )

        call_cost = self._apply_usage_budget(call_number, requested_at, responded_at, usage, operation)

        self._record_call(
            call_number, requested_at, operation, success=True, responded_at=responded_at,
            usage=usage, estimated_cost_usd=call_cost,
        )
        return parsed

    def _check_call_budget(self) -> int:
        if self._call_count >= self.config.max_calls_per_run:
            raise ProviderCallLimitExceededError(
                f"Limite massimo di chiamate per run superato ({self.config.max_calls_per_run})."
            )
        self._call_count += 1
        return self._call_count

    def _apply_usage_budget(
        self,
        call_number: int,
        requested_at: str,
        responded_at: str,
        usage: dict | None,
        operation: str,
    ) -> float | None:
        usage = usage or {}
        total_tokens = int(usage.get("total_tokens") or 0)
        self._tokens_used += total_tokens
        call_cost = self._estimate_cost(total_tokens)
        self._cost_used_usd += call_cost or 0.0

        if self._tokens_used > self.config.max_tokens_per_run:
            self._record_call(
                call_number, requested_at, operation, success=False, error="usage budget exceeded",
                responded_at=responded_at, usage=usage, estimated_cost_usd=call_cost,
            )
            raise ProviderUsageBudgetExceededError(
                f"Budget token per run superato: usati {self._tokens_used}, "
                f"limite {self.config.max_tokens_per_run}."
            )
        if self._cost_used_usd > self.config.max_cost_usd_per_run:
            self._record_call(
                call_number, requested_at, operation, success=False, error="cost budget exceeded",
                responded_at=responded_at, usage=usage, estimated_cost_usd=call_cost,
            )
            raise ProviderCostBudgetExceededError(
                f"Budget di costo stimato per run superato: stimati ${self._cost_used_usd:.4f}, "
                f"limite ${self.config.max_cost_usd_per_run:.4f}."
            )
        return call_cost

    def _estimate_cost(self, total_tokens: int) -> float | None:
        if self.config.cost_per_1k_tokens_usd is None:
            return None
        return (total_tokens / 1000.0) * self.config.cost_per_1k_tokens_usd

    def _record_call(
        self,
        call_number: int,
        requested_at: str,
        operation: str,
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
            operation=operation,
            call_number=call_number,
            requested_at=requested_at,
            responded_at=responded_at or _now(),
            success=success,
            usage=usage,
            estimated_cost_usd=estimated_cost_usd,
            error_summary=redact(error, self.config.api_key),
        )


# -- validazione della patch strutturata (fail-closed, mai in silenzio) --------------


def _assert_safe_relative_path(path: str) -> None:
    """Rifiuta path assoluti o con tentativo di traversal PRIMA di toccare la sandbox.

    Controllo indipendente e aggiuntivo rispetto a `Workspace.resolve` (che
    resta invariato e fa la sua stessa verifica al momento della scrittura su
    disco): qui blocchiamo l'operazione più a monte, appena il provider la
    propone, così un path non sicuro non genera nemmeno un `FileChange`.
    """
    if not path or not path.strip():
        raise ProviderUnsafePatchError("Percorso di patch non sicuro: vuoto.")
    if PurePosixPath(path).is_absolute() or path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        raise ProviderUnsafePatchError(f"Percorso di patch non sicuro (assoluto): {path!r}")
    if any(part == ".." for part in PurePosixPath(path).parts):
        raise ProviderUnsafePatchError(f"Percorso di patch non sicuro (tentativo di path traversal): {path!r}")


def _validate_and_convert_operations(
    operations: list[PatchFileOperation], *, max_files: int | None
) -> list[FileChange]:
    """Valida ogni operazione e la converte in `FileChange`, fail-closed su qualunque anomalia.

    - path traversal/assoluto -> `ProviderUnsafePatchError`;
    - `operation="delete"` -> `ProviderUnsafePatchError` (la sandbox `Workspace`
      non implementa la cancellazione: ignorarla in silenzio lasciando il file
      presente sarebbe un comportamento non sicuro, quindi si blocca);
    - `content` mancante per create/update -> `ProviderUnsafePatchError`.
    """
    if max_files is not None:
        _enforce_max_files(len(operations), max_files=max_files)

    file_changes: list[FileChange] = []
    for op in operations:
        _assert_safe_relative_path(op.path)
        if op.operation == "delete":
            raise ProviderUnsafePatchError(
                f"Operazione 'delete' non supportata dalla sandbox per il path {op.path!r}: "
                "rifiutata invece di essere ignorata in silenzio."
            )
        if op.operation not in ("create", "update"):
            raise ProviderUnsafePatchError(f"Operazione di patch non riconosciuta: {op.operation!r}")
        if op.content is None:
            raise ProviderUnsafePatchError(
                f"Contenuto mancante per l'operazione {op.operation!r} sul path {op.path!r}."
            )
        file_changes.append(FileChange(path=op.path, content=op.content))
    return file_changes


def _enforce_max_files(total_files: int, *, max_files: int | None) -> None:
    if max_files is not None and total_files > max_files:
        raise ProviderUnsafePatchError(
            f"La patch propone {total_files} file, ma la specifica corrente ne consente "
            f"al massimo {max_files}: rifiutata invece di essere applicata parzialmente."
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

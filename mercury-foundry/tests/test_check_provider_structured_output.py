"""Test di `OpenAICompatibleProvider.check_connectivity` — Structured Outputs.

Nessuno di questi test esegue una chiamata di rete reale: il client `openai`
è sempre costruito con un `httpx.MockTransport` iniettato, così la vera
logica di parsing dell'SDK ufficiale viene esercitata (non solo una funzione
mock nostra) senza mai raggiungere `api.openai.com`.
"""

from __future__ import annotations

import json

import httpx
import pytest
from openai import OpenAI

from mercury_foundry.ai.errors import (
    ProviderCostBudgetExceededError,
    ProviderIncompleteResponseError,
    ProviderMalformedResponseError,
    ProviderRefusalError,
    ProviderUnknownModelError,
    ProviderUsageBudgetExceededError,
)
from mercury_foundry.ai.provider_config import RealProviderConfig
from mercury_foundry.ai.real_provider import OpenAICompatibleProvider

FAKE_API_KEY = "sk-test-super-secret-value-12345"


def _config(**overrides) -> RealProviderConfig:
    base = dict(
        api_key=FAKE_API_KEY,
        model="gpt-4o-mini",
        base_url="https://example-provider.invalid/v1",
        timeout_seconds=5.0,
        max_calls_per_run=3,
        max_tokens_per_run=1000,
        max_cost_usd_per_run=1.0,
        cost_per_1k_tokens_usd=0.01,
    )
    base.update(overrides)
    return RealProviderConfig(**base)


def _mock_client(handler) -> OpenAI:
    """Costruisce un client `openai` reale con trasporto HTTP fittizio.

    Il client e tutta la logica di parsing della SDK ufficiale eseguono
    davvero; solo il livello di trasporto HTTP è sostituito.
    """
    return OpenAI(
        api_key=FAKE_API_KEY,
        base_url="https://example-provider.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _base_response(**overrides) -> dict:
    body = {
        "id": "resp_test",
        "object": "response",
        "created_at": 1700000000,
        "model": "gpt-4o-mini",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "metadata": {},
        "parallel_tool_calls": True,
        "temperature": 1.0,
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "output": [],
        "usage": {
            "input_tokens": 20,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 8,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 28,
        },
    }
    body.update(overrides)
    return body


def _message_output(text: str) -> list[dict]:
    return [
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
    ]


def _refusal_output(refusal_text: str) -> list[dict]:
    return [
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "refusal", "refusal": refusal_text}],
        }
    ]


# --- output strutturato valido -------------------------------------------------------

def test_valid_structured_output_returns_parsed_result_and_records_metadata():
    payload = json.dumps({"status": "ok", "message": "connectivity check ok"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    result = provider.check_connectivity("verifica di connettività")

    assert result == {"status": "ok", "message": "connectivity check ok"}

    record = provider.last_call_record
    assert record is not None
    assert record.success is True
    assert record.is_simulated is False
    assert record.usage == {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}
    assert record.estimated_cost_usd == pytest.approx((28 / 1000.0) * 0.01)
    assert record.error_summary is None


# --- rifiuto ---------------------------------------------------------------------------

def test_refusal_raises_provider_refusal_error_and_records_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_base_response(output=_refusal_output(f"non posso farlo (key={FAKE_API_KEY})"))
        )

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderRefusalError):
        provider.check_connectivity("verifica di connettività")

    record = provider.last_call_record
    assert record.success is False
    assert FAKE_API_KEY not in record.error_summary


# --- risposta incompleta -----------------------------------------------------------

def test_incomplete_response_raises_provider_incomplete_response_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_base_response(
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
                output=[],
            ),
        )

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderIncompleteResponseError) as exc_info:
        provider.check_connectivity("verifica di connettività")

    assert "max_output_tokens" in str(exc_info.value)
    assert provider.last_call_record.success is False


# --- risposta non conforme allo schema (malformata) --------------------------------

def test_invalid_schema_response_raises_provider_malformed_response_error():
    # Testo libero non conforme allo schema stretto: l'SDK non riesce a produrre
    # `output_parsed`, quindi il nostro codice deve bloccare senza indovinare nulla
    # da testo libero.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output("non sono JSON valido")))

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderMalformedResponseError):
        provider.check_connectivity("verifica di connettività")

    assert provider.last_call_record.success is False


def test_api_level_error_response_raises_provider_malformed_response_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"message": f"internal server error (key={FAKE_API_KEY})", "type": "server_error"}},
        )

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderMalformedResponseError):
        provider.check_connectivity("verifica di connettività")

    assert FAKE_API_KEY not in provider.last_call_record.error_summary


# --- modello non supportato per structured output -----------------------------------

def test_unsupported_structured_output_model_raises_provider_unknown_model_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "message": "The model `some-unsupported-model` does not exist or you do not have access to it",
                    "code": "model_not_found",
                }
            },
        )

    provider = OpenAICompatibleProvider(_config(model="some-unsupported-model"), client=_mock_client(handler))
    with pytest.raises(ProviderUnknownModelError):
        provider.check_connectivity("verifica di connettività")

    assert provider.last_call_record.success is False


def test_unsupported_structured_output_model_detected_from_message_without_error_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "This model does not support structured outputs / json_schema response format.",
                    "type": "invalid_request_error",
                }
            },
        )

    provider = OpenAICompatibleProvider(_config(), client=_mock_client(handler))
    with pytest.raises(ProviderUnknownModelError):
        provider.check_connectivity("verifica di connettività")


# --- persistenza di usage e costo stimato -------------------------------------------

def test_usage_and_estimated_cost_are_persisted_on_success():
    payload = json.dumps({"status": "ok", "message": "ok"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_base_response(
                output=_message_output(payload),
                usage={
                    "input_tokens": 68,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "output_tokens": 10,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": 78,
                },
            ),
        )

    provider = OpenAICompatibleProvider(_config(cost_per_1k_tokens_usd=0.0006), client=_mock_client(handler))
    provider.check_connectivity("verifica di connettività")

    record = provider.last_call_record
    assert record.usage == {"input_tokens": 68, "output_tokens": 10, "total_tokens": 78}
    assert record.estimated_cost_usd == pytest.approx((78 / 1000.0) * 0.0006)


def test_usage_budget_exceeded_blocks_structured_output_call():
    payload = json.dumps({"status": "ok", "message": "ok"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_base_response(
                output=_message_output(payload),
                usage={
                    "input_tokens": 4000,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "output_tokens": 1000,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": 5000,
                },
            ),
        )

    provider = OpenAICompatibleProvider(
        _config(max_tokens_per_run=100, max_calls_per_run=5), client=_mock_client(handler)
    )
    with pytest.raises(ProviderUsageBudgetExceededError):
        provider.check_connectivity("verifica di connettività")

    assert provider.last_call_record.success is False


def test_cost_budget_exceeded_blocks_structured_output_call():
    payload = json.dumps({"status": "ok", "message": "ok"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    provider = OpenAICompatibleProvider(
        _config(max_cost_usd_per_run=0.0001, max_tokens_per_run=100_000, cost_per_1k_tokens_usd=1.0),
        client=_mock_client(handler),
    )
    with pytest.raises(ProviderCostBudgetExceededError):
        provider.check_connectivity("verifica di connettività")

    assert provider.last_call_record.success is False


def test_check_connectivity_respects_max_calls_per_run():
    from mercury_foundry.ai.errors import ProviderCallLimitExceededError

    payload = json.dumps({"status": "ok", "message": "ok"})
    calls_made = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls_made.append(request)
        return httpx.Response(200, json=_base_response(output=_message_output(payload)))

    provider = OpenAICompatibleProvider(_config(max_calls_per_run=1), client=_mock_client(handler))
    provider.check_connectivity("prima chiamata")
    with pytest.raises(ProviderCallLimitExceededError):
        provider.check_connectivity("seconda chiamata, non deve partire")

    # La seconda chiamata è stata bloccata PRIMA di raggiungere la rete.
    assert len(calls_made) == 1

---
    name: OpenAI structured-output check-provider fix
    description: Why check-provider was moved off prompt-only JSON onto Responses API + Structured Outputs, and how it's tested
    ---

    The connectivity-check path (`check-provider` CLI command) originally reused the same "ask for JSON in the prompt, then json.loads the free text" mechanism as the full plan/patch generation methods. A real (authorized) API call showed this is unreliable: the model can return prose instead of JSON, which the old code correctly blocked but couldn't recover from.

    **Fix:** give the connectivity-check path its own method (`check_connectivity`) using the official `openai` SDK's Responses API with Structured Outputs (a Pydantic model passed as `text_format`, which the SDK turns into a strict JSON Schema and parses via `response.output_parsed` — never manual JSON extraction from text).

    **Why:** prompt-only JSON enforcement has no guarantee; strict schema + SDK-side parsing does. Scoped narrowly to check-provider only — `propose_plan`/`propose_patch` (the full Foundry run loop) intentionally still use the older raw-HTTP chat-completions mechanism, since redesigning those schemas was not part of the request and is much larger in scope.

    **How to test:** mock at the HTTP-transport layer with `httpx.MockTransport` injected into a real `openai.OpenAI(http_client=...)` instance, not by mocking the SDK's `.parse()` method. This exercises the SDK's actual parsing/refusal/incomplete-response detection against a fake HTTP response, which is far more faithful than mocking the call itself. Refusals show up as a `type="refusal"` content item nested in an output message; incomplete responses have `response.status == "incomplete"` with `incomplete_details.reason`; schema-non-conformant text raises a `pydantic.ValidationError` *inside* the SDK's parse step (not an `openai.APIError`) — must be caught separately.
    
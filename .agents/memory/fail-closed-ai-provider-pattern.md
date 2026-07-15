---
name: Fail-closed real-AI-provider pattern
description: Design pattern for adding a real, paid AI provider to a system that must never call it automatically, silently fall back, or leak secrets.
---

When a project needs its first *real* (paid) AI provider added behind an existing pluggable `AIProvider`-style interface, and the requirement is "never call it silently, never leak secrets, never fall back":

- Load all provider config (API key, model, base URL, timeout, call/token/cost budgets) exclusively from env vars/secrets, with **no default values** for endpoint/model/credentials. Missing/incomplete config must raise a clear config error listing only variable *names*, never values.
- Register the provider in the same explicit registry used for the fake/simulated one, so an unconfigured real provider fails the same way an unknown provider name already did — no new fallback path.
- Make the HTTP call function of the provider class injectable (constructor param). Real network code lives in one small default implementation that the automated test suite never exercises — all tests inject a mock. This is the only way to guarantee "tests never call the real API" without a separate integration-test tier.
- Give every real invocation a small result/record object (provider, model, timing, success, usage, estimated cost, redacted error) attached to the provider instance after each call (e.g. `last_call_record`). Let the deterministic orchestration layer (not the provider) persist it to a DB table and link it to the run/task/candidate — keeps the provider itself DB-agnostic and testable.
- Any provider-side failure (timeout, unknown model, malformed response, call-limit/token-budget/cost-budget exceeded, missing creds) should raise from one shared exception base class. The orchestration layer catches that base class and blocks the task/goal immediately (no automatic retry consuming an attempt) — budgets and safety errors are not the same category as "a test failed, try again".
- Redact secrets at the point an error message is constructed (replace the literal secret substring), not just "avoid printing it" — mocked tests that intentionally return a secret-laden error message are the way to prove this holds.
- Add a CLI/manual-only "connectivity check" command that require an explicit confirm flag and is never invoked by any automatic code path — this is how you let the human validate real credentials without the agent ever triggering a paid call itself.

**Why:** this was the exact shape of the requirement for Mercury Foundry V0.2 — add one real OpenAI-compatible provider to a system with a strict simulated-only history, with zero tolerance for a silent real API call or a leaked key during development.

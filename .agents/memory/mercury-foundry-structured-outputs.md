---
name: Mercury Foundry Structured Outputs rollout
description: propose_plan/propose_patch/propose_evaluation in real_provider.py share the same strict-schema Structured Outputs mechanism as check_connectivity; notes a pre-existing provider_calls persistence gap.
---

## What changed
`OpenAICompatibleProvider.propose_plan` and `propose_patch` (mercury-foundry/mercury_foundry/ai/real_provider.py)
were rewritten off the old raw chat-completions + free-text-JSON mechanism onto the same
Structured Outputs mechanism (`client.responses.parse` + strict Pydantic schema, SDK-level
parsing only) already used by `check_connectivity`. All four operations (`propose_plan`,
`propose_patch`, `check_connectivity`, and the new supplementary `propose_evaluation`) now
share one `_structured_call` helper for budget/timeout/refusal/incomplete/schema-mismatch handling.
Schemas live in `mercury_foundry/ai/schemas.py` (PlanSchema, PatchSchema, PatchFileOperation,
EvaluationSchema, ConnectivityCheckResult).

**Why:** the first real controlled Foundry run failed closed during PLAN because `propose_plan`
still relied on free-text JSON parsing (only `check_connectivity` had been fixed previously).
Prompt-only JSON is fragile against real models; strict Structured Outputs removes that failure mode.

## Patch safety additions
`propose_patch` validates every proposed file operation before converting it to a `FileChange`:
rejects path traversal/absolute paths, rejects `operation="delete"` outright (the sandbox
`Workspace` has no delete capability, so silently ignoring a delete would be unsafe), rejects
missing `content` for create/update, and — only if the caller passes `context["max_files"]`
(not currently threaded through by `Builder`, so default behavior is unchanged) — rejects a
patch that proposes more files than allowed. All violations raise `ProviderUnsafePatchError`.

## Known pre-existing gap (not fixed, intentionally out of scope)
`Orchestrator.submit_goal` only persists a `provider_calls` row for the PLAN step when it
**fails** (ProviderExecutionError). A successful plan call is never written to `provider_calls`
— only `ai_provider.last_call_record` holds it transiently, then gets overwritten by the next
call. Build/attempt calls (via `ExecutionLoop`) ARE always persisted, success or failure. So
`list_provider_calls_for_goal` undercounts real spend when planning succeeds on the first try.

**How to apply:** if asked to fix cost/usage reporting accuracy for Mercury Foundry, this is
the first place to look — `Orchestrator.submit_goal`'s success path needs an explicit
`models.create_provider_call` call mirroring the exception-handler branch already there.

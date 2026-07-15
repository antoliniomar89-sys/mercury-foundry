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

## provider_calls audit trail (gap fixed)
The PLAN-success persistence gap described above is fixed: every real provider call (PLAN,
PATCH, EVALUATION, CONNECTIVITY_CHECK — success or failure) now persists exactly one
`provider_calls` row via a single shared helper (`models.persist_provider_call_record`), used
by both `Orchestrator.submit_goal` and `ExecutionLoop`. `run_id` = `str(goal_id)` (one CLI
`submit` covers PLAN+BUILD for one goal in one provider instance, so goal_id is a valid run
correlation key without a new run-tracking subsystem). `ProviderCallRecord` gained a required
`operation` field, populated by the provider itself in `real_provider.py`. Dedup is enforced via
a `UNIQUE(run_id, provider_name, call_number)` index plus a pre-insert existence check in
`models.create_provider_call` (append-only: never updates/deletes, just skips duplicate inserts).

**Why:** the user explicitly required no undercounted spend and no duplicate audit rows before
running the Foundry for real again.

**How to apply / gotcha for future schema changes:** when adding a migrated column to an
existing SQLite table here, never put a dependent `CREATE INDEX`/constraint referencing the new
column directly in `schema.sql` — `executescript` runs unconditionally against old DBs where
`CREATE TABLE IF NOT EXISTS` is a no-op, so it fails with "no such column" before the
idempotent `ALTER TABLE` migration in `state/db.py` (`_migrate_provider_calls_columns`, run
after `executescript`) gets a chance to add the column. Put such indexes in the migration
function instead, after the `ALTER TABLE`. Also: any code path that opens/initializes this DB
(e.g. `diagnostics.py`'s doctor check) must call the same `state.db.init_schema`, not
re-implement its own `executescript`, or it'll silently skip the migration and desync.

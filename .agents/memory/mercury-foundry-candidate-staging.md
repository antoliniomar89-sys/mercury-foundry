---
name: Mercury Foundry candidate staging & atomic promotion
description: How BUILD/TEST/VERIFY isolation from the real target works, and the append-only candidate/provider-call linkage — read before touching execution/loop.py, approval/gate.py, or sandbox/staging.py.
---

## Staging-per-attempt, never touch the real target during BUILD/TEST/VERIFY

Every BUILD→TEST→VERIFY attempt in `ExecutionLoop.run_task` runs against an isolated
filesystem copy ("staging") of `target_project`, created fresh per attempt via
`sandbox.staging.create_staging(base_dir, run_id, attempt_id, target_root)`. The real
target is read once (to snapshot/copy it) and is never written to until a human
approves the resulting candidate.

**Why:** an in-progress or failed attempt must never leave partial/incorrect files in
the real project; only a human-approved candidate may become real changes.

**How to apply:** `Builder.build()`, `Evaluator.evaluate()`/`TestRunner.run()` all take
an optional `workspace`/`cwd` param — always pass the current attempt's staging root
explicitly from `ExecutionLoop`, never rely on the constructor-time default (that
default only exists for backward compatibility with older direct callers/tests).

## Atomic promotion happens only in the Approval Gate

`approval.gate.approve_candidate` is the *only* code path allowed to write to the real
target. Sequence: re-verify `pending_review` → `verify_target_unchanged` against the
candidate's stored `target_snapshot_hash` (raises `TargetConflictError`, fail-closed,
if the target drifted since the candidate was created) → `promote_staging` (all-or-
nothing; rolls back every path it touched in that call if any write fails) → flip
status to `approved` → discard the staging dir. `reject_candidate` never touches the
target — it only discards staging and flips status to `rejected`.

**Why:** prevents silently overwriting a target that changed after the candidate was
computed, and prevents a partially-promoted target if I/O fails mid-batch.

**How to apply:** a candidate's manifest (`manifest_json`) carries `target_root`,
`target_snapshot_hash`, and the diff (`created`/`modified`/`deleted` lists) needed to
promote without re-deriving anything — read it back via `approval.gate._diff_from_manifest`
rather than recomputing the diff by hand.

## TestRunner env must be an allowlist, not `os.environ` minus a blocklist

`sandbox.test_env.build_sanitized_test_env` builds the subprocess env from a small
allowlist (`PATH`, `LANG`, `LC_ALL`, plus `PYTHONPATH`/`PYTHONUSERBASE`/`UV_PYTHON_*`
which are required in this Replit environment for the installed `pytest` binary to
find its own `_pytest` package — without them you get a confusing
`ModuleNotFoundError: No module named '_pytest'`, not a permissions error). Explicit
secret names + a name-substring blocklist (`KEY`, `TOKEN`, `SECRET`, ...) are re-filtered
defense-in-depth at the end regardless of source.

**Why:** starting from a full `os.environ` copy and subtracting risks missing a future
secret name; an allowlist only grows by deliberate, reviewed additions.

## Append-only candidate↔provider_calls linkage

There is no `attach_candidate_to_provider_calls` anymore (it used to `UPDATE
provider_calls SET candidate_id=...` retroactively, violating provider_calls'
append-only-ness). Use `models.associate_candidate_provider_calls(conn, task_id,
candidate_id)` (idempotent `INSERT OR IGNORE` into `candidate_provider_calls`) and
`models.list_candidate_provider_calls(conn, candidate_id)` to read them back.

**Why:** `provider_calls` rows must never be mutated after insert — the association is
a separate junction table instead.

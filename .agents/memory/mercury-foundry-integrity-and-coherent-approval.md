---
name: Mercury Foundry candidate integrity + coherent filesystem/DB approval
description: How the Approval Gate verifies staging (not just target) integrity, coordinates filesystem promotion with a single DB transaction, and recovers (or fails safe) when a step after promotion fails.
---

## Problem
Atomic candidate staging (MF-FIX-004) only re-verified that the *target* was
unchanged before promoting. Two gaps remained: (1) nothing checked that the
*staging* itself hadn't been altered since candidate creation, and (2)
filesystem promotion and DB/audit writes were not coordinated — a DB failure
after a successful promotion could leave target and DB state diverged, with
no path back except manual intervention.

## Design
- **Integrity is a full manifest diff, not spot checks.** Store a generic
  per-file `{hash, size}` manifest of the staging tree at candidate-creation
  time; at approval time, recompute and diff. Any added/removed/changed byte
  anywhere is caught without needing to know which files matter — no
  probe-specific logic in the engine.
- **Read-only permissions are defense-in-depth only, never the control.** A
  filesystem that ignores chmod (or a root process) must not weaken security;
  the manifest diff is the actual gate, checked unconditionally.
- **Approval is: verify → verify → backup → promote filesystem → ONE DB
  transaction → commit → cleanup.** The backup (full copy of target, mirrors
  the existing staging-copy pattern) is created before any target write, so a
  failure discovered after promotion can restore the target exactly.
- **A failure after promotion has two possible outcomes, never a silent
  third:** DB rollback + successful target restore → original error re-raised,
  candidate stays `pending_review` (retryable); DB rollback but restore ALSO
  fails → new `recovery_required` status, backup+staging preserved, no
  auto-retry — this state requires a human, and deliberately has no path back
  to `approved`/`rejected` without manual DB intervention.
- **Approve/reject are idempotent on their own terminal state**, but a
  candidate can never cross from `rejected`/`recovery_required` back to
  `approved`. This is a deliberate behavior change from raising on any repeat
  call — idempotency at the boundary the caller already reached is safer than
  making "did I already do this?" the caller's problem.
- **Run-level (not task-level) provider-call accounting.** A PLAN call has
  `task_id = NULL`, so linking candidates to spend must key off `run_id`
  (shared by PLAN and every task/attempt in that run) or PLAN cost silently
  vanishes from candidate totals.

**Why:** each of these was a genuine audit-identified corruption/accounting
window, not hypothetical — the pattern generalizes to any system that
promotes a validated draft into a shared resource across more than one
write target (filesystem + DB here).

**How to apply:** when adding a new promotion/commit path that writes to two
or more independently-failable stores (disk, DB, external API), use this same
shape: snapshot everything needed to restore *before* the first destructive
write, do the destructive write, then do all bookkeeping writes in one
transaction, and give the "rollback itself failed" case its own terminal
state rather than retrying blindly.

## Follow-up: close every DB-only bypass for records predating the guarantees
A later audit found one more hole: `approve_candidate` still had a fallback
that let records created *before* this whole staging/manifest/backup system
existed (no `staging_root`/`target_snapshot_hash`/`target_root`/manifest)
slip through via a plain DB status flip, since "legacy record, nothing to
verify" had originally been read as "skip verification" instead of "cannot
be verified, so cannot be promoted."

**Why:** a record that predates a safety mechanism has no way to satisfy
that mechanism's checks — treating "nothing to check" as "check passed" is
the same fail-open bug the mechanism was built to prevent, just moved one
level up (into which records are exempt).

**How to apply:** whenever you add a mandatory verification gate to an
existing write path, audit every branch that bypasses the gate entirely
(compat shims, legacy fallbacks, "if not present, skip") — bypassing
verification for old data is equivalent to no verification. Give incomplete
records an explicit "not promotable" terminal error instead of a silent
alternate path; they should remain inspectable/rejectable, just never
auto-migrated or promoted retroactively from whatever happens to sit in the
target now (that data was never attributed to the record in the first place).

## Closing out a legacy record once its own guarantees make it inert
Once a record's error type proves it can never be promoted, the correct
close-out is: demonstrate the block once more for the audit trail, reject it
through the normal reject path (never a special-case deletion of the DB
row), and only then physically clean up whatever orphaned artifact it left
behind in the shared resource — and only that specific artifact, never a
broader sweep, since nothing guarantees other leftover files belong to the
same record.

**Why:** deleting the DB row or the target artifact directly (instead of
going through `reject_candidate`) would leave no audit trail and would
assume attribution the system explicitly does not track for legacy records.

**How to apply:** close-out order is verify-block → reject via the real API
→ inspect target for anything unattributable → remove only the exact
known-orphaned path → re-verify the shared resource is empty (or report
unexpected leftovers without deleting them).

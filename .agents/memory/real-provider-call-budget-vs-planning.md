---
name: Real-provider call budgets can starve multi-attempt fix loops
description: A tight max-calls-per-run budget interacts with the model's own planning step count; vague goal descriptions can consume the whole budget before any file is actually produced correctly.
---

When a real-provider run has both a `max_calls_per_run` budget AND a
multi-step PLAN→BUILD→TEST→FIX loop, the PLAN call and every BUILD/FIX
attempt all draw from the same shared call budget. A vague/generic goal
description (e.g. "create the probe with the two required files") lets the
model's own `propose_plan` step fragment the goal into many small tasks
(e.g. "analyze requirements" as task 1, actual file creation much later).
The first task can then consume a real BUILD call without producing the
required test file, and any second FIX attempt gets blocked by
`ProviderCallLimitExceededError` before it can retry — a legitimate
fail-closed outcome, not a bug in the enforcement engine (literal content
correction, extra-file dropping, and byte-exact writing all still worked
correctly in this case).

**Why:** observed in a controlled real run capped at `max_calls_per_run=2`:
call 1 = PLAN (produced a 7-task plan), call 2 = first BUILD (task 1 wrote
only the literal file, no test file, plan task 1 was "analyze requirements"
not "create both files"), exact_test_command then failed with "no tests
ran", and the FIX retry's BUILD call was blocked by the call budget before
it could even try again.

**How to apply:** when setting a tight call budget for a real controlled
run, either (a) make the goal description explicit enough that the model's
own plan is likely to be a single task producing both files immediately
(as in the MF-RUN-001B goal wording that got a one-step plan), or (b) size
`max_calls_per_run` with headroom for PLAN + at least one FIX retry, not
just PLAN + one BUILD attempt. A blocked outcome under a tight budget does
not indicate the literal_constraints engine is broken — check the audit
log's `LITERAL_CONSTRAINTS_ENFORCED`/`TEST_COMPLETED`/`PROVIDER_CALL_BLOCKED`
sequence to distinguish enforcement bugs from budget exhaustion.

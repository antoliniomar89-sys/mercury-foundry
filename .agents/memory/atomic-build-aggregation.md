---
name: Atomic BUILD aggregation and build-completeness gate
description: Why multi-step AI plans must be aggregated into one BUILD before TEST, and how the completeness gate blocks partial builds fail-closed.
---

A real provider's plan can fragment one goal into many steps (e.g. "create
app file + test file" → N separate plan steps). If each plan step becomes
its own task with an independent BUILD→TEST cycle, TEST can run before all
required files exist ("no tests ran"), and a FIX retry on a later task can
get starved by the run's call budget even though the actual problem was
architectural, not a bad patch.

**Rule:** when a goal's plan has more than one step, aggregate all steps
into a single task with a combined description, so one BUILD call (one
provider call) receives the full context and one TEST cycle runs only after
that single atomic write. A plan with exactly one step is unaffected —
this keeps aggregation backward-compatible with any existing single-step
plan/test.

**Companion rule:** before writing anything to the sandbox, check the
patch proposal for completeness (all of `LiteralConstraints.required_files`
present, and the proposal isn't empty). Fail closed if incomplete — no
write, no TEST, no automatic retry consumed. This is opt-in and generic
(never hardcodes a filename): a goal that doesn't set `required_files`
sees no behavior change.

**Why:** discovered from a real controlled run where a 7-step plan for a
2-file goal produced a premature TEST_STARTED with no test file on disk,
and the FIX retry then exhausted the run's real-call budget before it could
correct anything.

**How to apply:** when building/replaying an orchestrator that turns an AI
plan into build tasks, always ask "could this plan have more than one step,
and if so, does each step get its own TEST before all of them have run?" —
if yes, aggregate first.

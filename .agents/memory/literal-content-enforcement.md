---
name: Literal content must be engine-enforced, never model-generated
description: Why free-text "reproduce this exact content/path" instructions to an LLM are unreliable, and the general pattern used to fix it deterministically.
---

Small/cheap real LLMs (e.g. gpt-4o-mini) reliably paraphrase or summarize
literal content even when explicitly told "reproduce byte-for-byte, no
changes." This is not a prompting bug fixable by stronger wording — treat any
requirement for exact file paths/content/test commands as data, never as an
instruction to the model.

**Why:** observed twice in a real run (MERCURY FOUNDRY probe task): the model
produced plausible-looking but non-matching content, and separately wrote a
trivial "always true" test that made a broken pipeline report false-positive
success.

**How to apply:** structure exact requirements as a typed constraints object
carried alongside the task/goal (not embedded only in prose). Enforce in two
places, both deterministic (no model call):
1. Post-generation, pre-write: compare the model's proposal against the
   constraints. Auto-correct only when the constraint is fully specified
   (e.g. both exact path AND exact content known) — otherwise block
   fail-closed rather than guessing where/how to fix it.
2. Post-write, pre-success: re-verify the actual file(s) on disk against the
   constraints, independently of whatever test the model wrote. If an exact
   test command is known, the engine should run that literal command itself
   instead of trusting a model-authored test file to check its own work.

Also: `pytest` with zero collected tests exits non-zero ("no tests ran") —
if a spec forbids writing any test file, that is a structural conflict with
a deterministic evaluator treating "no tests ran" as failure, not a content
bug. Either allow a minimal literal test file, or supply an explicit
exact-test-command constraint that the engine executes directly.

If an exact-test-command constraint is expressed with a leading shell-style
env assignment (e.g. `PYTHONDONTWRITEBYTECODE=1 pytest -q`), an engine that
executes argv directly (never `shell=True`, by design, to avoid injection)
must explicitly peel off `NAME=VALUE` leading tokens and pass them as
subprocess `env` overrides — otherwise the whole first token is treated as
the literal executable name and fails with `FileNotFoundError`. This is a
real validation error worth fixing in the engine (not the constraints file),
since the shell-style prefix syntax is a reasonable/common thing to specify.

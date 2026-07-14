---
name: AI integration declined flow
description: What to do when setupReplitAIIntegrations returns status "awaiting_account_upgrade" and the user declines.
---

`setupReplitAIIntegrations({ providerSlug: ... })` can return `{status:"awaiting_account_upgrade", success:false}`
and prompt the user to upgrade their plan. If the user declines, the platform explicitly instructs:
do NOT retry the same call.

**Why:** retrying just re-prompts for the same upgrade the user already said no to — it's not a
transient failure.

**How to apply:** ask the user (via AskQuestion, not free text) whether they want to (a) provide
their own provider API key via the secrets flow (`requestSecrets`, never asked in chat), or
(b) proceed without a real AI provider — implement a clearly-labeled deterministic
fallback/simulation behind the same interface so the rest of the system still works and is
honest about what's real vs. simulated.

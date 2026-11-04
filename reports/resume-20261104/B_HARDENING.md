# B — Focused pipeline hardening
Two changes relative to frozen baseline 5a6e388:
1. Membership evidence with an end date cannot create a management event when its start is missing or the end is not later than the start.
2. Inherited STABLE review requires unchanged required dimensions and evidence obligations, in addition to the existing fingerprint and trigger checks. Expanded scope or changed evidence returns to VERIFIED pending independent review; unchanged scope can reuse STABLE.
Three focused regressions cover invalid intervals, expanded scope and changed evidence, including unchanged reuse. Local suite: 354 tests discovered, 296 passed, 58 hosted-only skips. Archived integration replay matches every accepted candidate table row exactly.
Source identity strictness and quarantine remain unchanged. This change does not independently certify transfer dates, resolve identity debt, repair all provider-routing causes or establish independent schedule completeness. Existing unknowns remain unknown.

# Historical database handoff · 2026-11-25

The six-club historical candidate has 6 of 24 units DATA-ACCEPTED and 11 SOURCE-READY. Bayern Munich 2025/26 H2 has 30 complete club matchday squads, an independently frozen 37-player observed roster, and 607 club player-match records with numeric minutes. Applicable national-team coverage remains open. The next historical work item is to reconcile its applicable national-team selection and actual participation in the January and June windows, then apply the existing whole-unit acceptance rules.

Independent cloud restoration passed in GitHub Actions for the exact November 25 historical candidate. The runner downloaded and hash-verified the private Release, checked all 2,969 retained evidence files, verified the six accepted snapshots, and ran the ordinary exporter. The private continuation entry is marked HANDOFF_READY. Neither the current-season production path nor any website release is part of this handoff.

Execution entry point is `ops.cloud.historical_batch`. The retained historical worker scripts under `ops/cloud/historical_continuation_workers/` preserve the original batch-relative layout. The private continuation configuration supplies the source ledgers and verified identity evidence.

Audit entry point is `squad_pipeline/reconstruction/engine.py` (R1, R2, R5, R3/R4, R6), with `calendar.py` and `retained_adapter.py` in the same package. Membership and minutes use `squad_pipeline/roster_policy.py` and `squad_pipeline/minute_policy.py`. Archived observed-union checks use `ops/cloud/historical_unit.py`. Follow the repository operating rules in `AGENTS.md`.

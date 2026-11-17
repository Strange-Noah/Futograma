# Historical database delivery — 2026-11-17

DATA-ACCEPTED remains **2/24**. [24-unit matrix](UNIT_DELIVERY_MATRIX.json). [Inter 2024/25 H2 acceptance](INTER_2024_H2_DATA_ACCEPTANCE_20261114.md) and [Inter 2025/26 H1 acceptance](INTER_2025_H1_DATA_ACCEPTANCE_20261114.md) remain unchanged.

## Actual data delivered

Since sandbox network collection resumed, isolated historical processing added **74 fixtures and 1,558 player-match records**, and recovered **1,734 previously UNKNOWN minute values**. The latest productive iteration added 13 fixtures, 286 player-match records and 381 minute values across Barcelona/Bayern source subsets. Existing source, identity, minute and canonical writers were reused.

Ordinary export: **PASS / VERIFIED locally**. Candidate SHA-256: `ede5d5642ee84714fd0a3fab8d222860b384b10148993fd018bafeec39831cfc`. Existing numeric values were preserved in the scoped minute-supplement verification. Complete event evidence supports recovered unused-substitute zeroes; other UNKNOWN values remain UNKNOWN. Export success is not whole-unit acceptance.

The matrix contains actual period-specific database counts and a next action for every incomplete unit. Full competition, national-team and R1–R6 closure remains unfinished where recorded. Current-season production is unchanged.

## Source findings and remaining work

Starting-XI name exceptions were processed once per identity using existing canonical entities, independent official/provider identity evidence and scoped historical roster/shirt corroboration. Case-specific inferences retain their evidence and limitations; no player was matched solely by fuzzy name similarity.

The public FotMob fixture archive attempt was stopped on **ROBOTS_DISALLOWED**. DFB liveticker pages inspected contained no match liveticker or squad; that batch was stopped. Sky legacy seasonal result routes redirect to current fixture pages and were not accepted as historical completeness. These provider limitations do not change unit acceptance requirements or justify fabricated records.

Priority: acquire missing early historical competition subsets, process all valid observations immediately, complete applicable national workload and existing audits, and accept eligible units. Optional YOUTH national completeness and future automated cross-season comparisons remain excluded/deferred.

## Verification and publication scope

New database results are **not independently remotely verified**. No hosted regression is claimed for this data-only update. Publication contains sanitized reports and the matrix only: no code, databases, raw sources, exports, identity ledgers or local configuration. [Resumable handoff](HISTORICAL_BATCH_HANDOFF_20261117.md).

# Lightweight historical batch instruction execution review

Formal historical acceptance remains **2/24**. Production remains unchanged.

| Requirement | Result and evidence |
| --- | --- |
| Identify repetitive work | PASS: retained Inter replays repeatedly loaded known identities, checked source hashes, wrote facts, exported, and reran scoped audits. Existing scripts were reused. |
| Thin batch entry point | PASS locally: one CLI delegates existing private replay/audit programs. SourceService, identity registry, canonical writers, minute policy and exporters retain their responsibilities. |
| Full artifacts and concise exceptions | PASS: separate hashed checkpoints, detailed receipts/logs and compact, deduplicated exception summaries. Competition-subset accounting and child exception import were missing in the earlier entry point; both are now repaired. |
| Decision reuse and historical scope | PASS for executed batches: existing identities and frozen rosters reused. No present-season classification was applied to an earlier period. |
| Accepted-unit regressions | PASS: Inter 2025/26 H1 and 2024/25 H2 reproduced all 94 tables without changes. Original immutable snapshots retain their hashes. Both ordinary and all-match exports passed. |
| Recovery | PASS: an intentional failing stage retained its attempt; subsequent resume used a fresh attempt, then reused the completed output. Changed plan input was rejected. |
| Unfinished units | PASS for scoped execution: both remaining Inter periods processed and existing audits executed. Whole-unit acceptance remains open; scoped audit success alone is insufficient. |
| Continuous delivery | IN PROGRESS: Barcelona Copa and Bayern DFB Cup subsets processed; another official Bayern Supercup fixture converted. Remaining club-periods still require source, national, minute and acceptance work. |
| GitHub code publication | BLOCKED: an earlier normal push was rejected by automatic approval because exact new-branch publication authorization was absent. No alternate publication route was used. Revised runner is local only. |

Implementation scope is now frozen at the useful minimum. No scheduler, parallel collector, new identity engine, canonical writer or audit framework was introduced. Future cross-season R1 automation remains one deferred TODO.

The local implementation checkpoint is `e124cdff072281f41fc332569b3a47154e4dc9a3`. This hash identifies local work; it is not a claim of public availability or hosted regression acceptance.

Actual delivery since the retained checkpoint: Bayern Supercup added one complete 20-person own-team squad with numeric minutes. Barcelona Copa's final unmatched bench identity was resolved using independent date-of-birth and documented historical club evidence; a misleading near-name candidate was affirmatively rejected. The existing writers recovered the one bench row. Barcelona Copa now has 215 rows across ten processed fixtures; no unknown minutes in that subset. This excludes outstanding competition scope and does not establish an additional accepted unit.

Request counts and bytes remain recorded per batch. Source counts in compact reports describe the explicitly supplied receipts, not every capture in the project. No source evidence bodies, databases, personal local paths or identity dossiers are included in this report.

# Runtime consolidation status — 2026-11-08

| Status | Implementation / callers | Retained dependency / retirement condition |
|---|---|---|
| RECONSTRUCTION_CANONICAL | `SourceService` and EvidenceStore → existing Client/source_parsing; RetainedAdapter, obligation executor, current historical replay callers | Reuse acquisition/parser/cache/provenance; acceptance does not migrate production |
| PRODUCTION_LEGACY | Existing `live_ingest`, `international_incremental`, cloud driver/incremental and native Client/parser calls | Preserve production pins, schemas, writer, roster_policy/minute_policy, release/rollback compatibility until separately approved migration |
| ADAPTER | SourceService retained-format/migration dispatch and RetainedAdapter | Remove only after all retained callers/formats have replacement contract/relocation/replay acceptance; no retirement authorized now |
| EXPERIMENTAL | Private Agent-created historical batches, hand-assembled proof/alias packets and cross-batch orchestration | Replace routine control with tracked club-period command using existing interfaces; keep evidence/history; do not publish raw batches/data |
| Unimplemented connection | Durable historical job linking catalogue → identity-bound observed union → original player pipelines → supplementary audit → export/recovery | Minimal wiring/checkpoint repair described in audit; not a new framework or parallel pipeline |

Reused code: all tested transport, parsing, normalization, policies, canonical persistence, audit, LocalStore/export/recovery modules. Newly written for this checkpoint: audit harness and sanitized reports only. No pipeline change, new collector/parser/writer or production migration.

Coverage: 169 guarded tests passed, 33 existing hosted-only skips; six additional guarded LocalStore/export/recovery checks passed. No live acquisition. Complete historical orchestration and 24-unit acceptance remain unverified. Previously accepted hosted runs retain their exact-commit scope.

Deferred debt: consolidate routine historical control, persist cross-batch scope/accounting and evidence routing, connect resume scheduling after isolated acceptance. Keep production source compatibility and legacy rollback intact; no unrelated cleanup. Actual call graph and limitations are in RUNTIME_AGENT_INDEPENDENCE_AUDIT.md.

# Architecture consolidation — 2026-11-08

The published runtime audit remains authoritative: partial independence.

- **RECONSTRUCTION_CANONICAL:** SourceService/EvidenceStore, existing parsers, shared identity_registry, roster_policy/minute_policy and original canonical writer. No new acquisition, normalization or fact-writing implementation.
- **EXPERIMENTAL:** tracked `ops.cloud.historical_unit` now replaces the routine *archive replay/checkpoint and observed-union evaluation* slice of private batch orchestration. Actual caller: CLI executed for four PSG historical periods. It is not yet the authoritative complete historical command. It adds job state, not football storage or adjudication authority.
- **ADAPTER:** existing retained-format/source migration dispatch and RetainedAdapter remain; removal requires all actual callers/formats and relocation/replay compatibility accepted. No speculative adapter added.
- **PRODUCTION_LEGACY:** current driver/incremental/native source calls and accepted writer/export/release remain authoritative for deployed production. No production module moved, deleted, rewritten or migrated.

Verified coverage: ten new focused tests; exact local commit584 selected/526 pass/58 hosted skips; subsequently published exact-candidate hosted584/584 pass including all58; four actual archived PSG unit runs and identical zero-parser repeats. No other club-period or full unattended job acceptance inferred.

Remaining duplicate/control risk: private batching still owns initial discovery, some proof assembly and downstream routing. Its archive replay slice can be retired only after caller migration acceptance (exact-candidate hosted584 tests now pass); historical evidence is never deleted. Existing source parsers remain shared; no versioned pipeline directory created.

Deferred work: durable disclosed-link discovery/accounting and complete-scope certificate handling; automated certificate handoffs; original downstream orchestration and end-to-end recovery. Preserve source-access stops and accepted facts. Whole-repository cleanup is outside scope.

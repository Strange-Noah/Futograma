# Architecture consolidation status

This checkpoint audits acquisition, archival, parsing, evidence normalization and their direct reconstructed callers. No repository-wide rewrite or production migration.

| Authority/status | Actual implementation and callers | Treatment / removal condition |
|---|---|---|
| RECONSTRUCTION_CANONICAL boundary | SourceService.acquire/observe; reconstructed RetainedAdapter, operational calendar replay and source acceptance runners | Continue to use this boundary; no new transport engine |
| PRODUCTION_LEGACY transport | live_sources.Client; called by SourceService and existing production collectors | Reused with opt-in isolated storage; legacy defaults remain; retirement requires separate accepted production migration |
| Reused provider parsing | international_fotmob; international_discovery.profile_hints/team_fixture_hints; international_parse.parse_sky; live_parse HTML parsers; identity_sources calendar | Dispatched by source_parsing, not copied. Optional club history leaves legacy default national-only behavior unchanged |
| ADAPTER, experimental integration | EvidenceStore normalization/URL capture index; AssetArchiveReader capture metadata; retained provider/canonical context bridge | Needed for actual retained formats. Remove only after accepted replacement records, verified callers and rollback compatibility |
| ADAPTER, pure calendar compatibility | fifa_windows can still parse for legacy pure tests; operational probe supplies SourceService observations and validates provenance | Remove fallback only after remaining tests/callers migrate and separate review confirms compatibility |
| EXPERIMENTAL/test-retained | reconstruction.archive.Archive | No operational RetainedAdapter caller remains; direct archive regression/rollback tests remain. Not declared RETIRED or deleted |
| EXPERIMENTAL integration extensions | Parser version 3, opt-in history claims, typed Sky national dispatch and bounded observation cache | Local tests and real archived replay pass; not covered by earlier Gate S hosted run or production authority |

Actual operational graph: reconstruction_probe → engine.run → RetainedAdapter.discover/resolve → SourceService.observe → acquire → EvidenceStore lookup/read (or the existing Client when explicitly permitted) → source_parsing → existing provider parsers. R1 provenance normalization uses that same service's EvidenceStore. Official calendar acquisition/parsing also goes through SourceService; downstream calendar code only interprets dates/windows. No urllib/requests transport exists in the reconstruction adapter.

[AST module/caller inventory plus runtime trace](CALL_GRAPH.json) records current code hashes and observed boundary categories. The inventory distinguishes static direct calls from runtime evidence; it does not pretend to resolve arbitrary Python dispatch. The operational probe makes zero network calls.

The archived Sky evidence wrapper held an original verified FotMob fixture context, not the already-transformed Sky parser context. A narrow adapter reconstructs the required candidate context after validating provider team bindings, kickoff, competition and level against the retained canonical fixture. Existing competition alias rules and label certificates are reused. Missing bindings/context remain explicit. No native ID is fabricated from filenames or similarity.

Known debt: legacy production orchestration still calls its own proven parsers directly; production remains authoritative there. Source-contract version 1 remains accepted at the immutable published commit; subsequent extensions are experimental until accepted. Duplicate historical one-off modules are preserved pending caller/rollback review. No active versioned pipeline copy was created. RETIRED implementations: none.

# Architecture and actual callers

The original pipeline retains primary authority: live_sources.Client / established acquisition and provider parsers → roster_refresh and identity_registry → canonical verification and live_ingest/international_store writers → normal export. It operates with all reconstruction imports unavailable in the controlled integration test.

The supplementary pipeline consumes accepted facts and source observations: RetainedAdapter → engine R1/R2/R5/R3/R4/R6 → separate findings and evidence-calibrated proposals. SourceService remains RECONSTRUCTION_CANONICAL for audit acquisition/parsing, reusing existing transports and provider parsers. It does not replace original production entrypoints.

Existing adjudication and persistence are shared through quality_pipeline, identity_registry, minute_policy, apply_appearance/fact and international_store. The optional controlled coordinator is not an R-module fact writer. Ordinary export accepts optional already-computed audit metadata; it imports no audit module and has no audit-failure dependency.

CALL_GRAPH.json records actual import edges and audit SQL authority. SourceService callers remain source_replay, reconstruction_probe, RetainedAdapter and controlled obligation execution. Retained source-format normalization and provider/native-to-canonical label handling remain ADAPTER components; retirement requires verified replacement/caller/rollback compatibility. Older archive/legacy helpers and historical orchestration remain preserved, not declared unused or deleted.

PRODUCTION_LEGACY components remain authoritative for production. Accepted source contracts remain RECONSTRUCTION_CANONICAL for audit consumers. Reconstructed audit orchestration remains separately tested development, awaiting hosted acceptance. No parallel collector, duplicate provider parser, new canonical writer, speculative future-provider adapter or copied versioned pipeline was introduced.

Deferred debt: older orchestration overlap, legacy generated label keys, additional provider formats and unproved national call-up intervals. These do not reopen accepted baseline scope or require wholesale cleanup. No module was retired and no production migration occurred.

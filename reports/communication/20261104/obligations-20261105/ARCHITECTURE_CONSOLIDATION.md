# Architecture consolidation checkpoint

SourceService remains RECONSTRUCTION_CANONICAL for acquisition/parsing. Its public Client, archive reader and existing provider parsers are reused. No second HTTP engine, parser family, speculative adapter or versioned pipeline directory was introduced. The publication worktree is Git packaging of the same implementation, not another active pipeline version.

| Implementation / status | Actual callers and evidence | Removal condition |
|---|---|---|
| source_interface.SourceService / RECONSTRUCTION_CANONICAL | source_replay, reconstruction_probe, RetainedAdapter and obligation_execution; runtime replay and source tests | Remains authoritative for reconstruction |
| source_parsing / ADAPTER | SourceService only; delegates to international_fotmob, live_parse, sky_german, identity_sources and international_discovery | Retain until stored formats and production migration have separate acceptance |
| RetainedAdapter / EXPERIMENTAL reconstructed consumer | reconstruction_probe and obligation_execution | Replace transitional source/canonical-label assumptions only after scoped compatibility acceptance |
| roster_policy, identity_registry and minute_policy / reused authority | RetainedAdapter and controlled adjudication | No replacement or duplicate policy implementation |
| coldstart_acceptance.apply_appearance / PRODUCTION_LEGACY reused writer | Existing cold-start coordinator; optional verified-canonical/unknown-supplement call from obligation_execution | Default behavior unchanged; no cold-start coordinator or Real Madrid operation invoked |
| international_store / PRODUCTION_LEGACY reused writer | Existing international collectors and controlled adjudication | Existing restrictions on known/legacy facts remain; no alternative writer |
| reconstruction.archive and legacy / preserved compatibility helpers | Existing tests and historical callers | Retirement requires caller/rollback verification and a separately authorized migration |

Caller audit: reconstruction acquisition/parsing calls flow through SourceService; direct HTTP calls are confined to the reused transport. Obligation execution coordinates evidence and established writers rather than implementing a provider parser or canonical database writer. Existing production modules are neither moved nor deleted.

New code repairs demonstrated locator, request-identity, accepted-status and target-specific exclusion defects and adds bounded orchestration/test coverage. Provider source identity remains separate from canonical identity. Sky may use a retained accepted request binding plus independently checked ordered fixture tuple; it explicitly does not claim a returned native resource ID. Parser version changes invalidate parse caches.

Duplication risks remain in older historical orchestration and legacy generated fixture/name keys. They are recorded rather than retired. Defer production migration, broad collector consolidation, old-helper removal, additional provider formats and national membership expansion until their evidence and callers warrant a separate scoped change. No whole-repository cleanup is a condition of this checkpoint.

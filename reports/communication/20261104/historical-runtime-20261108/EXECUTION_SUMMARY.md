# Historical runtime execution — 2026-11-08

The mandatory [runtime audit](../runtime-agent-independence-20261108/RUNTIME_AGENT_INDEPENDENCE_AUDIT.md) was published at2721587 and independently verified before this development resumed. Its verdict remains **PARTIALLY AGENT-INDEPENDENT**. Gate B remains formally ACCEPTED; historical units remain **0/24 accepted**. The earlier current website release is unchanged.

## Implemented and exercised

Local development candidate `7d50d4ce2590a86572193af4578187c99991ee90` adds `python -m ops.cloud.historical_unit`: bounded archive replay, a project-local durable per-unit SQLite checkpoint, exclusive job lock, body-integrity verification on reuse, parser-version-sensitive task identity, existing accepted fixture/source binding, shared `identity_registry.lookup` and `roster_initialization.initialize`, plus atomic private result output. It uses **SourceService**, not a parallel acquisition engine or provider parser. It never writes canonical facts, performs live collection or promotes production.

A completed source task is committed before the next starts. An actual CLI run processed20 source tasks and then failed during the accepted-catalogue read because the connection lacked the existing row-factory contract. After correction, the same workspace resumed93 tasks and reused20; a subsequent zero-task-budget run reused all113 with zero parser calls. Previous work was retained throughout. The synthetic test suite independently covers task-budget interruption/resume, deduplication, archive corruption, parser changes, unit-scope collision, exclusive ownership, prohibited live policy, and wrong ordered team/kickoff bindings.

The command is deliberately still **archive replay plus observed-union evaluation**, not the completed historical lifecycle. A root source URL discovery/pagination scheduler, complete-catalogue certificate, evidence-routing/adjudication integration, player-match/national execution and full unit recovery orchestration remain work. No fake completion follows merely from a zero process exit or no remaining replay tasks.

## Actual observed-union checkpoint

[Machine-readable execution](ACCEPTANCE_SUMMARY.json). The113 retained PSG detail responses comprise72 full native own-team squads and41 explicit unavailability responses. No new HTTP request was made; existing third-batch accounting remains348/500 requests,111,189,452/200,000,000 bytes conservative upper bound,38/80 catalogue,231/240 squad,48/120 identity operations. No RMA requests. Source access restrictions remain unchanged.

| PSG unit | Existing admitted fixture subset | Full accepted-bound squads | Missing squad coverage within subset | Observed mapped players | Unresolved player×fixture bindings | Other fixture-binding holds | Accepted? |
|---|---:|---:|---:|---:|---:|---:|---|
|2024/25 H1|22|1|21|20|0|1|No|
|2024/25 H2|35|16|19|27|5|0|No|
|2025/26 H1|24|24|0|28|43|0|No|
|2025/26 H2|30|30|0|30|52|1|No|

These counts describe accepted fixture subsets, not complete competition scope. All four runs explicitly set catalogue completeness false. Fixture/date/team holds and raw-name certificate reuse debt remain explicit source work; none becomes human Pending or a change to accepted roster/match facts. Prior verified identity conclusions are preserved; strict lookup on every original native packet is a separate operational handoff requirement, not a refutation of those conclusions. All four repeat populations and holds were identical; zero new canonical writes and zero HTTP requests.

The prior interrupted fixture-only operation had in fact completed:111/113 source fixture candidates are accepted/exported through existing `canonical_adapters.observe_fixture`, `quality_pipeline.process_fixture` and `quality_fixture.export_accepted_catalogue`; two team/time binding cases remain held. Old canonical field values, appearance/membership tables and source database are preserved; repeat logical rows identical; new lifecycle states VERIFIED, not STABLE. These111 fixture facts do not establish full rosters or period completeness.

For the other20 club-period units, preserved source progress remains unaccepted; this command has not yet certified them. Do not infer coverage for a club from another club’s execution.

## Regression and publication boundary

Ten new focused tests passed. Working tree regression:585 selected,527 passed,58 existing hosted-only skips,zero failures/errors. The three pre-existing dirty parser/test files remain unchanged and excluded from the code candidate. To verify the exact committed candidate without touching them, a separate process loaded precisely those three modules from Git HEAD in memory; all other tracked files matched HEAD: **584 selected,526 passed,58 hosted-only skips,zero failures/errors**. [Exact receipt](COMMITTED_REGRESSION.json). Existing SQLite ResourceWarning debt remains; no failures were suppressed.

[Complete code publication manifest](DEVELOPMENT_PUBLICATION_MANIFEST.json): two outgoing commits548bf4→7d50d4c above published1c397ff; four code/test files, every intermediate version and final SHA-256 reviewed. No code push has been attempted under this checkpoint; exact publication authorization is required under the established candidate-publication boundary. No private records, raw sources, databases, exports or local paths are in that payload. Hosted acceptance for584 tests, including all58, is **NOT EXECUTED** for7d50d4c. The earlier [570-test run](https://github.com/Strange-Noah/Futograma/actions/runs/42359045233) cannot certify it.

## Architecture and next work

Call graph: tracked `historical_unit` → SourceService/EvidenceStore → existing source parser → durable task receipt; existing `quality_fixture.export_accepted_catalogue` + source certificate → strict existing identity registry → existing roster initializer. It does not add a writer, source provider adapter, speculative provider abstraction or copied pipeline version.

Next: connect routine source discovery and admissibility/certificate routing using existing components; retain precise external access/unavailability and budget states; resolve remaining native-packet certificate handoffs through existing identity mechanisms; invoke original downstream pipeline only from independently accepted population inputs. Add ordinary interruption/repeat acceptance for those connections before scheduling. Do not interpret initial population as a contractual roster, search never-observed players exhaustively, or restart accepted Gate B.

[Architecture status](ARCHITECTURE_STATUS.md), [handoff](HANDOFF.md). No deployment, main merge, production pointer/schedule change, paid operation, RMA work or cleanup occurred.

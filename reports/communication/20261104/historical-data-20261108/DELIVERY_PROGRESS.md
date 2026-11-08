# Historical six-club data delivery progress — 2026-11-08

The corrected delivery requirements supersede the earlier plan to complete autonomous historical orchestration. **Historical acceptance concerns a complete, correct database; a reusable unattended historical acquisition framework is not required.** No further expansion of `historical_unit` was made. Gate S/Gate B remain accepted; historical unit acceptance remains **0/24**. Current/future production autonomy must later be demonstrated through the actual scheduled GitHub Actions workflow, not inferred from isolated tests or this historical work.

## Actual data construction

[Isolated data acceptance](DATA_ACCEPTANCE.json): through the existing fixture contract, identity verifier/registry, minute_policy, original appearance writer and normal exporter, the preserved candidate gained **71 historical PSG matches and1,432 player-match/squad rows**. Of these, **1,070 have verified direct source-reported numerical minutes;362 retain unknown minutes**. One additional source identity remains held. No unknown bench observation was changed to unused or zero. Two documented native-ID/name connections were verified by the existing P1 verifier and persisted through its existing alias writer, with repeat no-op and all old rows preserved.

The initial private candidate failed the unchanged exporter’s 11-starter check because some raw names had not yet reached existing identity certificates. That candidate and its evidence were retained. After the two evidence-backed identity connections and a complete-XI precondition, the new isolated candidate passed SQLite integrity/FKs, preservation of every original row, logical repeat equality, normal snapshot/export and ten period timelines. Original source database and production are unchanged. Management rows added:0; frozen historical rosters:0. These are accepted individual working-candidate facts and compatibility results, **not full-period or release acceptance**.

The existing PSG parser now exposes its actual `minsPlayed` field through shared `minute_policy`; invalid/duplicate values are rejected, extra-time reports remain un-clamped, missing bench stats remain unknown, and explicit substitution with missing minutes retains used status without invented minutes. This is a narrow repair of an existing format connection, not a historical-only downstream pipeline. Eleven focused PSG tests pass; full local candidate:590 selected,532 passed,58 existing hosted-only skips,zero failures/errors.

## Source acquisition and investigated transitions

[Reconciled source ledger](SOURCE_ACQUISITION.json), [source identity coverage](SOURCE_IDENTITY_COVERAGE.json). Fourth free batch: **103 HTTP requests,28,206,552 bytes**, within its internal200-request/100-MB operating ceiling. Raw request receipts independently reconcile to9 robots requests and94 target requests;98 attempted source operations include four whose target was not requested because policy/robots checks could not complete. Cumulative batches:903 requests;265,407,447-byte conservative upper bound, retaining the earlier interrupted-response reserve. These ceilings are operational safeguards, not monetary budgets or total-project authorization. No paid resource or Real Madrid managed-club acquisition.

A single official UEFA league-phase catalogue disclosed six clubs × eight fixtures. Shared games were deduplicated to42 reports. All42 public full-time PDFs were acquired through SourceService; layout-checked source XI/bench labels cover48 managed-club fixture observations. Additional ordinary public match pages supplied exact native match IDs/status/UTC. All42 report dates agree with their matching official page dates. No API was requested or page API credential used. This is complete source coverage of that **2024/25 Champions League league-phase subset**, not the whole domestic/cup/season catalogue or complete canonical identities.

| Investigation | Before → actual disposition | Limits |
|---|---|---|
| Native PSG direct minutes | Raw stats not connected →1,070 verified numerical working facts through existing writer |362 unknown preserved; no full-period acceptance |
| Raw-name handoff | Two source-ID/name variants unresolved → verified by existing P1 evidence rules |One other native packet identity remains source work; accepted earlier identity conclusions not revoked |
| Report extraction |16 tentative layout holds → all42 source reports layout-checked |15 concerned disciplinary asterisks; one was a visually confirmed17-player opponent list. No arbitrary18-player minimum and no human Pending |
| Shared UCL source work | Known official fixture IDs →42 deduplicated reports/48 target observations with date-coherent headers |Identity and canonical admission are separate obligations |
| PSG competition scope | CWC absent from earlier season-directory subset → official CWC schedule/result/line-up sources retained |Complete CWC catalogue and fact admission still required; no absent match fabricated |
| Source transport | Barça historical subdomain TLS EOF; legacy PSG robots301; FFF robots302 to HTTP → specific infrastructure/policy limitations |No weakened TLS, non-HTTPS follow, credentials, proxy or forbidden endpoint workaround; not evidence of no football matches |

UEFA source identities are under strict existing lookup, not name-only creation or automatic merging. Each of six clubs has six observed UCL source squads in2024/25 H1 and two in H2. Unmapped entities remain source work, not human Pending or resolved backlog. Adjacent historical periods remain independently constructed from observed XI/bench unions; no current roster seed, inferred departure or exhaustive search for never-observed players.

## Brief architecture disposition

| Disposition | Finding |
|---|---|
| KEEP | Gate S SourceService/accepted adapters; production collection/normalization/adjudication/writer/export; original R1–R6; `historical_unit` only for demonstrated archive replay/checkpoint/recovery |
| TEMPORARY | Isolated historical batches, PDF extraction/research and proof assembly; they call existing SourceService/policies/writers and preserve provenance |
| REDUNDANT | Some private replay/control wrappers overlap the kept replay helper; document overlap and stop expansion. No new authoritative collector, resolver, writer or audit engine was found |
| DEFERRED | Full unattended historical orchestration and future cross-season R1 automation. No new scheduling, R1 logic, tests/schema/config or empty module for that feature |

No broad refactor, retirement or deletion was performed. Source-related code changes and private data construction remain separate from production/runtime/deployment work. See the single planning TODO in the [updated handoff](../historical-runtime-20261108/HANDOFF.md).

## Code publication and remaining work

Exact reviewed local code candidate `9c7dfac0e736eebf566c3bf9755b5ad98f4a8aae`, two commits/four files above published7d50d4c: [full manifest](CODE_PUBLICATION_MANIFEST.json). The explicitly authorized first normal Git push failed `Empty reply from server` (128). The separately authorized single retry failed GitHub443 connection after75006ms (128). Independent GitHub reads after both failures show the historical branch still at7d50d4c. No further attempt or alternative route was used. Git supplied no request ID because no HTTP response was available. [Sanitized attempt record](PUBLICATION_ATTEMPTS.json). **590-test hosted regression NOT EXECUTED** for9c7dfac; the earlier [584-test exact run](https://github.com/Strange-Noah/Futograma/actions/runs/42391943315) does not certify the new parser. Local historical data work continues despite transport failure.

Next data work: bind remaining actually observed source entities, complete independent domestic/cup/CWC catalogues and squads, admit recovered facts through original mechanisms, then run scoped R1–R6 and cross-period historical investigations and repeat/export acceptance. Future cross-period automation stays deferred. After all24 units pass, use the existing separately gated website release/rollback process. Actual unattended current-season production verification is a later separate task; full autonomy remains unverified. No production/site/pointer/schedule change, public-main merge, paid operation, Real Madrid onboarding or cleanup occurred.

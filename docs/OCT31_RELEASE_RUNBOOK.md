# October 31 release preparation

This branch is a release candidate. No collection enablement, main database promotion, public Pages release or handover has been authorized. The first formal ingestion guard is 2026-10-31 16:00 Asia/Shanghai (08:00 UTC). Time alone never grants authority.

## Shared policies

`roster_policy` and `roster_cohort_current` separate FIRST_TEAM, YOUTH, UNKNOWN and EXCLUDED. Verified youth/development affiliation is enough for YOUTH; continuous first-team management is not required. Current established first-team status wins over age/U21 eligibility. Youth club appearances and historical international facts remain available; only FIRST_TEAM is actively scanned for new international appearances. A youth player may later be promoted by actual first-team source evidence.

`minute_policy` uses an inclusive five-minute maximum spread. Priority: official reported minutes, retained accepted facts, FotMob reports, Torlytics reports, Sky reports, derived clocks; ties use stable provider/source/value ordering. It never averages. Played versus did-not-play contradictions are rejected before numerical tolerance, including zero-minute used substitutes. Original observations and selected source are retained in source facts, raw evidence and policy decisions. Known accepted values have priority over lower-priority new observations; missing values may be supplemented through an audited revision. Original national-reference payloads are immutable.

International discovery follows actual personal histories and explicit national career teams, never nationality. There is no continuous-calendar completeness requirement. Time between international windows is not a source error. No new coverage certificate infrastructure is introduced. Newly established first-team players bootstrap actual appearances from the dynamically calculated current season, then use normal incremental scan checkpoints.

## Daily execution

`ops.cloud.platform update` invokes `squad_pipeline.cloud.driver`, which calls the shared `cloud.incremental.run`: bounded official first-team refresh, exact-ID personal-profile alias refresh and international history/fixtures, then club acquisition/retry with the refreshed shared registry. Runtime UTC bounds use 72 hours plus missed-checkpoint recovery; durable retries may be outside the normal window. Youth-only players are excluded from active international discovery. New stable source identities seen in club sheets remain in their real provider namespace. Plausible existing canonical people block duplicate creation and require an identity bridge; new source-only certificates never authorize cross-provider name matching or first-team membership.

The controller has no fixed historical acceptance window or dependency on old acceptance assets. Credentials and runtime files are available only from GitHub. Evidence is uploaded and verified before snapshot CAS. Full schema/count/version export validation precedes CAS; uploaded bytes are restored and verified. The private workflow template is `ops/cloud/workflows/data-platform.json`, with the reviewed exact code SHA replacing CODE_SHA_PLACEHOLDER. It remains an uninstalled template until final approval.

## Prepared release operations — final approval required

1. Publish the reviewed code to the production code ref without force-pushing. If fast-forward is unavailable, review a new merge commit and rerun exact-commit acceptance; do not silently change the code pin.
2. Install the private workflow with the accepted code pin while collection stays disabled. Preserve UTC cron `0 8 * * *`, non-cancelling concurrency and runtime first-run/authority guards.
3. Run `python -m ops.cloud.release_ops` only on a GitHub-hosted runner with explicit exact code approval, reviewed source ref/head, expected production head and expected database SHA. Approval token is PROMOTE_REVIEWED_PHASE3. It only promotes a snapshot; it does not enable collection or publish Pages. The production data commit/version is assigned by CAS, so do not reuse the acceptance data commit as the eventual production commit.
4. Export the promoted version with `ops.cloud.platform site`, then approve the public Pages release. Set FUTOGRAMA_SITE_BRANCH=site and FUTOGRAMA_SITE_DATA_REF=main, and pin FUTOGRAMA_RELEASE_CODE_SHA to the accepted frontend commit. The build must verify the production pointer, manifest and browser payload refer to the same database and exact code version. It must build from GitHub source.
5. Only after separate explicit approval set authority handover CONFIRMED and enable the collection variables. Confirm the first actual formal update and the published database version after the guarded October 31 slot. Before that, all tests remain isolated prelaunch acquisition, never formal ingestion.

## Rollback

Keep the current and required previous immutable snapshots and the prior production code pin. The release acceptance rehearses candidate publication and restoration of the actual production backup on a new isolated branch. It never modifies main. Synthetic and actual GitHub CAS/failure tests remain enabled.

For an approved emergency rollback, first disable collection; run `release_ops --operation rollback --source-ref main` with ROLLBACK_REVIEWED_PHASE3 approval, exact tool code approval, expected current main head, and reviewed previous database SHA. This path restores the previous production bytes without requiring collection to remain enabled and uses a non-force CAS. It retains the replaced snapshot as the new recovery pointer. Restore the paired workflow/code/site version explicitly; never force-push or rewrite history.

The pre-release public website is a Phase 2 preview, separate from the authoritative production database: its source commit is 6812ebeb0490ef698087281c32fe8c5eee705b9c and its data refs are site-mvp / acceptance/phase2-data. These refs remain unchanged during preparation. Public Pages now supports an explicit frontend code pin for rollback without rewriting main history. Do not confuse restoring the previous public preview with restoring the production SQLite baseline.

## Reporting

Report new failed runs promptly, including skipped dependencies. A green workflow does not erase actual missing fixtures, source restrictions, unknown roster evidence or business validation failures. The readiness report must identify code/data versions, live-source issues, rollback assets, public-site status and remaining final approvals separately. No local Mac service is required. Cleanup cannot touch local files; unresolved cloud evidence is retained.

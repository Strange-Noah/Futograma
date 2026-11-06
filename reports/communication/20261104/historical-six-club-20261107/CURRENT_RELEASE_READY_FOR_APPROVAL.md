# Exact current six-club release — ready for production review

Status: preparation PASS; production NOT AUTHORIZED. No production mutation has occurred. This release excludes all unaccepted historical development and optional frontend changes.

| Item | Exact review target |
|---|---|
| Public application code | `9acd42e73882020dca5f7820c80c61cc7bc8b187` |
| Isolated marker-free source | `acceptance/code-release-42263589840` |
| Source head | `997c16c29b32859549270e58e56c69a18067d237` |
| Source / current production SQLite SHA-256 | `dd49dddbce8f5ed1b7f5f878855f4937d13131cf4e95077f94b617e2b08aefc0` |
| Expected private production head before any change | `339c6976414809b1f75d226b67945f0b653a5141` |
| Private workflow code-pin-only commit | `aa8cdaa24ce321a6f7dc5f02ebc9bb3c15539754` (parent is the expected production head) |
| Private site current / previous | `35d81673cb6bb0dd04690d83e1845c0f3f8b56a4` / `93c56e9183878d39e8dd7699a11a60987dcf218e` |
| Existing code / rollback code | `666255afe5bb565f82156ba9908713a5bcbab8ed` |

[Release commit](https://github.com/Strange-Noah/Futograma/commit/9acd42e73882020dca5f7820c80c61cc7bc8b187) · [500/500 exact release regression, including all 58 hosted-only tests](https://github.com/Strange-Noah/Futograma/actions/runs/42236914802) · [successful independent smoke recovery](https://github.com/Strange-Noah/Futograma-data/actions/runs/42253702587) · [marker-free candidate preparation](https://github.com/Strange-Noah/Futograma-data/actions/runs/42263589840).

The smoke test's administrative marker snapshot is excluded. The selected candidate retains the exact production SQLite bytes, schema and football facts, adds no marker, and has both real current and previous assets independently downloaded/restored and hash/integrity/FK verified. Its actual isolated export passes coordinated code/data/browser gates; all 2,887 record keys and nine core football fields agree with the same production database exported at the same code. An additional comparison against the actual current site backup confirms that the complete records, clubs and players objects are identical, not merely selected fields. The only new root field is an empty quality_issues array; audit dossiers are absent. Six team → player → match browser paths pass on this exact export (16/17/14/15/16/19 displayed rows for sampled players). Existing zero/unknown semantics and source notices remain.

Actual public manifest was fetched once and exactly matches the backed-up current private site. Current and previous site transport files are backed up and Git-blob verified (10 files, 33,239,773 bytes). Current and previous data backups were also actually restored. No backup cleanup or deletion is proposed.

## Proposed operations — require separate exact authorization

1. Recheck every source/production/site/code pin. Stop on any unexpected advancement. Using normal Git HTTPS without force, advance **private Futograma-data/main only** from the expected production head to the existing `aa8cdaa` pin-only commit. It changes exactly seven literals in two workflow files to the reviewed application code. It includes none of the later experimental preparation jobs. No public Futograma/main merge or update.
2. Run the existing registered private `release-operations.yml` promotion with `PROMOTE_REVIEWED_PHASE3`, the exact isolated source/ref/hash above, and expected production head `aa8cdaa`. The original writer must preserve the former current pointer as previous and independently verify uploaded SQLite. There is no collection operation.
3. Regenerate the private `site` transport through existing `data-platform.yml` mode `site`, using the reviewed application code and new current data head. Preserve site Git history.
4. Change only public `FUTOGRAMA_RELEASE_CODE_SHA` from the existing code to the exact reviewed code. Keep `FUTOGRAMA_SITE_BRANCH=site` and `FUTOGRAMA_SITE_DATA_REF=main` unchanged. Run the existing free Pages workflow.
5. Independently verify actual production pointers, backups, exported browser hash, public manifest and six-club navigation. Preserve existing records and warnings. If a coordinated release check fails, use the existing reviewed rollback operation and original pins, regenerating matching site transport; never force-rewind history or discard an advanced dataset.

The full machine-readable payload, workflow file hashes, checks and rollback targets are in CURRENT_RELEASE_PLAN.json. Any unexpected source/data advancement invalidates the fixed proposal; it cannot authorize overwriting newer football facts.

Risks are coordination of code/data/site pins and GitHub connectivity, not football-data transformation: database bytes and frontend file bytes are unchanged. Strict release gates keep mismatched builds from replacing the live website. The existing automated collection schedule is preserved; no immediate update/acquisition, historical data, RMA activation, paid resource or destructive cleanup is part of this proposal.

One isolated preparation run failed before any data operation because of missing Python import configuration; a minimal import-path repair was verified locally and the subsequent exact run succeeded. The initial separate manual workflow was not registered by GitHub (404); no run was triggered there. The same isolated operation was integrated into the existing registered workflow on the personal branch only. These preparation versions remain in Git history and are excluded from the production pin-only payload.

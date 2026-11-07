# Current six-club production release — executed 2026-11-07

The user explicitly authorized the exact coordinated release in CURRENT_RELEASE_PLAN.json and necessary rollback. This execution record supersedes the proposal's frozen NOT_EXECUTED / production_authorized=false fields. Gate B remains ACCEPTED; no baseline recertification was started.

**Release accepted for the authorized scope.** Deployment, exact public assets and coordinated data/code pins are CONFIRMED. Post-deployment interactive browser navigation is BLOCKED by two computer-use service timeouts before a browser tab/state could be obtained. This limitation is retained rather than reported as a passed test. The exact pre-deployment isolated export passed six-club team → player → match navigation; byte-identical deployed web files and unchanged football records support preserved navigation, with the live interactive check still unexecuted.

| Check | Actual evidence | Status / limits |
|---|---|---|
| Application | [9acd42e](https://github.com/Strange-Noah/Futograma/commit/9acd42e73882020dca5f7820c80c61cc7bc8b187), [500/500 exact regression](https://github.com/Strange-Noah/Futograma/actions/runs/42236914802), all58 hosted-only cases | CONFIRMED |
| Source preparation | Marker-free isolated source 997c16c; actual current/previous assets restored and verified | CONFIRMED; preparation only |
| Private workflow pins | main fast-forward 339c697 → aa8cdaa: two existing workflows, seven code-pin literals only | CONFIRMED; no schedule changes |
| Data promotion | [Existing promotion run](https://github.com/Strange-Noah/Futograma-data/actions/runs/42269800463), resulting head 26e369c3aa76a228573467422d1a092ccdc9d8a2 | CONFIRMED; prior current retained as previous |
| Site export | [Existing site run](https://github.com/Strange-Noah/Futograma-data/actions/runs/42270398938), site head a916d322bcaebb4b7d5353df971526181dea8945 | CONFIRMED |
| Public deployment | [Pages run 42271225043](https://github.com/Strange-Noah/Futograma/actions/runs/42271225043) | CONFIRMED success; logs show application checkout9acd42e |
| Independent post-deploy verification | Public manifest/browser hashes, three web files compared byte-for-byte with9acd42e; fresh private current pointer and release variable read back | CONFIRMED |
| Football facts | 2,887 records; full records/clubs/players/source_issues equal the prior published payload; SQLite hash unchanged | CONFIRMED; no new football facts collected |
| Rollback | Actual previous code/data/site assets retained; existing reviewed rollback path remains available | CONFIRMED retained; rollback not invoked |

Exact SQLite SHA-256: `dd49dddbce8f5ed1b7f5f878855f4937d13131cf4e95077f94b617e2b08aefc0`.
Exact browser SHA-256: `17874ce684d01aff25f766a5b1e6f605e4109c5480427840e5096a7674f7a74d`.

Pages workflow metadata still names the unchanged public-main workflow revision666255a. Its build logs explicitly verify checkout9acd42e and matching data26e369c; this is not a public-main merge or evidence of the old application being deployed.

[Live website](https://strange-noah.github.io/Futograma/). Historical development commits are excluded from this release. No historical data release, Real Madrid work, schedule change, paid resource or cleanup occurred. Historical production release still requires its own acceptance and authorization.

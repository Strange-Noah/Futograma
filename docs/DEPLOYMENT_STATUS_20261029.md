# Futograma deployment status — 2026-10-29

Website: https://strange-noah.github.io/Futograma/

**Published baseline website; production ingestion DISABLED; authority handover UNCONFIRMED. Cloud independence is not fully accepted.**

## Repositories

- Code: https://github.com/Strange-Noah/Futograma — public, main.
- Data: https://github.com/Strange-Noah/Futograma-data — private, main/current.json.
- Active public workflows: Portable regression checks; Futograma Pages (plus gated monthly health job).
- Private: Futograma data platform; read-only preflight and isolated evidence/safety workflow variants on acceptance refs.

## Versions

- Published source commit: `01e4167540f7aa36773848278db6b6a139a0d358`.
- Data workflow pinned source: `bc96e5d7e45f6d36494ff852c51b7128fdd18e1f`.
- Data baseline commit: `f81718a3473736e8d8c04ed482e3a5f067bd8454`.
- SQLite SHA-256: `56f1495f8c77385dc806607463e6b34ed811ef5b74552012c6aec07a0678c1ac`.
- Schema SHA-256: `994c24bfb767d08750fd79f9b158ffa94ab7892eb51d44fa4a8dd2485a2b2304`.
- Data version: `20261029T062929-728f8d0b3dfc`; generation 2.

Initial scope: six clubs, 2026/27 H1, 2026-08-12 through 2026-10-28; 75 physical match records, 176 players, 2,222 club player-match minute rows. This is not a 3–5 season database.

## Acceptance

| Check | Status | Evidence / remaining issue | Run or source |
|---|---|---|---|
| A1 | PASS | Fresh hosted checkout, Python install, 33 portable regressions; no production DB fixture required. | [Evidence](https://github.com/Strange-Noah/Futograma/actions/runs/40830417695) |
| A2 | PASS | Private seed downloaded, hash/schema/integrity checked; current and previous independently restored. | [Evidence](https://github.com/Strange-Noah/Futograma-data/actions/runs/40829483316) |
| A3 | FAIL | Hosted network is reachable, but selected samples contain two validation failures; this is not a six-club preflight pass. | [Evidence](https://github.com/Strange-Noah/Futograma-data/actions/runs/40830868464) |
| A4 | PASS | One hosted job persisted acceptance assets; a separate fresh job restored them without local files. | [Evidence](https://github.com/Strange-Noah/Futograma-data/actions/runs/40829702370) |
| A5 | PASS | Pages built from GitHub source; public URL HTTP 200 with Futograma branding. | [Evidence](https://github.com/Strange-Noah/Futograma/actions/runs/40830527200) |
| A6 | PASS | Published manifest data_commit, data_version and SQLite SHA match private current.json. | [Evidence](https://github.com/Strange-Noah/Futograma/actions/runs/40830527200) |
| A7 | PENDING | Manual dispatch and gates verified. A real scheduled event has not been observed; production remains disabled. | [Evidence](https://github.com/Strange-Noah/Futograma-data/blob/main/.github/workflows/data-platform.yml) |
| A8 | PASS | All executed jobs are GitHub-hosted Linux; no Mac tunnel/mount/SSH callback. Filesystem cleanup rejects apply; local launchd remains unloaded. | [Evidence](https://github.com/Strange-Noah/Futograma/actions/runs/40830417695) |
| A9 | PASS | Isolated previous-snapshot promotion and recovery; real corrupt upload does not advance pointer; simultaneous Git-ref writers yield one accepted and one rejected. | [Evidence](https://github.com/Strange-Noah/Futograma-data/actions/runs/40830516796) |
| A10 | PENDING | Code, baseline, evidence, website and recovery assets reside on GitHub and independent jobs succeed; complete operational acceptance remains blocked by A3 and unobserved A7. | [Evidence](https://github.com/Strange-Noah/Futograma-data/releases) |

## Failed network preflight

No guard was weakened. The six-club run produced five selected match results: three unchanged dry-run validations and two rejections. No Barcelona detail sample was selected in the recent window, so six-club coverage is also not demonstrated.

- Manchester City v Brighton, 2026-10-28: `SKY_ROSTER_SIDE_MISMATCH`.
- Bayern v Magdeburg, 2026-10-28: `UNSUPPORTED_COMPETITION:None`.

The repeated failed preflight remains FAIL even though its evidence-preservation step succeeded. Private source bodies and diagnostics are retained in a separate unresolved-evidence Release, with upload readback verified. No bad match was accepted, and main/current.json remained unchanged. Parser/provider-side semantics require investigation before production approval.

## Security, retention and protection

- Interactive GitHub CLI OAuth was used only for authorized administration/publication. No account OAuth token is stored in Actions.
- Private workflows use their own repository-scoped GITHUB_TOKEN; a dedicated preflight variant uses contents:read. Write scope is needed only by bootstrap, isolated persistence and persistent-evidence publication.
- Public Pages reads the private derived site branch and baseline pointer using a read-only deploy key. Pages deployment and a separately gated monthly health job have their own minimal permissions.
- The monthly health receipt is installed to address long-lived public schedule inactivity; it cannot write until explicit production enable and handover flags are approved.
- Both repositories were configured and read back at seven-day log/artifact retention; Pages artifacts use one day. This setting is not a claim of retroactive shortening of runs created before configuration.
- No Release was deleted. Retention testing was dry-run only. Current, previous and unresolved evidence remain present. Failed test drafts are retained. Site Git history is derived text JSON and must be monitored for growth; no history rewrite was performed.
- 159 unresolved holds remain unresolved: seven metadata-only failed leads; 152 body-marked holds include six empty failed-response bodies and 146 nonempty bodies. A stored body/hash is not substantive identity verification.
- Original local files were not moved, deleted, truncated or overwritten. All 43 original SQLite hashes and reviewed source hashes remain unchanged. The previously unloaded launchd service remains disabled.

## Production state

`FUTBOL_CLOUD_ENABLED=false` in both repositories. `FUTBOL_AUTHORITY_HANDOVER=UNCONFIRMED`. No newly ingested or corrected production match facts were written; only accepted baseline facts were migrated. Formal collection was never invoked. Earliest permitted formal ingestion remains **2026-10-31 16:00 Asia/Shanghai**, not a deadline. Daily data cron UTC `0 8 * * *` is installed but gated. No production pointer promotion or rollback occurred during acceptance; the pointer was initialized only by the authorized bootstrap.

The next step is to resolve the failed preflight and complete outstanding acceptance. Do not enable ingestion or declare a production authority handover based on this report.

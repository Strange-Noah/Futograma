# Futograma

[Live website](https://strange-noah.github.io/Futograma/) · Football player match histories, managed rosters and recorded club/national minutes.

Futograma runs entirely on GitHub Actions, private SQLite snapshots and static GitHub Pages. No Mac, local folder, server process or online database connection is required. The production application/website is pinned to `6e3078bb15ca2e83323e80af86f7234e54910fd8`; separately CI-verified deployment/reporting tools are pinned to `1fbdf1f445c9f07e3ab3ca6e528d71073fb1137f`. This branch proposes documentation cleanup only; it does not change those production pins.

## What the website shows

Choose a club and player to inspect recorded club and national-team matches. Current first-team management is distinct from match participation. YOUTH players are explicitly labeled and hidden from the default first-team cohort; their existing facts remain available. Unknown identity/membership and pending sources stay visible as uncertainty. Club-only minutes are never represented as complete personal workload; national history is not claimed complete. A human-authorized roster decision changes management, never appearances or minutes.

## Architecture

GitHub Actions → restore private authoritative SQLite → discover and validate → immutable snapshot + atomic pointer → version-bound static JSON → GitHub Pages.

- Public code: `Strange-Noah/Futograma`; complete website source in `web/`.
- Private production data: `Strange-Noah/Futograma-data`, `main/current.json`, verified Release assets.
- Private derived transport: `site` branch; it is not a database authority.
- Private durable reports: `production-reports`, `reports/production/latest.json`, `runs/<run-id>.json`, `attempts/<run-id>-<attempt>.json`, and scheduler configuration in `scheduler.json`.
- `acceptance/*` branches are isolated tests, never a production data fallback.

## Schedule and operation

Daily collection is08:00UTC /16:00Asia/Shanghai, with72-hour recheck, missed-checkpoint catchup and persistent due retries. The first run onOctober31 is temporarily deferred to17:00Asia/Shanghai, enforced by the GitHub runner; subsequent days remain16:00. GitHub cron may be delayed. Manual GitHub entrypoints and durable checkpoints recover missed windows without needing a chat session. Pages has08:30/09:30/10:30UTC publication opportunities.

Both production repositories use `FUTBOL_CLOUD_ENABLED=true`, `FUTBOL_AUTHORITY_HANDOVER=CONFIRMED`. The private runtime variable `FUTOGRAMA_FIRST_FORMAL_RUN` records the effective initial deadline. Read durable GitHub reports for actual run outcomes; a deployed workflow or zero new matches alone is not collection success.

## Development quickstart

Use Python3.14.

```sh
python -m pip install -r requirements-cloud.txt
python -m unittest discover -s tests/cloud -v
node --input-type=module --check < web/app.js
```

Tests use synthetic fixtures. Production execution deliberately rejects local machines. Operate through the private repository Actions UI; do not run production commands against a local database. For the operational reporting additions, use the pinned tooling branch until the separately reviewed consolidation is merged.

## Repository map

| Path | Purpose |
|---|---|
| `squad_pipeline/` | Identity, affiliation, source acquisition, appearance/minute validation |
| `squad_pipeline/cloud/` | Hosted guard, snapshots, provenance, incremental controller and export |
| `ops/cloud/` | Orchestration, release/recovery tools, CI/version gates and browser acceptance |
| `web/` | Complete static HTML/CSS/JS source |
| `.github/workflows/` | CI and public Pages configuration |
| `tests/cloud/` | Portable regression suite |
| `docs/` | Architecture, deployment, recovery and this audit |

See [architecture and maintenance](docs/PRODUCTION_ARCHITECTURE_AUDIT_20261031.md) and [dependency inventory](docs/audit/file-classification-20261031.json). Historical helper modules remain when actual imports reuse their functions; absence from one entrypoint is not deletion authorization. No production files, Git history or backups are moved or removed by this documentation branch.

## Sources and limitations

Public official club sources and stable provider identities support management decisions. FotMob discovery and corroborating public match sources support match acquisition. Provider availability and identity conflicts can leave quarantined records. Minute differences≤5 follow deterministic priority only when appearance status agrees; contradictions remain conflicts. Preserve selected and original observations.

SQLite/current and previous recovery assets, identity aliases, fact/source indexes and unresolved evidence are permanent. Runner files expire with their jobs. No local cleanup or automatic release pruning is enabled by this audit. Never infer source completeness from structural roster completeness.

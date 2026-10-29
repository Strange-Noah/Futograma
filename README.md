# Futograma

Football match participation and club workload, hosted entirely on GitHub.

Public application: Strange-Noah/Futograma. Private SQLite baseline: Strange-Noah/Futograma-data, main/current.json and verified Release assets. Production ingestion remains disabled; authority handover is unapproved. Earliest formal ingestion: 2026-10-31 16:00 Asia/Shanghai. Daily cron 0 8 * * * is UTC 08:00.

Private data Actions check out pinned public code using their own repository-scoped GITHUB_TOKEN. Public Pages uses a read-only deploy key to retrieve derived static JSON and its version pointer. No account-wide OAuth token is stored in Actions. SQLite and raw evidence never enter this public repository or website artifacts.

- squad_pipeline/live_*.py: acquisition, parsing, identity and minute validation.
- squad_pipeline/cloud/: SQLite schema, immutable snapshots, CAS publication, provenance and recovery.
- ops/cloud/platform.py: private hosted orchestration and isolated acceptance.
- ops/cloud/workflows/: source workflow templates installed in the private repository.
- .github/workflows/: portable CI and public GitHub Pages.
- web/: complete static website source; no local build or manual HTML upload.
- tests/cloud/: synthetic regression, fixture cleanup restricted to hosted runners.

See docs/DEPLOYMENT.md and docs/RECOVERY.md. Website statistics are recorded club minutes, not complete personal workload. Unverified membership remains unknown. All runners are GitHub-hosted Linux. No Mac mount, tunnel, server or local file is used. Runner files expire with jobs; filesystem cleanup is disabled. Release cleanup is dry-run only during deployment. No local original is deleted by deployment.

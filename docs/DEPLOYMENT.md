# Futograma deployment

Public code: Strange-Noah/Futograma/main. Private data: Strange-Noah/Futograma-data/main/current.json plus SQLite Release assets. The data workflow installs pinned code with its own contents:write GITHUB_TOKEN. Public Pages has contents:read and pages/id-token:write and a read-only data deploy key. No user OAuth token is saved as a workflow secret.

Upload the authorized seed once as private Release futograma-seed-20261029, including SQLite gzip, source/migrated SHA and seven-row evidence review. Hosted bootstrap verifies hashes, schema and facts, uploads two readback-verified snapshots, and initializes current.json. It refuses an existing authority. This is baseline migration, not match ingestion or authority handover.

Dispatch verify, acceptance, preflight and site after bootstrap. Acceptance creates acceptance/<run-id>; a dependent fresh job restores/promotes previous on that isolated ref. Production main/current.json must remain unchanged. Manual recover_acceptance accepts an earlier numeric acceptance run id. Public Pages then verifies data commit/version/SHA against private main/current.json and deploys only source HTML/JS/CSS and derived public JSON. Site branch is derived transport, never database authority.

Keep FUTBOL_CLOUD_ENABLED=false and FUTBOL_AUTHORITY_HANDOVER unconfirmed. Separate FUTBOL_REMOTE_ACCEPTANCE_ENABLED permits acceptance only. UTC08:00 schedule is installed but cannot ingest; UTC08:30 Pages refresh does not collect matches. A7 stays PENDING until an actual scheduled event is observed. Log retention is configured to seven days, Pages artifact to one day.

The seven metadata-only holds are failed-source leads, not verified originals lost during migration. Their URLs, player bindings, request failures and unknown status persist; they support retry, not substantive membership proof. The other 152 unresolved holds contain hash-verified original bodies. Full seven-row review stays private. Future verification requires permitted dated official evidence. No unknown has been promoted.

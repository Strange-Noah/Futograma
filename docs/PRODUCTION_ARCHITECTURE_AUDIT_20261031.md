# Production architecture and documentation audit — October31

## Actual production arrangement

Application/frontend6e3078b; release/reporting tools1fbdf1. Production source is GitHub-only. The private daily workflow checks out both immutable pins. Tools currently live on a protected operational branch rather than being consolidated into default-main source; preserve that ref/commit. An eventual consolidation needs CI, real isolated acquisition/recovery and exact website/snapshot compatibility validation before changing pins. This audit branch does not promote code or data.

## Findings and minimum next changes

| Priority | Finding | Isolated proposal | Evidence / protection |
|---|---|---|---|
| P0 docs | Default README still says authority unapproved and phase2 acceptance transport | Replace README with accurate production introduction, quickstart, repository map, scheduling, reports and limitations | Current API variables and successful production Pages runs; no workflow/data change |
| P1 organization | Operational tools are pinned on a separate branch, while source templates can be older than installed workflows | Consolidate exact verified tools in a future release and identify installed manifests as current until then | Preserve1fbdf1 and runtime6e; do not switch production to this audit head |
| P1 docs | Historical acceptance documentation can look like current operational instructions | Label it historical and provide one current runbook index; retain source evidence | No files moved/deleted here; references must be updated and tested before relocation |
| P1 boundaries | Older experiment-named modules are imported by validated ingestion | Keep imported libraries in place; split shared functions only if a separately tested change is useful | AST inventory includes function-local imports; filename/date alone never establishes obsolescence |
| P1 reports | JSON reports must stay outside authoritative main so commits cannot invalidate site data-head checks | Retain production-reports canonical paths and byte-verified uploads; document mode and completion states | Existing live reporting and isolated failure finalizer tests |
| P2 source failures | Accepted dataset retains source/identity gaps | Preserve queues and evidence; resolve through ordinary validated ingestion, not README cleanup | Do not manufacture missing matches or silently change aliases |

## Runtime flow

1. Hosted guard refuses local production access; restore main/current.json and verify actual Release bytes/SQLite integrity.
2. Runtime uses configured first deadline and daily72h window expanded by durable checkpoints. A one-time hosted wait admits the initial17:00 run; subsequent daily16:00 slots are unchanged.
3. Shared identity and management policy drives current first-team/youth selection, independent of historical appearances. Raw-source replay and deterministic minute reconciliation retain observations.
4. Preserve unresolved evidence before CAS publication. Any critical version/hash/upload gate stops replacement. Current→previous retains recovery.
5. Export uses configured current-roster timezone; match dates remain Europe/Madrid. Browser payload hash, database hash, data version and source commit are checked together.
6. Private site transport and public Pages publish from GitHub source. Repeated publication opportunities handle ordinary cron delays.
7. Durable per-run and per-attempt JSON record completion, changes, unresolved sources, retry/publication outcome and schedule state; independent finalizer handles a failed/timed-out operation job.

## Inventory method and limits

The attached machine inventory classifies every textual source file from the approved application plus active tools by explicit entrypoints, Python AST imports, test/workflow/website/config paths and conservative review status. Static reachability includes optional operation branches and cannot prove a file unused. The real hosted acceptance/recovery runs substantiate independence from local data. No file is automatically archived.

## Proposed structure (not applied)

Keep production code at its current import paths. Add a current documentation index and clearly labeled historical/acceptance documentation. Only after dependency references, full regression, hosted acquisition and independent recovery pass may historical material be copied into a separate archival layout. Do not migrate or delete the user's local directory; do not rewrite Git history or remove production backups.

## Acceptance before any later structural promotion

Fresh checkout + dependency install; complete tests; actual network/replay; identity and historical fact invariants; report failure path; current/previous restore; exact static website/database version; and inspection of every changed import/config/workflow path. Documentation-only edits do not authorize production source pin changes.

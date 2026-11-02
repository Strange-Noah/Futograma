# Canonical fixture reconciliation (isolated candidate)

Production remains on its existing pins until separately authorized. This change
is additive; never regenerate accepted match IDs, derive appearances from source
tasks, or interpret recovered fixture metadata as verified player identity.

## Entry points and migration

`squad_pipeline.canonical_fixture.bootstrap(db, snapshot_sha256)` imports accepted
club and international fixture IDs unchanged. DDL and migration version are in
`canonical_fixture.py`; callers own transactions. Apply only on a restored isolated
snapshot, with `foreign_keys=ON`, integrity checks and a retained before copy.
No old fact, identity, roster or source-evidence table is rebuilt.

`reconcile(db, observation, create=False, registered_id=None)` is the shared core.
`canonical_adapters.observe_fixture` serves live club ingestion and historical
replay; `national` serves international_store.put_match. Registered legacy IDs
remain valid. New observations bind by an existing provider ID or unique exact
UTC kickoff, ordered verified team keys, competition and compatible edition/stage.
New-source provisional national IDs cannot bypass this search. Unknown or
ambiguous entities remain issues. No fuzzy label matching or nearby-date merge.

Team and competition aliases require retained evidence. National cross-provider
team bridges use explicit `verified_key`, backed by the source evidence ID;
name normalization alone is insufficient. Legacy accepted tokens and provider
links are retained, including nullable opponent IDs resolved from consistent
non-null records in the same verified snapshot. Conflicting retained IDs stop
migration. Legacy unknown-team anchors are not new independently verified IDs.

## Facts, fields and failures

Canonical registry/claims contain fixture metadata. Existing club/international
appearance tables contain player facts. Retry queues and canonical_source_issues
contain source problems. Export renders only accepted appearance rows in player
timelines and emits issues separately. A fixture association never resolves an
unverified player. Accepted null fields may be enriched, never erased by nulls.
Conflicting scores remain explicit SOURCE_CONFLICT with original claims and
selection provenance. Same-provider kickoff corrections require verified event
binding, correction_basis=EXACT_PROVIDER_EVENT_ID_RECONFIRMED and a positive
monotone correction_revision. Without that evidence, changes remain conflicts.

Returned-match and lineup-ID errors are SOURCE_MATCH_ID_MISMATCH, separate from
player identity. Fixture-team/time/competition contradictions have fixture scope.
Cause-specific revalidation may rearm only supported competition parsing and
transient team-directory errors, within the existing retry-age policy. Diagnostic
reclassification leaves quarantine and attempt/history facts intact. ID checks
are never relaxed. No bulk resolution of the 201 wrong-event tasks is provided.

The 72-hour delayed-completion and roster/first-team/YOUTH policies are unchanged.
Website release and scheduled collection remain separately controlled.

## Reproducible acceptance and rollback

Run `python -m unittest discover -s tests/cloud -v` on a fresh checkout. Hosted
`ops.cloud.canonical_stage` requires an acceptance/* data ref, disabled cloud
authority for that job, the pinned baseline and archived response hashes. It
replays real club responses in both orders and twice, checks every old fact/source
table's semantic hash, accepted international provider mappings, independent
snapshot restore, and browser score/timeline output. `canonical_mismatch_replay`
checks all 42 archived URLs behind 201 tasks, with bounded public probes only.
All hosted jobs publish exclusively to isolated refs/releases, never main/Pages.
Isolated snapshots have ref-specific retention markers so production pruning
cannot accidentally remove acceptance or recovery assets.

Rollback of this isolated candidate is to discard its use and restore the pinned
baseline through GitHubStore.restore on a fresh runner; do not DROP tables or
mutate the protected local database. A future approved production rollback must
use the then-current expected data head and compare-and-swap promotion of a
verified previous snapshot plus its paired code/export version. No force-push,
local deletion or automatic production promotion is included here.

Known conservative boundaries: truly duplicate pre-existing canonical IDs are
not merged automatically; a future evidence-reviewed redirect/merge migration is
required. Kickoff corrections without stable source revision evidence stay held.
The diagnostic cluster has not yielded a working alternate FotMob route; query
parameters that still return a wrong ID are rejected. Archived evidence replay
is not proof that unavailable source tasks or all historical identities are solved.

# Independent reproduction procedure and count definitions
The development commit fc82acf41df1c0b843aeb16827abe4c75dc69bc9 is preserved locally and remains unpublished after its separate approval denial. This reporting branch deliberately does not include its code history. Public aggregate records let readers inspect conclusions; a full rerun also needs authorized access to that frozen code and retained private evidence. Public reports alone are not claimed to reconstruct private inputs.

1. Verify a clean unchanged candidate, the authorized root/canonical boundaries and the prior six-club reference hash against its private pointer/manifest. Never use a previous Real Madrid candidate as input.
2. Open the reference read-only; copy it to a fresh investigation-only database. Preserve source bodies and their metadata. Retain the July 1–November 4 inclusive 2026 cutoff used by the original probe; do not silently advance it to the investigation date.
3. Parse the same retained official FIFA HTML using reconstruction.calendar.fifa_windows. Record its actual output and the file-mtime observation-time limitation.
4. For each clubs row, instantiate RetainedAdapter with the original authorized archive root, invoke engine.run twice with max_attempts=500, and record discover/windows/opportunities/gaps entry order plus every resolve callback's path, outcome and underlying body hash. Do not change implementation predicates to improve counts.
5. Count unique members/fixtures/statuses returned by discover and compare first/repeat metrics with the archived probe-v4 result. Independently census all validation_match_sheets bodies by SHA and raw format. Apply adapter predicates in source order to obtain exclusive first-failure counts. Independently inspect all FotMob leagueId/parentLeagueId values as a separate overlapping diagnostic.
6. Compare each of 13 persisted resolved_status candidates with target validation_player_aliases and actual raw own-side starters/subs. Record missing alias separately from actual source absence; do not infer a cause. Count callback bodies, not path names, for evidence independence.
7. Compare unordered row multisets for every one of the 90 original tables before/after. Run integrity_check and foreign_key_check, rehash reference bytes, and compare normal export football content after excluding generated_at, version.sqlite_sha256 and the separate additive completeness_audit object.
8. For lifecycle history only, use an isolated synthetic in-memory database with one R4 claim: unavailable/depA → found/depB → unavailable/depA at the same cutoff. Three evaluations yield two transition rows in the frozen code; the current finding is PENDING. This is a synthetic diagnostic, not football evidence.

## SQL definitions
Baseline club record counts: player_match_minutes WHERE club_id = selected club; group by participation_status. Club fixture count: matches WHERE home_id = selected club OR away_id = selected club. Sheet count: validation_match_sheets WHERE club_id = selected club. These are different denominators.

Event-key population: DISTINCT player_id FROM club_management_events by club. Historical union additionally uses player_match_minutes, validation_roster_membership and player_eligibility. Adapter-admitted managed days come from the retained roster decision plus its evidence admission; uncertified included days remain in the policy-included-day denominator. Unknown days are not counted as included.

Retained national rows/fixtures: international_appearances restricted to player IDs in that club's player_match_minutes; count rows and DISTINCT match_id. This is a historical club-associated subset, not proof of current membership or the complete national audit obligation.

Diagnostic opportunity denominator: for each in-range retained club fixture, count every event-key player included by roster_decision on that date if at least one different same-club retained player has played=1 for that fixture. It is diagnostic legacy coverage, not independent completeness. Actual opportunities are engine.opportunities results after new adapter admission.

R6 totals: group reconstruction_attempts payload by path and outcome; deduplicate body SHA privately for unique evidence count. Do not publish player IDs, claim payloads, body hashes or raw responses here. Distinguish first-run attempt telemetry from repeat reuse and initial supported_absences from post-resolution candidate counts.

## Source pointers
Code inspected: reconstruction/engine.py (run, opportunities, gaps, active_resolution), retained_adapter.py (source, discover, resolve), archive.py (resolve), calendar.py and ops/cloud/reconstruction_probe.py. Earlier logs: full-tests-v4, export-check, probe-v4 result. All are local preserved sources; this communication branch contains sanitized outputs only.

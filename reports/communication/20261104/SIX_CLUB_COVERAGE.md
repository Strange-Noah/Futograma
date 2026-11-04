# Six-club coverage — independently reproduced
Investigation 2026-11-05; frozen observation/audit cutoff 2026-11-04. Interval 2026-07-01 through 2026-11-04 inclusive: 127 days. All figures come from the unchanged fc82acf retained adapter/core and a separate reference copy. See [reproduction procedure](REPRODUCTION.md) and [aggregate result](AGGREGATES.json).

## R1 scope and denominators
Event-key players are DISTINCT club_management_events players, the adapter's discovery universe. Historical union adds player_match_minutes, validation_roster_membership and player_eligibility identities; it is not a current roster. Admitted means this adapter accepted at least one retained source form/hash on a day, not fresh independent proof of every assertion. Policy-included days count only event-key players; unknown days are outside the included-day denominator. No invented player-by-fixture absence matrix is required.

| Club | Event-key players / historical union | Admitted players / event-key players | Admitted managed days / policy-included days | Uncertified included days | Unknown days within event-key scope |
|---|---|---|---|---|---|
| Manchester City | 30 / 30 | 18 / 30 | 1008 / 3302 | 2294 | 0 |
| FC Barcelona | 29 / 30 | 0 / 29 | 0 / 3186 | 3186 | 122 |
| Inter Milan | 24 / 28 | 0 / 24 | 0 / 3048 | 3048 | 0 |
| Bayern München | 24 / 29 | 0 / 24 | 0 / 2921 | 2921 | 0 |
| Arsenal | 25 / 32 | 18 / 25 | 1079 / 3048 | 1969 | 0 |
| Paris Saint-Germain | 26 / 28 | 0 / 26 | 0 / 3048 | 3048 | 0 |

There are 19 identities in the historical union outside event-key discovery (per-club union minus event-key counts). Their eligibility is not established here; omission from discovery must be audited, not silently treated as out-of-scope. Four clubs admitting zero managed days is a certificate/adapter failure, not zero managed football players. This probe does not establish comprehensive historical transfer/loan intervals.

## R2 retained football facts versus executed discovery
Reference fixtures are matches rows with either ordered side equal to the club; cross-club fixtures count for both relevant clubs. Source-sheet denominator is validation_match_sheets, and differs from fixture count. Legacy records are accepted retained rows, not new source revalidation. Played means STARTER+SUBSTITUTE_USED, unused means explicit UNUSED_SUBSTITUTE; existing non-squad rows are retained rather than newly synthesized. Adapter inputs may include statuses for players outside its admitted membership population.

| Club | Retained reference fixture rows | Available source sheets | Adapter-admitted fixtures | Legacy played / unused / non-squad / all rows | Adapter input played / unused |
|---|---|---|---|---|---|
| Manchester City | 15 | 13 | 8 | 207 / 81 / 125 / 413 | 119 / 42 |
| FC Barcelona | 14 | 13 | 1 | 224 / 84 / 89 / 397 | 16 / 7 |
| Inter Milan | 12 | 11 | 0 | 176 / 68 / 64 / 308 | 0 / 0 |
| Bayern München | 14 | 12 | 1 | 216 / 68 / 104 / 388 | 16 / 4 |
| Arsenal | 15 | 17 | 4 | 238 / 68 / 166 / 472 | 65 / 15 |
| Paris Saint-Germain | 14 | 13 | 1 | 221 / 70 / 80 / 371 | 16 / 4 |

All 79 sheet bodies pass stored SHA checks. Of 31 FotMob JSON sheets, only 15 enter the adapter. The other 48 sheets have unimplemented provider/format paths in this adapter, even though other parsers exist elsewhere. Full verified club-fixture denominator remains UNKNOWN because broad discovery is not executed. The admitted-fixture count does not constitute complete source acceptance or independent verification of every status read from derived sheet.players.

## R3 / R4 / R6 execution
Diagnostic pairs use retained club matches, actual retained participating peers and the established roster policy on the fixture day for event-key players. They quantify dropped scope against existing data. They are NOT the complete independently verified R4 denominator: national opportunities, missing fixtures and omitted membership identities remain unknown. Initial R3 means qualifying intervals within admitted membership and opportunity scope; its denominator is those admitted intervals, not every calendar day or all football players.

| Club | Legacy-policy diagnostic pairs | Audited pairs / diagnostic pairs | Initial R3 / R4 triggers | R6 callbacks / distinct bodies | Staged exclusions / new appearance rows | Repeat callbacks |
|---|---|---|---|---|---|---|
| Manchester City | 364 | 90 / 364 | 5 / 10 | 120 / 5 | 10 / 0 | 0 |
| FC Barcelona | 352 | 0 / 352 | 0 / 0 | 0 / 0 | 0 / 0 | 0 |
| Inter Milan | 264 | 0 / 264 | 0 / 0 | 0 / 0 | 0 / 0 | 0 |
| Bayern München | 322 | 0 / 322 | 0 / 0 | 0 / 0 | 0 / 0 | 0 |
| Arsenal | 360 | 18 / 360 | 3 / 3 | 36 / 1 | 3 / 0 | 0 |
| Paris Saint-Germain | 336 | 0 / 336 | 0 / 0 | 0 / 0 | 0 / 0 | 0 |

Totals: 108 executed R4 opportunities out of 1,998 diagnostic retained-policy pairs; this ratio is diagnostic only, never a completeness pass rate. There are 8 initial R3 intervals, 13 R4 missing-status claims, 156 R6 callbacks and 13 staged exclusions. No remaining gaps after these candidates are applied in-memory; this does not close uncovered club/national scope.

R6: each of the 13 claims has 12 callback results. Three path names read the same cached sheet: 39 FOUND callbacks from six distinct bodies; nine paths query an unpopulated Archive index: 117 UNAVAILABLE callbacks. No live evidence search or separate official-squad/substitution source acquisition occurred. No automatic new appearance rows were inserted (0 by inspected implementation and exact fact-table comparison).

Absence validation: of 13 candidates, 12 target aliases can independently be compared with their retained complete raw own-side squad and are not listed; 1 target lacks a retained FotMob alias, so exclusion remains unresolved. No target was observed present in the 12 supported source checks. These are not independently corroborated official absence findings, nor evidence of injury/disciplinary cause. Fully corroborated new absence count is NOT ESTABLISHED. Initial supported_absences metric is 0 because it counts pre-resolution inputs only; it is not the final candidate count. Persisted Pending is 0 but at least one emitted claim remains unproven, and whole uncovered populations are unenumerated.

## National-team context / R5
The following retained records are joined through historical club-player associations, not inferred national squad membership. Fixtures overlap between clubs and must not be summed as unique international matches. The true relevant historical national population/opportunity denominator remains UNKNOWN.

| Club | Retained national rows for club-associated players | Distinct retained national fixtures for same union | National fixtures supplied by new adapter | Full relevant national denominator |
|---|---|---|---|---|
| Manchester City | 85 | 49 | 0 (not implemented) | UNKNOWN |
| FC Barcelona | 102 | 42 | 0 (not implemented) | UNKNOWN |
| Inter Milan | 58 | 44 | 0 (not implemented) | UNKNOWN |
| Bayern München | 62 | 39 | 0 (not implemented) | UNKNOWN |
| Arsenal | 97 | 40 | 0 (not implemented) | UNKNOWN |
| Paris Saint-Germain | 90 | 41 | 0 (not implemented) | UNKNOWN |

All six adapter runs invoke R5 and receive two official windows overlapping the audit interval: World Cup June 11–July 19 inclusive, and September 21–October 6 inclusive. Future November 9–17 window is outside the frozen cutoff. Their source is the retained official FIFA calendar capture. Observed fixture-span windows supplied by this adapter: 0 because no national fixtures are emitted. Official-window parsing is EXECUTED; observed-window construction is NOT EXERCISED on real data here. National R4/R6: NOT EXECUTED, not NOT APPLICABLE. The exported label OFFICIAL_AND_OBSERVED therefore overstates actual execution. Calendar observation timestamp is derived from retained file mtime; exact HTTP response-time provenance was not captured by that probe.

## State vocabulary
NOT IMPLEMENTED: broad/live discovery, national adapter, recovery persistence, several retained provider paths. NOT EXECUTED: national R3/R4/R6 and populated archive/live path searches. NOT OBSERVED: new conflicts within emitted subset; not a claim about all evidence. NOT APPLICABLE: a genuinely outside-cutoff fixture under the frozen scope, not an unimplemented club. UNKNOWN: full relevant populations, missing fixtures, independently corroborated absences and unresolved causes. Missing telemetry never means zero.

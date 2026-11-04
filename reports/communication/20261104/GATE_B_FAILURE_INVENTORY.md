# Gate B failure inventory — checkpoint 2
Investigation date 2026-11-05 (Asia/Shanghai). Frozen development fc82acf41df1c0b843aeb16827abe4c75dc69bc9. This report does not publish or modify development code.

The prior probe's FAIL string is hardcoded in reconstruction_probe.probe; it is not a computed B1–B6 verdict. Its failure must be reassessed by subgate. Reproduction runs the unchanged core and retained adapter against a separate isolated copy of the same reference, with local event/attempt instrumentation. Counts below are observed probe results, not full six-club completeness.

| Subgate | Reassessed result | Evidence |
|---|---|---|
| B1 preservation | PASS, tested snapshot scope | Every row of 90 pre-existing tables exactly preserved in independent reproduction; integrity ok, FK errors 0; reference hash unchanged. |
| B2 required audit coverage | FAIL | Only 15 of 79 retained club source sheets admitted; adapter membership-evidence admissions exist for only City/Arsenal. 108 audit opportunities executed; full required denominator remains unknown. National discovery/membership adapter not implemented. |
| B3 active evidence resolution | FAIL overall; narrow lookup executed | 156 path calls for 13 claims: 39 FOUND callbacks reuse six underlying match sheets; 117 UNAVAILABLE callbacks query an empty archive index. No live multi-source search. |
| B4 integrity / no fabrication | Mixed: preservation PASS; candidate verification FAIL | Existing facts unchanged. Twelve of 13 absence candidates are independently inspectable against retained alias and raw own-side squad; one lacks a target FotMob alias and cannot establish exclusion. No confirmed new appearances or independently corroborated absence facts were committed. |
| B5 operation / export | PASS within tested unchanged-input scope; broader lifecycle NOT ACCEPTED | Repeat has 0 new resolution calls; no duplicate findings; fixed 500-call budget per club, not exhausted. Earlier normal-export comparison preserves football content after documented metadata/additive summary exceptions. Reopen/caching and runtime-bound gaps require remediation. |
| B6 football completeness | UNKNOWN / NOT ACCEPTED | Whole membership and national-team scope is uncovered. Unresolved real-world evidence alone is not the reason B2/B3 fail; missing execution and adapters are. |

## Quantified rejection causes
Denominator: all 79 retained validation_match_sheets, all raw bodies found and hash-verified in authorized workspace.

- 33 HTML source sheets: adapter NOT IMPLEMENTED for this format, even though retained bodies exist.
- 15 JSON sheets from other providers: adapter NOT IMPLEMENTED; rejected solely by provider filter.
- Of 31 FotMob JSON sheets: 15 admitted; 11 rejected first by filename/returned-ID equality; 5 rejected first by competition-ID equality. These categories are exclusive first-failure counts.
- All 11 filename failures use nonnumeric archive basenames and agree with their registered ordered sides and kickoff. Filename equality is not a valid provider identity proof. This does not by itself prove all missing requested-ID/source metadata is recoverable.
- Inspecting all 31 FotMob sheets, 9 have provider season-specific league IDs whose parentLeagueId equals the retained competition ID (7 Champions League, 1 EFL Cup, 1 Super Cup). Four are hidden behind the earlier filename rejection. Direct leaf-ID equality confuses edition/provider identifiers with competition identity; it is not proof of nine real football conflicts.

Do not remove identity checks wholesale. Replace storage-layout assumptions with retained request/returned identity proof, and use an explicit tested competition/edition mapping. Preserve contradictory source observations and existing canonical IDs.

## Corrections to prior interpretation
Zero opportunities for four clubs is execution starvation, not absence of teammate fixtures. Zero Pending is not success: missing populations never enter the finding namespace and one unresolved alias case was labeled RESOLVED. R6 path-name count is not independent-source coverage. No external transport/permission failure prevented continued authorized local implementation.

## Checkpoint 3 refinement
Membership coverage loss includes implementation mismatches, not merely unavailable evidence: 15 valid retained captures use an ignored response_timestamp field; SourceRecord schema/asset resolution is unhandled; the explicit Bardghji authorization needs a separate manual-authority contract. Twelve absence candidates now have independently checked raw-source/alias support; one lacks an alias and remains unproven. No new final football facts were persisted. A synthetic A→B→A lifecycle probe demonstrates only two stored transitions for three evaluations. See ROOT_CAUSE_AND_REMEDIATION and AGGREGATES.

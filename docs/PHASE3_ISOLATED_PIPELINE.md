# Phase 3 isolated identity and all-match pipeline

This document describes the development acceptance pipeline. It does not authorize production collection, database promotion, or Pages publication. The first formal ingestion time remains **2026-10-31 16:00 Asia/Shanghai (08:00 UTC)**; reaching that time does not confirm authority handover. Both repository collection variables remain disabled until a separately reviewed handover.

## Identity, management, and represented team

`identity_pipeline.py` retains original names and Unicode comparison variants. Fuzzy similarity ranks candidates; identity acceptance requires stable provider identifiers, independent birth-date/official biography evidence, or a documented legal/common name bridge. Career affiliation can corroborate identity without authorizing current management.

`identity_registry.py` reuses verified certificates and rejects ambiguous global names. A generated name key is not a stable provider ID. `provider_identity_discovery.py` preserves canonical identifiers, including negative internal identifiers, when linking a verified provider identity.

`roster_verification.py` keeps identity, academy affiliation, registration, and current management separate. An unavailable page does not erase an already verified interval. An appearance, academy listing, or List B registration does not independently prove continuous first-team management. Explicit departures remain effective until dated return/signing evidence is verified.

International appearances never create a managing-club relationship. The navigation remains **club → managed player → club and international timeline**; `represented_team` describes the match side, while `managing_club` comes from roster management evidence.

## International acquisition

The initial implementation uses additive international fact/evidence/provider-bridge tables and a read-only combined timeline. Existing club facts and the original national-reference payloads remain protected. Reference integration preserves each record's original verification scope and payload hash.

Discovery starts with a verified player's public personal history, then follows explicit national career affiliations to public team fixtures. Nationality is never an affiliation source. Public recent history and a finite fixture response are not proof of complete history; every managed player has a coverage record.

`international_fotmob.py` validates the expected match, ordered team IDs, kickoff, competition, lineup sides, player IDs and target identity. Placeholder player ID zero never becomes a canonical player. Missing or disputed minutes remain null; confirmed non-participation is a separate zero-minute state. Extra time needs verified match-duration evidence. Hidden FT/AET template text is not duration evidence.

`international_sky_fallback.py` searches observed public directories and month links. It validates full ordered team names, date, competition and exact detail kickoff. A short lineup label is accepted only through the directory's explicit full/short name pair for the same provider match ID and team ID, with the source hash retained. This is not a global abbreviation or fuzzy-name rule. Cross-provider team IDs must agree with canonical team identity.

`source_redirects.py` follows only observed, bounded, same-host HTTPS Sky canonical redirects. Source access policy is checked on every hop. Cross-host redirects, credentials, API paths, redirect loops, and access denials are not bypassed.

Player aliases must already be verified. Personal history and explicit national affiliation establish target context. Independent source clocks do not silently override personal reports or retained accepted minutes. Original source quarantine can remain in place even when another source independently establishes a valid fact.

## Persistence and consumer gates

`international_retry.py` persists attempts, source failures, quarantine, and escalation. Queue rows describe historical source tasks; a historical identity-source quarantine is not itself a statement that the current canonical mapping remains unknown. Report current identity coverage separately.

`cloud/export_publish.py` validates the full export, schema, counts and exact pointer/version before the isolated snapshot compare-and-swap. It then verifies the committed-version export. Failed export or version validation must not call the snapshot publisher. Current and previous immutable snapshots remain recoverable from GitHub.

`cloud/all_match_export.py` exports schema version 3. The website displays club and national records in one timeline, with represented team, level, participation and source status. Coverage warnings remain visible; recorded minutes must not be labelled complete personal workload when history is incomplete.

## Hosted acceptance entry points

The private data repository acceptance workflow checks out an exact public code commit. `ops.cloud.phase3_ci_gate` requires successful official CI for that exact source before acquisition. GitHub-hosted isolation guards require collection disabled and handover unconfirmed.

The full acceptance workflow uses separate jobs:

1. Restore and read-only consumer compatibility preflight.
2. Replay the original rejected full/short team-label cases before acquisition.
3. `python -m ops.cloud.phase3_international_e2e`: live acquisition into an isolated copy, protected-fact checks, export-before-publication, isolated snapshot CAS and uploaded-result readback.
4. A fresh checkout restores current and previous snapshots, reproduces the export, builds the site from repository source and exercises the mixed timeline in hosted Chrome.
5. An always-running report exposes success, failure and skipped dependencies separately.

A separate `python -m ops.cloud.phase3_raw_replay` job restores the snapshot and retained responses entirely from GitHub. It verifies every raw body hash, replays discovery and parsing without external match acquisition, and checks that canonical facts and the original reference payloads do not change. A successful replay does not prove complete source coverage.

The acceptance runner deliberately pins a historical evidence baseline and window. It is not the unattended production daily controller. The production `platform`/`driver` integration, migration/promotion of the reviewed snapshot, exact-version Pages publication and authority handover require their own rollout acceptance. No local database, absolute desktop path, local process, or manually uploaded website build is an acceptable production dependency.

## Reading results

Report these separately: software tests, live source quality, current management completeness, international coverage, snapshot publication/recovery, hosted UI, public Pages publication, and production authority. A green workflow is not proof that unresolved sources, management dossiers, or international history are complete. A skipped recovery is not passed.

Preserve unresolved evidence. The acceptance jobs do not invoke local cleanup or prune cloud snapshots. Runner temporary files can expire with the job after required evidence has been persisted. Never delete the current authority, required recovery snapshot, or unresolved evidence to make a check appear clean.

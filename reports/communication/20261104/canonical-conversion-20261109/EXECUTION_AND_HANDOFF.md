# Immediate canonical conversion — 2026-11-09

PASS / VERIFIED: immediately converted the four complete Barcelona/Bayern club-season league subsets through the existing fixture adapter, identity verifier, canonical writer and appearance writer. Of144 source fixtures,143 new canonical matches and1,720 identity-verified player-match records were persisted in an isolated candidate. One Barcelona fixture remains held because the available kickoff is a midnight placeholder; it did not postpone the other143.

PASS / VERIFIED: all original database rows and accepted canonical fields preserved; original source database unchanged; SQLite integrity and foreign keys pass. Repeated the construction on the same candidate: zero new matches or player-match rows, all logical rows identical and database digest unchanged.

FAIL / CONFIRMED ERROR: ordinary pre-export inspection rejects this partial candidate with INVALID_STARTING_XI_COUNT. Existing verified identities do not yet cover all11 starters in some fixtures. Valid partial records remain persisted; no identities or zero minutes were fabricated to pass export. An intermediate repeat packaging collision was separate from football facts; after verifying the retained gzip against the database, the ordinary pre-export inspection reproduced the starting-XI error.

NOT TESTED / UNKNOWN: new event-derived minutes and R1–R6 acceptance for these records.1,309 source identity rows remain unfinished investigation work, not human Pending. Minutes remain unknown, and listed bench players retain unknown participation. Existing accepted minute facts remain unchanged. Full24-unit DATA-ACCEPTED remains0/24. The matrix distinguishes complete league subsets from unfinished full-competition unit scope.

Continue immediately with identity bridges and minutes on the persisted143 fixtures, alongside missing competition acquisition. Process Inter's retained117/117 squad observations after confirming its full required competition scope; use the same original writers. Do not wait for all clubs, build another framework or drop valid partial rows because full export is not ready. The precise local continuation, candidate digest, inputs and repeat receipts are retained privately.

No production database, schedule, deployment or pointer was changed. No new permanent module, deferred cross-season automation, Real Madrid work or paid operation. Source captures, private identities and databases are excluded from this publication.

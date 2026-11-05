# B5 — Discovery, adjudication and persistence compatibility

B5 passes through controlled isolated tests and existing realistic fixture contracts; it does not require repopulating the six-club database.

The original workflow test preserves an accepted synthetic row, omits it only in an isolated fixture database, acquires and parses native full-team sources, verifies canonical identities, and restores the same participation/minutes through live_ingest's existing writer. Native sources, canonical observation registry, raw-artifact receipts and the fixture's retained original row provide reproducible provenance. A repeat leaves fact counts and contents unchanged. The untouched fixture constructor remains the rollback reference.

The typed-source test independently exercises SourceService raw replay → exact requested/returned identity → shared identity_registry → minute_policy and participation checks → existing apply_appearance/fact revision writer → repeat UNCHANGED_ACCEPTED. Other tests preserve the full prior unknown-minute row in revision evidence, reject unverified canonical overrides, retain negative canonical IDs, preserve known values on conflict and verify caller rollback.

Audit proposals remain distinct from accepted football facts. The audit package itself has no canonical writer; the controlled adjudication coordinator calls established club/international persistence functions and observes their restrictions. No new football table writer or collector was introduced.

Claim: existing discovery and adjudication mechanisms cooperate with supplementary audits without competing canonical authority. Direction: SUPPORTS. Confidence: CONFIRMED by controlled software evidence. Limitation: no genuine additional numerical fact qualified in the completed real-data 198-obligation set, so real-data canonical writes were zero. No recovery claim is invented to demonstrate compatibility.

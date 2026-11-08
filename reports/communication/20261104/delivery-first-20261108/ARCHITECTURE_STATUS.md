# Concise architecture status

- KEEP: accepted SourceService acquisition/archive boundary; source identity, roster_policy and minute_policy; original fixture adjudication, canonical persistence, R1–R6 and export. Production remains authoritative for production.
- KEEP: historical_unit only for demonstrated archived replay, checkpoints and recovery. No additions in this checkpoint.
- TEMPORARY: isolated private programs extracting the archived official DFL/LaLiga/DFB/Inter sheets and UEFA report layouts, and aggregating source populations. They acquire through SourceService or read its hash-verified archives, then use existing identity lookup. They are not imported by scheduled production or published with these reports.
- REDUNDANT: historical manual replay controls overlap the existing replay/checkpoint facility. Avoid expansion; preserve evidence and existing useful callers. No speculative deletion or rewrite.
- DEFERRED: reusable autonomous historical orchestration and broad cleanup. The single future cross-season R1 TODO remains in the existing handoff; no implementation or additional design work.

Actual call graph at this checkpoint: private bounded source task -> SourceService -> original public Client -> EvidenceStore; temporary archived-format extraction -> retained field provenance -> existing identity_registry lookup in read-only mode. Downstream accepted historical writes continue to use the original fixture and appearance functions and normal export. No second collector, identity resolver, canonical writer, audit engine, production adapter or copied pipeline version was added.

Remaining temporary format extraction has no production runtime callers. Retirement condition: preserve source captures and reproducible accepted dataset receipts, finish historical delivery, then separately review whether any one-time tool is still needed for replay. No retirement is authorized by this classification.

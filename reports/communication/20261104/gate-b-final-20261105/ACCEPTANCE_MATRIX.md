# Gate B final acceptance contract — 2026-11-05

Overall: **ACCEPTED — 2026-11-06**. B1–B5 retain their accepted local evidence; B6 now has independently verified exact-candidate hosted evidence. All six independently evaluated requirements pass. No baseline recertification was reopened.

| Requirement | Status | Exact evidence and limits |
|---|---|---|
| B1 Baseline fidelity | PASS | All 90 original tables equal the accepted reference; full normal export matches after excluding generated_at and the expected audit-copy SQLite hash. Current website comparison is separately versioned. |
| B2 Original-pipeline independence | PASS | Original Client → native parsers → identity verification/persistence → roster refresh → live_ingest.run → canonical writer → normal export executed on controlled fixtures with all reconstruction imports rejected. A missing fact is restored; repeat facts remain unchanged. |
| B3 Supplementary effectiveness | PASS | Six-club 2,267 eligible opportunities; dependency order R1→R2→R5→R3/R4→R6; preserved ledger for the 198 investigations; 168 accepted-evidence dispositions and 30 supported inferences; no blanket recertification. |
| B4 Authority and isolation | PASS | Audit SQL writes target only its own ledgers; injected R3/R4/R6 failures preserve accepted facts and allow ordinary export; no findings become canonical corrections or automatic human Pending. |
| B5 Discovery/persistence compatibility | PASS | Controlled source-driven omitted-fact recovery through the original update path, plus typed SourceService replay → shared identity/participation checks → existing writer → repeat no-op and retained revision tests. No real six-club fact is changed. |
| B6 Independent regression | PASS | Exact commit 51c7bf9: hosted run 42229323426 executes 500 tests, zero failures/skips. All 58 formerly local-skipped cases individually verified successful. |

The 30 supported conclusions do not materially prevent B3/B4 acceptance: they are 24 source-supported nonselection observations and six dependent R3 conclusions, explicitly below canonical confirmation. Their uncertainty does not change accepted facts, require an absence cause, or justify human Pending. No new numerical recovery qualified in the real-data obligation set; B5 is a controlled compatibility test as required.

Reviewed code candidate: `51c7bf988ffdd2ed2232cc238a48f92c4fd6e481`, two commits above accepted Gate S `53ace3a29596263ca57f39370e39f1243160fcdc`, 20 final files. See CODE_PUBLICATION_MANIFEST.json for every intermediate version and final hash.

Publication succeeded under the latest one-attempt authorization. Independent remote readback confirms exact branch SHA, both parent links to accepted Gate S base, and all 20 final file contents against the SHA-256 manifest via matching Git blobs. [Hosted run](https://github.com/Strange-Noah/Futograma/actions/runs/42229323426), job 127930822855, completed successfully. The log contains 500 test identities, Ran 500 tests, OK, and no skips; all 58 expected hosted-only cases match successful test lines. Two runtime warnings (existing yearless-date parsing and a resource warning) are recorded as deferred software debt, not failures or skips. See HOSTED_ACCEPTANCE.json.

Gate B ACCEPTED validates original-pipeline independence and supplementary-audit compatibility within accepted scope. It is not deployment authorization or universal source completeness. Website readiness and Real Madrid preparation now proceed as separate activities. Production remains NO-GO.

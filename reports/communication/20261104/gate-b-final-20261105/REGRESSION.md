# B6 — Regression and reproducibility

Local current candidate: **500 discovered, 442 passed, 58 known hosted-only skips; zero failures**. This adds three independence/failure-isolation integration tests to the previous 497-test candidate. Exact identities of all 58 locally skipped cases are retained for later hosted log verification.

The preserved Gate S [433/433 hosted run](https://github.com/Strange-Noah/Futograma/actions/runs/41885610724) remains valid for Gate S. It does not validate current candidate `51c7bf988ffdd2ed2232cc238a48f92c4fd6e481`. Current code hosted execution: **NOT EXECUTED**.

Normal approval permitted a Git HTTPS HTTP/1.1 push of that exact candidate to `Strange-Noah/Futograma`, branch `codex/gate-b-obligations-20261105`. The push failed: `Failed to connect to github.com port 443 after 75003 ms: Couldn't connect to server`, exit 128. No GitHub request ID was returned. Independent read-only GitHub inspection confirms the branch remains absent. No retry, credential change, alternate transport or API publication was attempted.

B6 status: BLOCKED by publication transport. Required next evidence: successful separately authorized exact-payload publication, independently verified branch SHA and all outgoing file blobs, the existing free CI run, 500 executed tests with zero failures/unexplained skips, and explicit successful execution of each of the 58 formerly skipped cases. No hosted run URL exists for the new candidate yet.

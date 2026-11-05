# B1 — Baseline fidelity and current website comparison

Claim: the isolated audit environment preserves the accepted six-club baseline. Direction: SUPPORTS. Confidence: CONFIRMED by complete table-content and normal-export comparisons, not by reacquiring historical facts.

All 90 original tables match exactly. Accepted roster/management policies, canonical identities, fixtures, scores, minutes, participation, competition normalization, source debt and historical evidence remain intact. The frozen reference SHA-256 remains `1e1e72176bd32eb45a1b84e9db34fea653c1602ecf483384642950d556d7c1c0`.

The entire normal browser export matches between reference and isolated candidate after removing only `generated_at` and `version.sqlite_sha256`. The SQLite hash differs because the candidate contains supplementary audit ledgers. Normal export was executed with reconstruction imports blocked. No audit record is required to export accepted football data.

## Genuinely current public website comparison

The public [manifest](https://strange-noah.github.io/Futograma/data/manifest.json) and its browser JSON were read in this session. The browser body matches the manifest hash and version. Public data commit: `ceaaa8ec6d4e1c980d6f41afc94daaff192ecbf3`; data version `20261104T160520-1e148ae24020`; website code `666255afe5bb565f82156ba9908713a5bcbab8ed`; SQLite SHA `11b31a695137d4095a654157d8559c245c7f3deb315af094003faa9a089b890e`.

All 2,843 frozen-reference player-match records are present on the current site. Core dates, kickoff, competition, opponent, minutes, participation, represented team, match type and level agree for every common record. The current website has 20 additional club player-match records and 24 common records with different canonical metadata. These are versioned-output differences, not losses from the frozen baseline; no new audit recertification or fact correction is inferred from them.

Limitations: this proves current public artifact self-consistency and aggregate comparison, not a fresh independent review of the website's newer records or a readback of the private current production pointer. The accepted frozen comparison and current website comparison remain separate. A deployment-pointer review belongs to the separately gated readiness stage.

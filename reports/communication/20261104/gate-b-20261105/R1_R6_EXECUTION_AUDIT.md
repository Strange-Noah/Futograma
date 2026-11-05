# R1–R6 execution audit

Local development commit `dbe49bc5d41182b9aa4715c0d108a502119877e3` on `codex/source-reintegration-20261105`; not published. The exact accepted Gate S commit 53ace3a and its 433-test hosted acceptance remain unchanged and do not cover this later development.

| Stage | Actual execution | Acceptance limit |
|---|---|---|
| R1 | Existing roster policy plus source-normalized event assertions; manual/day authority kept separate | Historical management intervals remain uncertified for substantial parts of all six clubs; no backward extrapolation from current snapshots |
| R2 | Retained club details, actual player histories and team schedules requested through SourceService; strict existing FotMob and Sky parsers reused | Native hints remain candidates; broad/live coverage and new-fixture adjudication are incomplete |
| R5 | Official calendar parsed through SourceService; downstream windows consume typed observations; independently parsed team fixtures add observed national context before R3/R4 | Career entries are discovery leads, not current call-up membership; no national membership fabricated |
| R3 | Five-day warnings over supported intervals with actual teammate opportunities, including season/cutoff boundaries | Uncertified roster intervals remain coverage debt |
| R4 | Every applicable certified teammate opportunity evaluated; unknown/conflicting target status kept visible | Zero opportunities caused by missing membership certificates are not passes |
| R6 | Exact player profiles checked against archived lineup details; explicit history/lineup IDs and minutes jointly validated by existing parser | Other unmapped paths remain NOT_REQUESTED/SOURCE_WORK_REQUIRED, not performed searches or website failures; unresolved conflicts remain |

Runtime trace: 1042 SourceService.observe calls, 292 distinct body hashes, 648 parser executions and 388 cache hits. Network requests: zero. This is real archived evidence replay, not a new live collection run. [Runtime/category detail](CALL_GRAPH.json).

R6 now uses up to three actual exact-profile captures per player/interval. A relevant history claim is checked jointly with the exact fixture detail. Explicit unused substitutes can recover a zero status candidate; missing minutes and unknown bench participation cannot. Same-publisher profile/detail checks are not described as independent corroboration. One response is not relabelled as an official squad, press conference or disciplinary search. Unperformed paths retain concrete retry/remediation obligations and do not consume the search budget.

Repeated runs perform zero additional resolution checks, while discovery-boundary replay remains explicit. A bounded observation cache avoids repeated parsing without skipping hash checks, parser-version invalidation or caller isolation. No status candidate was written into canonical appearances; independent persistence adjudication remains required.

Local regression: 484 tests discovered, 426 passed, 58 existing hosted-only local skips. This development has not had hosted regression. The accepted Gate S hosted run remains [433/433](https://github.com/Strange-Noah/Futograma/actions/runs/41885610724). Normal football export comparison on the full-period candidate passes after excluding generation timestamp, candidate database hash and the separate audit summary. Every original table is preserved exactly and the frozen reference hash is unchanged.

Remaining mandatory work: historical R1 certificates; complete broad discovery/detail verification; independently supported national selection intervals; genuine remaining R6 source paths; safe adjudicated persistence; complete six-club acceptance and hosted integration regression. No gate is waived.

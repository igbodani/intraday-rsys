---
name: replay-causality
description: Audit or test intraday-rsys information availability, event ordering, bar revisions, execution references and timer causality. Use for data/replay/strategy/execution changes or suspected lookahead and impossible-fill bugs.
---

Read [data rules](../../../docs/data_rules.md), [contracts](../../../docs/contracts.md) and the affected runner's actual call path. Do not assume a fixture interpreter and a public runner share timing behavior.

1. Write a tiny timeline with market timestamp, availability/known time, decision/activation time, timer request and eligible fill reference. Include declared delay, units, quality and reference identity. Use explicit synthetic values; do not infer real provider semantics from fixture rules.
2. Identify the consumer's bounded view. Follow history/warmup/scanner access as well as the current event. Reject future or wrong-session history, raw future arrays and unavailable revisions. Distinguish missing observations from a verified zero.
3. Verify deterministic phases and tie-breaking against the actual engine. Check decisions/exits/entries, rank under constrained cash and exactly-once reference consumption. Current rejection of unsupported equal-time trade fills must not become silent acceptance.
4. Check that a fill is possible after activation/readiness using the configured execution basis. Completed OHLC cannot reveal a precise intrabar touch sequence. A timer exit cannot use an earlier open or a print from before its request simply because delivery was late. Corrections must not rewrite committed fills.
5. Run a future-prefix test: start with a fixture that produces meaningful decisions/fills, then mutate or append only information unavailable at a chosen cutoff. Compare all earlier proposals, ranks, acceptance/rejections, reservations, fills, equity and decisions available in the public trace. Exclude attempt IDs and source hashes only when comparing semantic behavior; retain original artifacts.
6. Add a negative control: change an observation eligible before that cutoff and demonstrate the relevant earlier result changes. If it does not, the future-prefix test may be vacuous. Include missing-data, positive-delay and timer-without-new-data boundaries relevant to the change.
7. Check account effects independently: intent-owned reservations, cash/quantity conservation, atomic rejection, one cost application and protective exits under scanner removal/halts. Compare public-run behavior and retained evidence, not only helper output.

Start with relevant cases in [causality](../../../tests/test_causality.py), [clock regressions](../../../tests/test_r3_clock_fixes.py), [develop regressions](../../../tests/test_develop_regressions.py), [trade input](../../../tests/test_trade_input_run.py) and [public sessions](../../../tests/test_public_sessions.py). Select cases by the changed contract; do not claim every risk is covered by a passing file.

Return the timeline, invariants, reproduction/test paths, commands actually run, outcomes and unresolved provider/ordering limits. Review mode reports proposed regressions without editing tracked tests; implementation mode adds them under the user's task scope.

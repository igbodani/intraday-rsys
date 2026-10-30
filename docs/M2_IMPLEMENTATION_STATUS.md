# M2 implementation status

Updated 2026-10-29 after priorities 1–2 on `fix/r3-replay-causality`, based on repaired revision `4e68583`. Use the containing commit as the implemented source revision.

Synthetic ORB/VWAP bar and trade sessions use ReplayRunner, bounded strategy views, shared account transitions, sizing and timers. Public `rsys run` now accepts explicit version-2 bar/trade multi-session configuration, preserving account equity and risk while resetting daily strategies. Inputs, resolved defaults, fills, failures, metrics and reports are retained. See [PUBLIC_SESSION_RUNS.md](PUBLIC_SESSION_RUNS.md) for the exact schema and policy semantics.

The 678-test develop snapshot contained eight reproduced defects. [DEVELOP_REPAIR_RESULTS.md](DEVELOP_REPAIR_RESULTS.md) documents those repairs and 24 regressions. This extension adds 46 public-session/policy cases: **748 tests pass**, Ruff is clean, and the existing public paths plus both new examples are verified for expected exits and repeatability.

| Area | Current state | Remaining gate |
|---|---|---|
| M2-01 account/bar execution | Shared contracts, session-scoped IDs, Decimal ledger, F01–F17, equity sizing, daily/account entry halts and explicit costs/delays | Complete real account, settlement and corporate-action policy |
| M2-01T | Public trade configuration connects SeriesRouter/ReplayRunner, phase-40 decisions, phase-60 exits, phase-70 ranked entries, quality validation and post-request timer fills; complete retained evidence | Full event-key support for equal market timestamps and audited provider semantics |
| M2-02 | ORB, ranking, RVOL/gain math and fixture history with enforced prior-session dates | Minute-specific dated/as-of provider history, coverage/universe audit, denominator and exclusion evidence |
| M2-03 | Public bar/trade multi-session coordinator; fresh ORB/VWAP strategies, persistent account/peak halt, increasing dates and flat-before-next-session enforcement | Broader strategy matrix and complete supported corporate-action lifecycle |
| M2-04 | Multi-session run/report/compare; sealed complete inputs/artifacts, partial failures, session-end drawdown and scheduled-time exposure | Loader holdout enforcement, uncertainty estimates and frozen final protocol |

Exposure uses account availability clipped to scheduled regular hours. An unclosed diagnostic position contributes through scheduled close; this proves neither closure nor complete coverage. Drawdown includes starting capital but remains session-end-only.

D10/D11 now have executable, explicit version-2 synthetic policies with recorded values in the examples. Daily halts reset each session; account drawdown halts stay latched, pending entries release cash, and protective exits continue. The research YAML remains a separate blocked interface. D09/D12/D22 remain Open; D13/D16/D17 are Proposed. No research thresholds or approvals changed. The register's future-dated owner-round entry remains flagged for provenance verification.

Alpaca acquisition, broker execution, historical-performance evidence, optimization, paper/live trading and final evaluation remain unimplemented. `final-evaluate` deliberately exits 5. Next: audited dated/as-of historical loading and loader-level holdout enforcement before market-result claims.

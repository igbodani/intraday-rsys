# M2 implementation status

Updated 2026-10-29 after review of develop `bab9082` and repairs on `fix/r3-replay-causality`. Use the containing commit as the repaired source revision.

Synthetic ORB/VWAP bar sessions and the Python trade-input API use ReplayRunner, bounded strategy views, shared account transitions, sizing and timers. Fixture-backed scanner history, chronological multi-session coordination and descriptive statistics exist as Python APIs. Public `rsys run` still accepts only the strict single-session synthetic bar schema.

The 678-test develop snapshot contained eight reproduced defects. [DEVELOP_REPAIR_RESULTS.md](DEVELOP_REPAIR_RESULTS.md) documents the repairs and 24 new regression cases: **702 tests pass**, Ruff is clean, and 19 public-command invocations pass their expected exits and repeatability checks.

| Area | Current state | Remaining gate |
|---|---|---|
| M2-01 account/bar execution | Shared contracts, session-scoped transaction IDs, Decimal ledger and F01–F17 | Complete account/corporate-action policy and implement recorded research risk/cost settings |
| M2-01T | Trade runner connects SeriesRouter/ReplayRunner, phase-40 decisions, phase-60 exits, phase-70 ranked entries, quality validation and post-request timer fills | Public configuration, retained evidence, full event-key support for equal market timestamps and audited provider semantics |
| M2-02 | ORB, ranking, RVOL/gain math and fixture history with enforced prior-session dates | Minute-specific dated/as-of provider history, coverage/universe audit, denominator and exclusion evidence |
| M2-03 | VWAP and bar-based multi-session coordinator; fresh strategies, persistent account and increasing session dates | Public multi-session composition, broader strategy matrix and complete supported corporate-action lifecycle |
| M2-04 | Synthetic run/report/compare CLI; corrected session-end drawdown and scheduled-time exposure | Multi-session CLI evidence/reports, loader holdout enforcement and frozen final protocol |

Exposure uses account availability clipped to scheduled regular hours. An unclosed diagnostic position contributes through scheduled close; this proves neither closure nor complete coverage. Drawdown includes starting capital but remains session-end-only.

D10/D11 are recorded Decided while their research values remain unapplied in executable baseline configuration. D09/D12/D22 remain Open; D13/D16/D17 are Proposed. No research values or approvals changed in this repair. The register's future-dated owner-round entry is flagged for provenance verification.

Alpaca acquisition, broker execution, historical-performance evidence, optimization, paper/live trading and final evaluation remain unimplemented. `final-evaluate` deliberately exits 5. Next: a reproducible multi-session public run with retained evidence; risk/cost policy and audited historical loading must precede market-result claims.

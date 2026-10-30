# Develop review repairs

> Historical planning or implementation record. Starting-state descriptions and paths reflect its original revision. Use [ARCHITECTURE.md](ARCHITECTURE.md) for current packages and [CURRENT_SYSTEM_AND_NEXT_STEPS.md](CURRENT_SYSTEM_AND_NEXT_STEPS.md) for current status. Behavioral requirements still apply unless superseded by the decision register.

Date: 2026-10-29 (America/Denver). Baseline: `bab9082a985f019d4ebe4ad450c5c3dbe86f40e9`, tree `e5da5f0342476be3cbec760677f58ecb92ce50d9`. Target: `fix/r3-replay-causality`; the containing commit identifies the repaired source. No market data or broker operations were used.

## Failures and fixes

The baseline passed 678 tests and Ruff. Independent whole-pipeline examples exposed eight defects. The initial regression batch produced 13 failing parameterized cases before implementation; additional boundaries bring this repair to 24 new cases in [test_develop_regressions.py](../tests/test_develop_regressions.py).

| Defect | Repair | Regression evidence |
|---|---|---|
| Repeated exit times collided across days, recording a sale while still holding shares | Bar exit IDs use absolute session/entry time; trade transaction IDs include session/instrument; reservations are session-scoped | Three actual sessions with identical entry times, repeated/changed exit prices and nonzero fees; exact cash/P&L, zero reservations, flat positions after every day |
| Future/current-day history changed past RVOL | Enforce strictly prior dates and newest-first order at provider and composition boundaries; reject duplicate history and reversed current-bar availability | Future/current volume changes preserve hand-calculated RVOL 2; unbounded providers cannot bypass the cutoff or backfill selected missing data |
| Decisions ran at publication phase 20 before same-time data | Enqueue one actual phase-40 decision after source application | Delayed breakout print is included; exactly one accepted decision with a phase-40 trace |
| Conversion washed bad quality and trade sizes | Preserve quality, size, conditions and tape; follow ReplayRunner's fail-closed quality policy | Invalid print fails before filling or mutating its bar; supplied metadata survives; corrections cannot masquerade as fresh trades |
| Timers used late-delivered pre-request prints | Retain first exit-request time; require later market and availability times | Both flatten and max-hold ignore a 09:47:00 print delivered at 09:47:10 after a 09:47:05 request, then fill at 09:47:15 |
| Exposure mixed midnight-relative timestamps with elapsed session duration | Normalize actual FillRecord clock times, clip to regular hours and union overlaps; record actual UTC session open | Real early/regular sessions give `(duration - 16) / duration`; out-of-session/overlapping intervals stay bounded |
| Drawdown omitted the first-session loss | Include starting capital; positive no-decline curves report 0% | 1000 → 900 gives 100 / 10%; flat/profitable first sessions give zero |
| Duplicate/reversed sessions were accepted | Enforce increasing dates; forbid silent retry/advance after runtime failure | Invalid order fails before mutation; incomplete attempts require a fresh coordinator and reconciled account |

## Related integration coverage

Trade publications no longer execute account orders in phase 35. Exits run at phase 60 and rank-ordered entries at phase 70. Reversing symbol input order preserves the complete trace, with exact shared-cash quantities. Reused provider trade IDs on different symbols no longer collide.

Same-batch cancellations/replacements invalidate the original print before execution, including when no adapter previously existed. Replacement price/size payloads are preserved. Entry-adapter state is committed only after successful account entry, avoiding a consumed entry after fill-time sizing rejection. Future-price perturbation preserves the earlier whole-run trace; changing the earlier signal is a negative control.

Existing statistical fixtures now use the real runner's timestamp convention. The H01 scanner composition fixture now chooses a decision date after its history, instead of asserting a result from future sessions.

## Verification actually executed

```bash
uv run --frozen pytest tests/ -q       # 702 passed
uv run --frozen ruff check .          # All checks passed
git diff --check                     # clean
```

Public-command verification: **19 invocations** with expected exits. Fixture validation succeeds; the pending research baseline exits 2 with 44 blockers. Four replay configurations each run twice with matching semantic hashes. F01–F17 run twice with expected checkpoints. ORB/VWAP run/report configurations each run twice with distinct attempts, matching semantic hashes and one completed trade; each pair compares successfully. `final-evaluate` remains blocked with exit 5. Generated attempts stay outside source control.

## Caller-visible contracts and limits

- `TradeExecutionAdapter.force_exit` requires `requested_at_ms`. Typed synthetic `Trade` supports size/conditions/tape; legacy fixture objects without those fields retain the explicitly synthetic unit-size assumption. Invalid-quality input fails the attempt, rather than silently disappearing from coverage. Correction-flag inputs must use `corrections_by_symbol` with an original identity.
- Exposure is a scheduled-time diagnostic using account availability. Open positions contribute through scheduled close without asserting an exit there. Drawdown includes a `session_date=None` starting-capital point and remains session-end-only.
- History helpers still require caller-prepared same-minute cumulative volumes and prior-close/liquidity observations. This repair enforces dates/order; revision availability, minute-addressable historical loading, feed coverage and universe membership remain separate work.
- Public trade/multi-session configuration and retained evidence, risk/cost implementation, Alpaca ingestion, complete loader holdout enforcement and the frozen final protocol remain subsequent tickets. D10/D11 values are preserved; this repair neither invents nor activates research assumptions. M2 and Steps 1–6 are not claimed complete.

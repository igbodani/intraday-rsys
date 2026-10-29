# M2 repair results

Date: 2026-10-29. Target branch: `fix/r3-replay-causality`. Parent: `de33f3303aa54e7a9fdfa0bd9ba897be000a5bf4`. This document describes the repaired source in its containing commit. No merge, force push, market-data purchase or trading action is part of the repair.

## Findings and changes

| Reproduced problem | Corrected behavior | Regression evidence |
|---|---|---|
| Accepted size could overdraw cash after a price gap | Recheck current cash/risk at execution; floor increments, cap at accepted quantity; fail atomically | `test_gap_cannot_create_negative_cash`, `test_fill_rechecks_risk_and_never_upsizes`, `test_overspending_buy_is_atomic` |
| A completed bar's earlier open was used at publication time | Separate modeled open observation at interval start from completed-bar publication; later decisions cannot use an earlier open | `test_delayed_signal_cannot_use_an_earlier_open`; persisted entry-time assertion |
| Entry bar was skipped by protective execution | Entry-interval extrema settle at publication; stop wins ambiguous dual touch | `test_entry_interval_protection_is_active`; F02 |
| Expiry depended on the next observation; flatten reused a past open or quietly stayed open | Independent phase-30 timers, cancellation ownership and incomplete status for missing required exit data | `test_expired_intent_cannot_fill_after_gap_in_input`, `test_required_flatten_without_execution_data_is_incomplete`, corrected flatten integration test |
| Current prices did not update position marks | Mark at source publication; ending equity includes marked holdings | `test_equity_uses_last_published_mark` |
| Win/loss and expectancy ignored fees | Use net ledger trade P&L, including both fees; breakeven trades retain correct missingness labels | `test_metrics_use_net_trade_pnl_including_both_fees`, `test_breakeven_completed_trade_is_not_labeled_no_trades` |
| Range completeness, increment rounding and pending VWAP updates were unsafe | Require the full ORB opening range; exact multiple rounding; keep VWAP cumulative state current while pending/held; conservative history-gap behavior | `test_missing_opening_range_never_signals`, `test_non_power_of_ten_tick`, public-config VWAP pending-state test |
| Late pre-ready/pre-entry prints, expiry and duplicate exits were incomplete in trade adapter | Check both market and availability activation timing, expire intent, preserve one terminal exit and report costs | Repair trade tests plus existing T01–T09 |
| Cash APIs could overwrite a position, release another intent's cash or oversettle proceeds | Validate before mutation, own reservations by ID, guard duplicate position and optional transaction-ID redelivery | Account repair tests and ledger invariant suite |
| Phase views and event deduplication were too shallow | Copy enqueued payloads, recursively freeze decision mappings, compare full event identity, reject insertion into dispatched phases | Phase repair tests and existing engine-order tests |
| Session runner bypassed ReplayRunner | Bind source/account events to shared nanosecond ReplayKey ordering; query bounded replay contexts at phase 40; accept at 50 and execute exits/entries at 60/70 | Full trace-prefix invariance, saved context, input-version and reversed-universe tests |
| Unknown config fields/schema and early/invalid completed bars were accepted or ignored | Strict synthetic schema, full dated session/calendar checks, OHLC/volume/increment validation and forwarded strategy options | Parameterized malformed-input tests |
| Data hash was actually modification time; invalid input left no attempt; fixture artifact list was persisted too late | SHA-256 of bytes read once, failed-attempt retention, partial trace and artifacts finalized inside lifecycle | Evidence retention/hash tests, fixture artifacts test |
| Trace grouped all decisions before all fills | Retain transitions chronologically with availability, phase, source references and account snapshots | Persisted trace-order test and repeated semantic hash checks |
| Comparison omitted capital, data, strategy and scanner assumptions | Require sealed successful version-2 evidence, verify manifest/trace hashes, compare full implemented assumption set | Comparison regressions and subprocess checks, including intentional risk difference |
| Split reset mark to entry and fixture oracle ignored explicit expectations | Preserve current marked equity for supported exact split quantities; reject unsupported conversion; check named checkpoints, exit price, entry/flatten times and first rejection | Split regression and mutated-expectation negative controls |
| Final-evaluation CLI looked like a generic unimplemented command | Explicitly blocked exit 5; synthetic run cannot select a research/holdout evidence mode | Final CLI subprocess regression and holdout suite |

Existing tests were corrected where their input or expectation encoded an invalid contract: the gap-stop bar now has valid OHLC, the flatten test supplies a real open at the timer time, VWAP warmup uses distinct consecutive minutes, seeded trade positions supply activation timing, and an unmatched reservation release raises instead of clamping. No test is skipped to make the repair pass.

## Checks executed

- `uv run --frozen pytest tests/ -q`: **622 passed**.
- `uv run --frozen ruff check .`: clean.
- Public command verification: **19 invocations**, all with expected exit codes. This includes fixture config validation (0), unresolved research config validation (2), four R3 replay configs twice each (0), F01–F17 twice (0), ORB and VWAP session configs with reports twice each (0), one identical-pair comparison per session type (0), and blocked final evaluation (5).
- Repeated replay, fixture and session invocations produced equal semantic trace hashes and distinct retained attempts.
- ORB example: starting equity `10000`, ending equity `9880`, one completed trade. VWAP example: starting/ending equity `10000`, one completed breakeven trade. These are synthetic diagnostics, not backtest returns.
- New repair tests live in `tests/test_m2_account_repairs.py`, `tests/test_m2_run_repairs.py` and `tests/test_m2_evidence_repairs.py`; the original 557-test suite remains included.

The initial independent repair regressions reproduced 20 failures before the fixes. A passing final suite establishes the tested contracts, not completion of every requirement in the original M2 handoff.

## Supported scope and remaining work

See [CURRENT_SYSTEM_AND_NEXT_STEPS.md](CURRENT_SYSTEM_AND_NEXT_STEPS.md) for the full module inventory and implementation/test instructions for the next agent. Remaining work includes a shared execution/account contract across adapters; integrated synthetic trade-input P&L; historical scanner data; multi-session state and settlement; complete corporate-action support; broader comparison/report definitions; and a real loader-level holdout boundary.

Synthetic opens are explicitly modeled observations, not inferred historical receipt timestamps. OHLC touch timing is not known exactly. Overlapping unpublished intervals while holding a position are rejected rather than silently retroactively settling protection. Split conversion in account fixtures is restricted to known-at-effective actions and exact supported quantity increments. No real provider or broker policy is validated.

The research decisions remain open. Final evaluation, market experiments, optimization, paper trading and live execution remain outside the supported system.

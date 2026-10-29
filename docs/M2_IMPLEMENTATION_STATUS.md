# M2 implementation status

Source: `docs/M2_IMPLEMENTATION_HANDOFF.md`. Checkout: branch `fix/r3-replay-causality`,
HEAD `2cd1e4a3173b6ea923f0a5ecbd93166af181cc93` (matches the handoff's pinned repaired
commit; no reset or merge was needed). No commits were made to git history by this pass;
changes are in the working tree only.

## Step 0 — baseline (done)

```
git rev-parse HEAD            -> 2cd1e4a3173b6ea923f0a5ecbd93166af181cc93
uv sync --frozen --group dev   -> ok, 20 packages, no changes
uv run pytest tests/ -q        -> 426 passed (pre-existing baseline)
uv run ruff check .            -> All checks passed
```

The four `rsys replay` example configs were not re-run in this pass (no runtime files were
touched by this slice); re-run them before relying on this status for later steps.

## What was implemented (subset of M2-01, steps 2-4)

| File | Role |
|---|---|
| `src/rsys/orders.py` | Typed immutable records: `Proposal`, `AcceptedIntent`, `Position`, `Fill`, `Checkpoint`, rejection-reason constants |
| `src/rsys/portfolio.py` | Decimal ledger: total/spendable/unsettled/reserved cash, `reserve`/`release_reservation`/`apply_settlement_release`/`buy`/`sell`/`equity` |
| `src/rsys/sizing.py` | Cost-aware risk sizing (`size_quantity`): largest quantity satisfying risk budget, fixed costs and available cash, floored to the quantity step |
| `src/rsys/fixture_engine.py` | `FixtureEngine`/`run_case`/`compare_case`: interprets one `fixtures/cases.json` case's event stream through the Portfolio/sizing modules and emits an ordered checkpoint sequence plus final account state |
| `src/rsys/fixture_cli.py` | `run_fixture_cli`: loads cases, runs the selected case(s), retains one evidence attempt (trace.jsonl + manifest.json) per invocation via the existing `experiments.start_run`/`run_context`/`RunStorage` lifecycle |
| `src/rsys/acceptance.py` | `sort_proposals` (stable `(decision_available_at, scanner_rank, instrument_id, strategy_id, proposal_id)` ordering) and `AcceptanceBook` (rejects a missing rank with `NO_CURRENT_RANK`, rejects a reused `proposal_id` with `DUPLICATE_PROPOSAL` without double-reserving) |
| `src/rsys/phase_engine.py` | `PhaseEngine`: standalone `(available_at_ns, phase, event_id)`-ordered scheduler proving the declared 10-80 phase table in isolation (frozen same-time decision views, settlement-before-decision, closed-phase rejection, idempotent/conflicting redelivery) |
| `src/rsys/scanner.py` | `rank_candidates`: deterministic ranking layer only (descending RVOL, descending gain, ascending symbol, top-N, excluded-with-reason) -- real prior-session RVOL history selection is NOT implemented (see gap 5) |
| `src/rsys/strategies/orb.py` | `OrbStrategy` (`orb_close_cross_v1`): standalone completed-bar state machine (COLLECT_RANGE/ARMED/PROPOSED/IN_POSITION/DONE/EXPIRED) producing `Proposal` objects; stop from frozen range low, target computed from the actual fill |
| `src/rsys/strategies/vwap.py` | `VwapStrategy` (`vwap_touch_hold_v1`): standalone completed-bar state machine (WARMUP/IDLE/ARMED/PULLBACK/PROPOSED/IN_POSITION/DONE/EXPIRED); bar-price-proxy VWAP, rising-slope arm, touch-then-confirm pullback, stop from the pullback-window lows |
| `src/rsys/standalone_run.py` | `StandaloneRunner`: composes `scanner.rank_candidates` + ORB/VWAP strategies + `PhaseEngine` + `sizing`/`acceptance`/`Portfolio` into one end-to-end proposal -> acceptance -> next-bar-open entry fill -> protective stop/target exit (gap and intrabar) -> max-hold/flatten timer flow |
| `src/rsys/trade_execution.py` | `TradeExecutionAdapter` (`subsequent_trade_estimate_v1`, M2-01T): fills/exits on a later eligible trade print; rejects self-consumed/pre-activation/stale/bad-quality/corrected trades; idempotent against redelivery |
| `src/rsys/evaluation.py` | `compute_metrics`/`reconstruct_completed_trades` (reconciled win rate, expectancy, profit factor with explicit nonfinite/unavailable states, never zero-defaulted) and `compare_runs`/`RunAssumptions` (rejects an unexplained assumption difference instead of silently comparing) |
| `src/rsys/holdout.py` | `HoldoutGuard`/`PartitionManifest`: denies access to a sealed/undeclared/aliased partition path before any read is attempted; `verify_partition_integrity` catches a tampered frozen file; `final_evaluate()` is permanently, truthfully blocked (D16/D17 unresolved) |
| `src/rsys/run_cli.py` | `rsys run --config PATH --out {auto,PATH}`: loads a synthetic session JSON config (symbols -> ORB/VWAP strategy + bars, sizing, execution, scanner inputs), drives `StandaloneRunner`, computes `evaluation.compute_metrics`, and retains one evidence attempt (trace.jsonl + manifest.json) via `experiments.start_run`/`run_context`/`RunStorage` |
| `src/rsys/compare_cli.py` | `rsys compare --storage-root PATH --runs ID_A ID_B --out PATH`: loads two retained `rsys run` manifests, reuses `evaluation.compare_runs` to reject an unexplained assumption difference (universe/cost/risk/execution), and reports the ending-equity delta |
| `src/rsys/report.py` | `render_report_for_attempt`: a portable, self-contained HTML report (no external assets/network) rendered from a retained `rsys run` attempt's manifest.json + trace.jsonl; HTML-escapes every value; zero-trade runs are labeled, not silently blank |
| `src/rsys/cli.py` | `rsys fixture --case {F01..F17,all}`, `rsys run --config ... [--report]` and `rsys compare --runs ...` wired to real behavior (all three were stubs) |

Tests added: `tests/test_fixture_cases.py` (all 17 cases, parameterized, exact Decimal
checkpoint/final-state assertions against the fixture's own JSON), `tests/test_portfolio.py`,
`tests/test_sizing.py`, `tests/test_fixture_cli.py` (subprocess CLI tests including exit
codes, repeated-invocation distinct-attempt check, and a mutated-checkpoint negative control
that proves the comparison actually checks values), `tests/test_acceptance.py` (rank/time
sort order independent of input order, `NO_CURRENT_RANK`, duplicate-id rejection without
double-reserving, a rejected id is not marked seen, cancel/expiry release exactly the
reservation and free cash for a later proposal), `tests/test_fixture_engine_timers.py`
(synthetic cases, not F01-F17: pending-intent expiry, max-hold auto-flatten, a rejected
proposal freeing its symbol for a fresh one, and `max_fills_per_symbol_day`),
`tests/test_engine_order.py` (two-symbol reversed-delivery-order invariance reusing
`rsys.acceptance`/`rsys.portfolio`, a decision's frozen view never seeing a later publish,
settlement-before-decision with a stop change only affecting later settles, a closed-phase
rejection that mutates nothing, idempotent vs. conflicting event redelivery, and
ready-at/event-key execution eligibility), `tests/test_scanner.py` (rank order, tie-break,
input-order independence, missing-input exclusion, top-N capping without losing the full
ranking) and `tests/test_orb.py` (S01/S02 from `fixtures/strategy_cases.json` plus
ORB-01..07: no signal before the range is available, the breakout bar itself never folds
into the frozen range, strict-crossing-only, noncandidate crossings produce no proposal,
no re-entry after one fill, entry-deadline expiry, future-bar perturbation leaving the
prior proposal unchanged, and target computed from the actual fill), and `tests/test_vwap.py`
(S03/S04 plus VWAP-01..08: hand-calculated weighted average, warmup/zero-volume, slope on
completed bars only, arm/touch/confirm as separate events, previous-high excluding the
confirmation bar, invalidation-before-confirmation, pullback-window-only stop, future-bar
perturbation, a noncandidate confirmation not pausing the window, and age-6 rejection), and
`tests/test_standalone_run.py` (a candidate's ORB breakout is accepted and filled at the
NEXT bar's open -- never the signal close -- while an identical noncandidate breakout on a
second symbol never proposes; insufficient cash releases the reservation and rearms the
strategy; a gap through the stop at fill time rejects without opening a position; a filled
position later exits via a gap-through-stop, an intrabar stop/target touch, a max-hold
timer and a flatten timer; no exit fires on the entry bar itself), and
`tests/test_trade_execution.py` (T01-T09: self-consumed-trade rejection, zero/positive
latency readiness boundaries, pre-activation staleness despite late delivery, observed
stop-vs-target trade sequence rather than a bar-mode "stop wins" rule, entry trade cannot
also exit, gap-through-stop adverse print, duplicate redelivery and correction idempotency,
bad-quality rejection, and a later trade never changing an earlier fill), and
`tests/test_evaluation.py` (zero-trade metrics unavailable not zero, no-losses profit
factor explicitly not-defined rather than `Infinity`, hand-calculated mixed win/loss
metrics, an unmatched exit skipped rather than zero-scored, and comparison rejecting an
unexplained assumption difference unless explicitly declared permitted); one further test
in `tests/test_standalone_run.py` reconciles `compute_metrics` against a real
`StandaloneRunner` session's fills/portfolio. `tests/test_holdout_boundary.py` (denial
before any read via a reader that fails the test if called; a warmup-labeled request and a
path alias against a sealed partition are both still denied; an unknown/undeclared path is
denied, not silently allowed; duplicate path declarations across partitions are rejected at
construction; a tampered frozen file fails integrity verification; `final_evaluate()` is
always truthfully blocked with its missing D16/D17 requirements listed). `tests/test_run_cli.py`
(subprocess tests for `rsys run`: a real session retains an attempt with the expected
ending equity and both a FILLED and an EXIT record in its trace; a malformed config and an
unknown strategy id both exit 2 with a clear message; repeated invocations get distinct
retained attempts with identical semantic trace output). `tests/test_compare_cli.py`
(identical configs compare with no diffs and a zero delta; a changed universe is an
unexplained difference that exits 4; a declared permitted difference is reported, not
rejected; an unknown run ID is a data error exiting 3). `tests/test_report.py` (run ID,
metrics and trace rows present in the rendered HTML; untrusted values are HTML-escaped;
zero-trade runs are explicitly labeled, not silently blank; a missing manifest raises
`ReportError`); one further test in `tests/test_run_cli.py` checks `rsys run --report`
writes a readable `report.html` next to the retained attempt.

**Result:** `uv run pytest tests/ -q` -> 557 passed (426 baseline + 131 new). `uv run ruff
check .` -> clean. `uv run rsys fixture --case all --out <dir>` -> exit 0, all 17 cases
`ok`, retained trace/manifest written under `<dir>/attempts/<run_id>/`. `uv run rsys run
--config fixtures/standalone_run_fixture.json --out <dir> --report` -> exit 0, retained
trace/manifest/report.html. `uv run rsys compare --storage-root <dir> --runs <ID_A> <ID_B>
--out <dir2>` -> exit 0.

## What was NOT implemented (honest gaps)

This pass delivers a usable, independently-tested **subset** of M2-01 (steps 2-4: ledger,
sizing, bar-execution rules, F01-F17 fixture CLI), not the full M2-01 gate and not any of
M2-01T/M2-02/M2-03/M2-04:

1. **Step 1 (M2-01a), phase-aware engine.** `src/rsys/phase_engine.py` now proves the
   declared phase ordering (same-time batching, frozen decision views, settlement-before-
   decision, closed-phase rejection, idempotent/conflicting redelivery) in
   `tests/test_engine_order.py`, reusing `rsys.acceptance`/`rsys.portfolio` for the account
   side. `src/rsys/standalone_run.py` composes it with `scanner`/ORB/VWAP/`sizing` into one
   proposal -> acceptance -> entry-fill flow (`tests/test_standalone_run.py`). Both remain a
   **standalone scheduler**, not an extension of `ReplayRunner`/`SeriesRouter`: no code or
   state is shared with the existing data-only replay runtime in `replay_runtime.py`, and
   `FixtureEngine` (F01-F17) still runs its own local interpreter, not `PhaseEngine`.
   `standalone_run.py` also models protective stop/target exits (gap and intrabar touch)
   and max-hold/flatten timers, but not splits, corporate actions or scanner-removal
   edge cases beyond what ORB/VWAP already handle themselves.
   `tests/test_order_contracts.py` (typed proposal/fill contract tests beyond what
   `orders.py`'s dataclasses already give for free) was not written. Wiring a real
   multi-symbol market-data feed and `ReplayRunner` integration remain future work.
2. **Acceptance ordering contract.** `src/rsys/acceptance.py` now implements the general
   `(decision_available_at, scanner_rank, instrument_id, strategy_id, proposal_id)` sort,
   `NO_CURRENT_RANK` and duplicate-id rejection as a standalone, unit-tested module
   (`tests/test_acceptance.py`). It is **not yet wired into `FixtureEngine`**: F01-F17 still
   use fixture_engine.py's own local sort/accept path (fixture cases are explicitly exempt
   from rank requirements per the handoff), so this module has no effect on the F01-F17
   fixture CLI result yet. Wiring it in is future work once a real scanner exists to supply
   ranks.
3. **Expiry, cancel-and-rearm, and max-fills-per-day** are now implemented in
   `FixtureEngine` (`Proposal.expiry_ms`/`max_hold_ms`, `max_fills_per_symbol_day` on the
   case, `tests/test_fixture_engine_timers.py`), using synthetic event sequences since none
   of F01-F17 exercise them. These fields are fixture-engine-only conventions (read off the
   raw event/case JSON), not yet part of a real strategy's proposal contract.
4. **M2-01T (subsequent-trade execution adapter).** `src/rsys/trade_execution.py` implements
   `subsequent_trade_estimate_v1` standalone (`tests/test_trade_execution.py`, T01-T09). It
   is not wired into `StandaloneRunner`/`PhaseEngine`/real trade data, and T06 (exits/entries
   competing for shared cash through this adapter specifically) and T10 (a fixture-only
   intrabar strategy proving the real engine path) were not written -- T06 is already
   covered at the acceptance/portfolio layer (`tests/test_acceptance.py`), and T10 requires
   the phase-aware engine integration this pass does not deliver. Quote state is not
   modeled at all.
5. **M2-02/M2-03 (features, scanner, ORB, VWAP strategies).** `scanner.py` implements only
   the ranking/tie-break/exclusion layer, not the real H01-H03 prior-session RVOL history
   selection or a verified dollar-volume eligibility audit (both need calendar-aware
   historical data loading, out of scope here); callers must already have `rvol`/`gain`
   per symbol. `strategies/orb.py` implements `orb_close_cross_v1` standalone (not wired
   into `PhaseEngine`, a real data feed, `rsys.sizing` or `rsys.acceptance`); the scanner
   candidate flag is passed in by the caller per bar, not computed by the strategy itself.
   `strategies/vwap.py` implements `vwap_touch_hold_v1` with the same scope/limits as ORB
   (standalone, not wired into `PhaseEngine`/`rsys.sizing`/`rsys.acceptance`).
6. **M2-04 (synthetic run/compare/report path, holdout boundary).** `src/rsys/evaluation.py`
   implements the reconciled-metrics and assumption-comparison subset
   (`tests/test_evaluation.py`). `src/rsys/run_cli.py` persists a `StandaloneRunner` session
   as a retained run attempt behind a real `rsys run` CLI command, `src/rsys/compare_cli.py`
   implements a real `rsys compare` command comparing two retained `rsys run` manifests
   (`tests/test_compare_cli.py`), and `src/rsys/report.py` renders a portable, self-contained
   HTML report from a retained attempt (`rsys run --report`, `tests/test_report.py`). There
   is still no multi-session composition, and `rsys compare`/the report only know about
   `rsys run` (`StandaloneRunner`) attempts -- not `rsys replay` attempts or cross-
   storage-root comparisons. `run_cli.py`'s config schema is a new, separate synthetic
   schema -- it does NOT reuse or validate against `rsys.config.ReplayConfig`.
   Exposure/turnover/drawdown and mark/equity-series metrics are not implemented.
   `src/rsys/holdout.py` implements the access-denial contract standalone
   (`tests/test_holdout_boundary.py`) over synthetic partitions; it is not wired into any
   real data loader, `rsys final-evaluate` still returns its pre-existing stub exit code
   rather than `FINAL_EVALUATE_EXIT_CODE`, and no real frozen holdout manifest exists.
7. **Causality matrix (C01-C15)** was not run against this slice; it targets the full
   phase-aware engine, not the fixture-only interpreter.

## Requirement -> test mapping (delivered subset)

| Requirement | Test |
|---|---|
| F01-F17 exact checkpoints | `tests/test_fixture_cases.py::test_fixture_case[F01..F17]` |
| Money identities, fee-once, reservation release | `tests/test_portfolio.py` |
| F13/F14 cost-aware sizing | `tests/test_sizing.py` |
| CLI exit codes, retained distinct attempts, negative control | `tests/test_fixture_cli.py` |

## Open decisions

No new D09-D22 decision was resolved or invented. `costs`/`sizing` values used by
`FixtureEngine` come only from each fixture case's own declared JSON fields; they are not
treated as approved research defaults anywhere in this code.

## Next dependency

To continue toward the full M2-01 gate: implement the phase-aware engine extension
(step 1) and retrofit `FixtureEngine`'s rules into it so F01-F17 run through the shared
multi-symbol scheduler, not a case-local interpreter. That is a prerequisite for M2-01T,
the scanner and the strategies, since they all depend on the same phase ordering.

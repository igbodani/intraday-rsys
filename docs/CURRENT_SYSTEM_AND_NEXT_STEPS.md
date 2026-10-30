# Current system and next steps for Copilot

Review date: 2026-10-29. Repair base: `de33f3303aa54e7a9fdfa0bd9ba897be000a5bf4` on `fix/r3-replay-causality`. The earlier `2cd1e4a` baseline preceded the standalone M2 additions. This document and [M2_REPAIR_RESULTS.md](M2_REPAIR_RESULTS.md) describe the repair that follows those additions. Use the containing commit as the exact repaired source revision.

## What the system is meant to do now

This is an **offline synthetic research-engine prototype**. It can replay declared synthetic data, construct causal bar histories, exercise account invariants, and run a small ORB or VWAP example through scanner ranking, proposals, cash reservation, modeled execution, position protection, metrics and retained reports. It is useful for testing software behavior before buying data or conducting market research.

There are two public execution paths with deliberately different input schemas:

- `rsys replay` uses `ReplayConfig`, the R3 event/data pipeline, coverage declarations, bar builders, revisions and optional synthetic observers. Its `execution.data_basis` remains `inactive`: it does not produce trade P&L.
- `rsys run` accepts the strict version-1 `synthetic_standalone` JSON schema. Its session runner now binds source publication and account events to `ReplayRunner.domain_engine()`. Strategies receive completed bars through bounded replay contexts. Opens are separate **modeled** execution observations. This path produces synthetic account P&L; it cannot ingest recorded trades, provider credentials or a real data partition.

`StandaloneRunner` keeps its existing Python name for compatibility. Calling it “standalone” no longer means that its source clock and decision views bypass ReplayRunner. It still does not implement the full trade-input/account pipeline.

A successful finite synthetic input is not evidence of a complete historical trading day. A null flatten setting allows a diagnostic ending snapshot with open positions. If flatten or max-hold is configured, an outstanding required exit without an eligible subsequent open makes the attempt fail as incomplete. No overnight trading policy has been authorized.

## Is the existing work good and testable?

The modular boundaries are useful: Decimal account arithmetic, proposal records, strategies, scanner ranking, bounded views and evidence persistence can be exercised independently. The public CLIs now provide repeatable integration checks. The repair added failure-producing regression cases before changing the unsafe behavior, then tested the actual session runner and retained artifacts.

The original 557 green tests were insufficient evidence for execution correctness. In particular, the account fixture interpreter and the session runner implemented different execution timing, and several tests encoded the mistaken “no entry-bar exit” or “fill at publication using a past open” behavior. A fixture oracle also ignored several expected fields. Those are material defects, not cosmetic test gaps. The repair addresses them and adds whole-run future-prefix and negative-control tests.

The architecture follows the declared path for the **synthetic bar slice**. It remains partial M2 overall. Fixture account actions, synthetic bar sessions and the trade adapter still have separate policy handlers; sharing the phase queue does not prove all three are equivalent. More features should be integrated through existing replay contexts and account transitions, with differential tests, rather than introducing another independent scheduler.

## Implemented modules and what the tests actually prove

All paths below are repository-relative links to executable tests. A passing row establishes the listed software behavior, not market realism.

| # | Module / responsibility | Exact tests and key assertions | Current limit |
|---|---|---|---|
| 1 | Domain records and Decimal ledger: `orders.py`, `portfolio.py`, `money.py` | [test_portfolio.py](../tests/test_portfolio.py): cash/equity identities, fees once, unsettled proceeds unavailable. [test_m2_account_repairs.py](../tests/test_m2_account_repairs.py): overspend/invalid transitions are atomic, equal-sized reservations retain ownership, duplicate fills/releases do not charge twice, nonfinite/boolean amounts fail. | One long position per symbol; explicit synthetic settlement. No verified broker account profile. |
| 2 | Sizing: `sizing.py` | [test_sizing.py](../tests/test_sizing.py): hand-calculated cost-aware cash/risk bounds. [test_m2_account_repairs.py](../tests/test_m2_account_repairs.py): maximum 2.5 with whole-share step gives 2; nondecimal tick increments round correctly. [test_m2_run_repairs.py](../tests/test_m2_run_repairs.py): actual gap price rechecks cash and risk without upsizing. | Fixed fees and supplied absolute budget; no authorized research risk fraction or daily-loss policy. |
| 3 | Acceptance and owned reservations: `acceptance.py` | [test_acceptance.py](../tests/test_acceptance.py): deterministic rank/time ordering, missing-rank rejection, duplicate identities, failed acceptance retries. Repair tests cover pending-symbol conflicts and owned cancellation. | No portfolio optimization or broker order lifecycle. |
| 4 | Phase queue and replay bridge: `phase_engine.py`, `replay_runtime.py`, `views.py` | [test_engine_order.py](../tests/test_engine_order.py): settle-before-decision, stable same-time batches and closed-phase rejection. [test_m2_account_repairs.py](../tests/test_m2_account_repairs.py): nested snapshots, caller mutation, conflicting full event identities. [test_m2_run_repairs.py](../tests/test_m2_run_repairs.py): real ReplayRunner contexts and identical complete trace prefixes under future price changes, with an earlier-price negative control. Existing [test_r3_clock_fixes.py](../tests/test_r3_clock_fixes.py), [test_replay_contract_fixes.py](../tests/test_replay_contract_fixes.py) and [test_causality.py](../tests/test_causality.py) retain R3 coverage. | Source/data replay and synthetic account composition are integrated for both the bar-open and trade-input paths (module 15). |
| 5 | Account fixture engine and CLI: `fixture_engine.py`, `fixture_cli.py` | [test_fixture_cases.py](../tests/test_fixture_cases.py): F01–F17 hand-calculated checkpoints. [test_fixture_engine_timers.py](../tests/test_fixture_engine_timers.py): expiry/rearm/max-hold. [test_fixture_cli.py](../tests/test_fixture_cli.py): subprocess commands, distinct attempts and a corrupted expected-value negative control. [test_m2_evidence_repairs.py](../tests/test_m2_evidence_repairs.py): ignored-field negative controls, artifact persistence and marked equity across splits. | Synthetic account events only. Late-known splits, pending-intent split conversion and fractional cash-in-lieu are explicitly unsupported. |
| 6 | Scanner ranking: `scanner.py` | [test_scanner.py](../tests/test_scanner.py): RVOL/gain/symbol ordering, exclusions, top-N independent of full ranking, `ranking-json-v1` digest reconstruction and digest sensitivity to a changed ranked value. Session repair test verifies missing RVOL is excluded with its reason. | Scores come from either a caller-supplied fixture (`CandidateInput` direct) or the causal historical mode (module 17); no real historical dollar-volume eligibility loader. |
| 7 | ORB baseline: `strategies/orb.py` | [test_orb.py](../tests/test_orb.py): S01/S02, strict crossing, frozen range, deadline, one fill, target from actual entry. Repair tests reject an incomplete 15-minute range and prove fill-time risk checks. | Completed-bar strategy only; session data requirements are conservative. No forming-bar ORB variant. |
| 8 | VWAP baseline: `strategies/vwap.py` | [test_vwap.py](../tests/test_vwap.py): S03/S04 weighted sums, warmup, slope, arm/touch/confirmation order and age limits. Repair test drives S03 through public configuration and positive latency, asserting cumulative observations continue while pending/held. | Bar-price proxy, one session; missing-history recovery is unspecified and disables further setup use. |
| 9 | Synthetic session composition: `standalone_run.py` | [test_standalone_run.py](../tests/test_standalone_run.py): scanner-independent protection, gap/stop/target exits and timers. [test_m2_run_repairs.py](../tests/test_m2_run_repairs.py): entry-interval protection, delayed publications, expiry without subsequent data, missing required flatten, rank-ordered shared cash and marked equity. | Modeled opens at interval starts; no exact intrabar touch timestamp, real fills, partial fills or corporate actions in session input. Overlapping unpublished position intervals fail explicitly. |
| 10 | Subsequent-trade adapter: `trade_execution.py` | [test_trade_execution.py](../tests/test_trade_execution.py): self-consumed, stale, corrected and bad-quality prints; observed stop/target sequence. Repair tests add pre-ready market-time rejection, expiry, pre-entry late-print rejection and one terminal exit. | Isolated adapter plus `force_exit` for flatten/max-hold; connected to account sizing/reservations through `trade_input_run.py` (module 15), not yet a public CLI/config path. |
| 11 | Metrics and comparison: `evaluation.py`, `compare_cli.py` | [test_evaluation.py](../tests/test_evaluation.py): hand-calculated wins/losses, unavailable/no-loss states and intentional differences. [test_compare_cli.py](../tests/test_compare_cli.py): subprocess acceptance/refusal. Repair tests check net fees, breakeven labeling, capital/data/scanner/strategy differences and artifact tampering. | Two successful version-2 synthetic run manifests only. No multi-session statistics, confidence intervals or promotion gate. |
| 12 | Holdout guard: `holdout.py` | [test_holdout_boundary.py](../tests/test_holdout_boundary.py): denied reads never invoke a reader; aliases, warmup labels and changed files do not bypass the guard. Repair subprocess test requires blocked exit 5 for `final-evaluate`. | A tested guard, not a completed data-loader access boundary. Final evaluation remains disabled. |
| 13 | Strict run configuration and retained lifecycle: `session_config.py`, `run_cli.py`, `experiments.py`, `storage.py` | [test_run_cli.py](../tests/test_run_cli.py): public run/report paths and deterministic repeated traces. [test_m2_evidence_repairs.py](../tests/test_m2_evidence_repairs.py): exact byte hash, unreadable/malformed/incomplete attempts retained, persisted artifacts, chronological phase trace and input versions. [test_run_record.py](../tests/test_run_record.py) retains transactional lifecycle coverage. | One dated 2024 NYSE regular session, one-minute raw bars; unsupported fields and research labels fail. |
| 14 | Portable report: `report.py` | [test_report.py](../tests/test_report.py): HTML escaping, metric/trace rows, zero-trade labeling, missing-input errors. Run/evidence tests verify report creation and retention before terminal finalization. | HTML diagnostic report for synthetic run output; no research performance dashboard. |
| 15 | Trade-input synthetic execution composition: `trade_input_run.py` | [test_trade_input_run.py](../tests/test_trade_input_run.py): a later eligible trade fills entry (never the bucket's own first print), a stop print exits at its own reference price, flatten settles on the next eligible trade (not a bar open), and a missing closure trade raises `IncompleteSessionError`. | `subsequent_trade_estimate_v1` execution mode (Step 2/M2-01T); Python API only, no public CLI/config command yet. `rsys replay` is unchanged and remains data-only. |
| 16 | Causal historical features: `features.py` | [test_features.py](../tests/test_features.py): H01-H03 cumulative-RVOL selection/exclusion (hand-calculated from `fixtures/data_cases.json`), DQ08 mixed-feed rejection, insufficient calendar history, zero denominator, missing previous close, split-unit conversion invariance, explicit dollar-volume threshold. | Computes RVOL/gain/eligibility from caller-supplied `HistorySession` rows; not a historical data loader. Blocked on D12/D13 for real coverage and on D14 for the unset `min_dollar_volume`. |
| 17 | Historical scanner mode: `scanner_history.py` | [test_scanner_history.py](../tests/test_scanner_history.py): candidate math matches the H01 hand calculation, dollar-volume-ineligible exclusion, builder output shape, a causality negative control (a later bar never changes an earlier decision), and a real `StandaloneRunner.run_session` composed through the historical mode in place of supplied scores. | `FixtureHistoryProvider` is declared synthetic, not a provider adapter. `HistoryProvider` is a narrow interface; a real calendar-driven loader against live data remains blocked on D12/D13. |
| 18 | Multi-session composition: `session_coordinator.py` | [test_session_coordinator.py](../tests/test_session_coordinator.py): consecutive sessions each independently detect/fill/exit the same breakout (no ORB/one-fill state leak), realized P&L accumulates exactly once per session, a zero-bar symbol never fabricates a proposal, a calendar-derived flatten fires at the correct early-close instant, an open position triggers `UnreconciledPositionError` on the next session, an unresolved required flatten still raises `IncompleteSessionError`, a non-trading day is rejected before any session state is built, a saved prior session's context is unchanged afterward, and an exact/late-known/fractional split are each handled per `docs/data_rules.md`. | `Portfolio` is the only carried state; only an exact-ratio split is a supported corporate action (D09 blocks fractional/settlement semantics beyond it). DST-crossing composition is not explicitly tested (delegated to `calendar.py`'s own coverage). |
| 19 | Multi-session exposure and drawdown statistics: `multi_session_evaluation.py` | [test_multi_session_evaluation.py](../tests/test_multi_session_evaluation.py): hand-calculated max drawdown and its percentage of the correct peak; empty-curve and nonpositive-peak explicit unavailable results; overlapping same-session positions union instead of double-counting exposure; an unexited position is closed out at its own session's scheduled close; exposure sums correctly across multiple sessions; zero sessions is explicit; `MultiSessionMetrics` combines two sessions' fills and equity correctly. | Descriptive diagnostics only, not a risk limit or promotion gate (D10/D17 stay Open). No CLI/report command runs multiple sessions yet; `rsys run` remains single-session. |

## Corrected behavior that callers must account for

1. An entry at the 09:46 open is recorded at 09:46. It no longer waits until the 09:47 close to pretend the earlier open was available. A decision arriving at 09:46:30 must wait for a later eligible open.
2. Entry-interval stops and targets are active. The interval's low/high become usable for conservative settlement at publication. Stop takes priority when OHLC cannot order both touches. No exact touch time is invented.
3. Timers exist independently of market observations. A 09:48 flatten cannot use the 09:47 open. Missing subsequent execution data produces an incomplete attempt.
4. Reservations belong to intents. Callers should retain `AcceptedIntent.reservation_id` and supply it to cancellation/fill operations. Amount-only compatibility is limited to an unambiguous reservation; it cannot clamp or release someone else's cash.
5. Fill-time sizing uses actual modeled entry price, current free cash plus the intent's own reservation, the original risk budget and a cap at accepted quantity. Gap prices can reduce size or reject; they cannot borrow cash.
6. Completed-trade statistics use net P&L including entry and exit fees. Open positions use the last published mark. Account equity and trade statistics answer different questions and are both retained.
7. Version-2 run manifests identify execution/scanner/account bases, resolved strategy and execution settings, source/dependency/config/data hashes and chronological trace hashes. Old version-1 manifests must be rerun before comparison. They are not silently upgraded or treated as equivalent evidence.
8. The session schema rejects unknown settings, dates spanning multiple sessions, offset/sub-millisecond timestamps, out-of-session bars, invalid OHLC/volume, premature completed bars and unsupported evidence labels. `strategy_config` is forwarded and retained rather than ignored.

## Copilot work order after this repair

Read `AGENTS.md`, `docs/decisions.md`, this document and the original [M2_IMPLEMENTATION_HANDOFF.md](M2_IMPLEMENTATION_HANDOFF.md). The original handoff remains the detailed requirement list; its starting-state descriptions are historical. Do one bounded ticket at a time. Keep current tests and the public synthetic paths green. Add a regression that fails before changing a known bug. Never manufacture research values to unblock an interface.

### Step 0 — establish the next checkout's baseline

Inspect the branch and worktree, preserve user edits, install the frozen lockfile, then run the full test and lint commands below. Rerun both synthetic session examples and all four R3 replay configs. Record the actual commit, commands, outcomes and any differences from this repair. Do not use a historical test count as proof about a newer checkout.

**Acceptance:** all existing tests and public examples pass; two identical invocations produce different attempt IDs but equal semantic traces. A malformed run still leaves a failed attempt. This is the starting gate for every next ticket.

### Step 1 — consolidate execution contracts before expanding inputs

Work in `orders.py`, `standalone_run.py`, `fixture_engine.py`, `trade_execution.py` and `replay_runtime.py`. Define one explicit fill/account transition contract containing instrument, intent identity, activation key, execution-reference identity, market time where known, availability key, quantity, price, fees and reason. Keep a modeled OHLC touch's exact market time unavailable. Extract genuinely shared cash/quantity revalidation and terminal-order behavior from the fixture/session handlers; preserve their declared execution differences.

Introduce one adapter boundary for bar-open versus subsequent-trade execution. An adapter selects an eligible reference and price; the account service validates and commits the fill exactly once. Do not let an adapter independently bypass cash or risk checks. Do not merge trade and bar liquidity or silently turn an R3 data-only replay into a trading run.

**Tests:** run F01–F17 through the shared transitions; compare pre/post extraction checkpoints exactly. Add duplicate/corrected reference delivery, equal-value reservation ownership, conflicting identities and transaction atomicity tests. At an equal timestamp, prove exits precede entries and accepted-intent order governs competing fills. Keep the whole trace-prefix test with its negative control. Completion requires integration tests, not only the adapter unit tests.

### Step 2 — integrate synthetic trade-input execution (M2-01T)

Add an explicit, separately labeled synthetic execution mode using the existing trade replay stream and `SeriesRouter`. Route each decision's bounded view and consumed input versions to acceptance. Keep execution readiness and event eligibility separate: a late-delivered print with market time before readiness is not a new opportunity. Expiry must release cash without waiting for another trade; a required close with no eligible print must end incomplete.

Account for fees in the same ledger as bar sessions. Preserve entry/exit reference IDs and full keys in retained output. Corrections may affect future observations but must not rewrite a prior fill. If two prints share a timestamp, use full event ordering and consumed-reference evidence rather than guessing order from milliseconds alone; the current isolated adapter is deliberately conservative at equal times. Keep `rsys replay` data-only unless an explicit new command/config contract enables execution.

**Tests:** reuse T01–T09, then test real engine-to-ledger composition with late prints, positive latency, expiry, same-time sequences, scanner removal, duplicate exits, correction-after-fill and missing closure data. Compare account cash, realized P&L, fees and ending holdings to hand calculations. Perturb only future trades/corrections and compare the full earlier trace. A bar/trade comparison is descriptive only when all other assumptions match and the execution difference is explicit.

### Step 3 — implement calendar-aware scanner history (M2-02a)

Add a history-provider interface that returns only observations available by the decision cutoff, including source identity, session date, interval coverage and unit basis. Implement same-time cumulative volume, valid prior-session selection and prior-day dollar-volume eligibility using the declared calendar. Distinguish missing data, verified no-trade intervals and zero volume. Respect early closes and supported as-of split conversions. Do not use today's surviving asset list as historical membership.

Replace supplied scores in a new explicit scanner mode. Keep the existing supplied-score fixture mode for isolated tests. Retain denominator session IDs, excluded sessions/reasons and ranking inputs so each decision can be reconstructed.

**Tests:** hand-calculate RVOL using a tiny multi-day fixture; test missing denominator, zero denominator, early-close mismatch, insufficient history, delayed/revised prior observations, split unit mismatch and historical-universe exclusion. Change a future day's volume and show earlier eligibility/ranks remain identical. Verify position protection continues when a symbol leaves top-N. Real provider coverage and policy remain blocked by D12/D13/D22.

### Step 4 — compose multiple sessions without leaking state

Add a session coordinator around the tested per-session components. Explicitly separate persistent account/settlement state from daily scanner, strategy, one-fill and timer state. Use full dated nanosecond keys everywhere; local milliseconds are only a legacy boundary representation. Validate the declared calendar across holidays, DST and early closes. Support only corporate-action transformations whose units and known-at timing are fully specified; otherwise retain an explicit unsupported result.

**Tests:** two consecutive sessions reset ORB range, VWAP sums and fill counters; cash changes exactly once; a no-data day does not fabricate observations; early-close flatten is enforced; an unreconciled position cannot silently carry overnight. Test late-known actions and fractional entitlements as failures until their policies are declared. A saved prior-session context must not change after session two. Market settlement and fractional quantity semantics require D09.

### Step 5 — extend evidence, reports and controlled comparisons

Build on versioned manifests and the existing SQLite lifecycle. Add exposure, drawdown and multi-session statistics only with explicit definitions and independent calculations. Keep requested analysis axes separate: scanner, entry, exit, execution and account/cost differences must be visible. Extend comparison deliberately to new evidence types; never accept arbitrary old manifests by filling missing assumptions with defaults.

Complete the data-loader holdout boundary before adding search, tuning or historical evaluation. Every read, including feature warmup and cached aliases, must use a partition manifest and be denied before I/O when unauthorized. Define failure/incomplete/report statuses consistently across public commands.

**Tests:** tampered/missing artifacts, unsuccessful attempts, schema migration, changed dates/capital/costs/strategy/source/dependencies, one permitted axis alongside a second forbidden difference, zero-trade and breakeven metrics, HTML escaping, and reader spies that prove denied holdout access performs no read. Keep failed attempts in storage. No final-evaluation output until the frozen protocol and access logging are implemented and authorized.

### Step 6 — resolve research decisions before real-data claims

D09 controls account/quantity/settlement; D10 risk; D11 execution costs/latency; D12 data coverage and budget; D13 historical universe; D16 development/validation/final partitions and trial budget; D17 promotion gates; D22 provider/feed event semantics. D19 affects expense interpretation. Implement typed interfaces and synthetic cases while these are open. Ask the owner for the unresolved research choices only when dependent work is ready; do not reinterpret fixture values as approval.

`final_evaluate()` remains blocked. There is no optimizer, live broker, paper-trading system or validated market strategy in this repair.

## Reproducible checks

```bash
uv sync --frozen --group dev
uv run pytest tests/ -q
uv run ruff check .
uv run rsys fixture --case all --out runs/fixture
uv run rsys run --config fixtures/standalone_run_fixture.json --out runs/orb --report
uv run rsys run --config fixtures/standalone_vwap_fixture.json --out runs/vwap --report
uv run rsys replay --config configs/replay_bar_fixture.yaml --out auto
uv run rsys replay --config configs/replay_trade_fixture.yaml --out auto
uv run rsys replay --config configs/replay_intrabar_observer_fixture.yaml --out auto
uv run rsys replay --config configs/replay_closed_observer_fixture.yaml --out auto
# Repeat the same run command; use the two returned run IDs from the same storage root:
uv run rsys compare --storage-root runs/orb --runs ID_A ID_B --out runs/comparison
uv run rsys final-evaluate  # expected blocked exit 5
```

Output roots may accumulate independent attempts; run IDs prevent overwrite. Reports are artifacts of the attempt when requested with `run --report`. Generated evidence is not source code and should not be committed. Read [M2_REPAIR_RESULTS.md](M2_REPAIR_RESULTS.md) for the exact checks executed for this repair.

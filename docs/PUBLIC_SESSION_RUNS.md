# Public bar/trade sessions and account policies

Implemented 2026-10-29 from `4e68583` on `fix/r3-replay-causality`. This document describes the public synthetic execution interface for priorities 1–2. It is not an Alpaca data adapter, historical performance claim or paper-trading interface.

## Run the examples

```bash
uv sync --frozen --group dev
uv run rsys run --config fixtures/sessions_bar_fixture.json --out runs/sessions-bar --report
uv run rsys run --config fixtures/sessions_trade_fixture.json --out runs/sessions-trade --report
# Repeat either command, then compare its two returned IDs:
uv run rsys compare --storage-root runs/sessions-trade --runs ID_A ID_B --out runs/comparison
```

Both fixtures run 2024-11-29 (early close) and 2024-12-02 with independent daily ORB state and one persistent experimental account. Each completes two trades. The bar example ends at **1022.34800** from 1000; the trade example ends at **1034.14420**. These are hand-checkable synthetic outcomes, not returns from real market data. VWAP is covered through both input modes in `tests/test_public_sessions.py`.

Existing version-1 `synthetic_standalone` files remain supported without adopting new research assumptions. Version-2 input uses `evidence_type: synthetic_sessions`; it produces a version-3 evidence manifest. `rsys replay` stays data-only and `final-evaluate` stays blocked. Research YAML validation is a separate, still-blocked interface; `configs/baseline_pending.yaml` does not become a historical run merely because the synthetic account policies now exist.

## Version-2 configuration

Use the complete JSON fixtures as runnable examples. All fields below are required except `top_n`, which resolves to 20. Unknown fields, unsupported dates/modes, nonfinite numbers, boolean numeric values and missing policy conventions fail before execution.

| Field | Contract |
|---|---|
| `schema_version` | Integer `2` |
| `evidence_type` | `synthetic_sessions` only; no historical/holdout/provider input |
| `input_mode` | `bar` or `trade`, fixed for the attempt |
| `initial_cash` | Positive Decimal amount, supplied once; no deposits/withdrawals between sessions |
| `top_n` | Positive scanner selection count; resolved and retained |
| `sizing` | Explicit positive `quantity_step`; `max_quantity` positive or null. No independent absolute-risk override |
| `risk` | Explicit `per_trade_fraction`, `daily_loss_fraction`, `maximum_drawdown_fraction`, `daily_loss_basis`, `halt_action` |
| `costs` | Explicit nonnegative `entry_fee`, `exit_fee`, combined `adverse_bps` below 10000, integer `feed_delay_ms` |
| `execution` | Explicit `max_hold_ms` (positive integer or null) and nonnegative integer `decision_latency_ms` |
| `completion_policy` | `require_flat`, or `allow_final_open_snapshot` for a deliberately diagnostic last session |
| `sessions` | Nonempty list of strictly increasing, unique dates; each session has `date`, `symbols`, `scanner_inputs`, `flatten_at` |

`flatten_at` is a local clock time (or dated local timestamp) strictly before the declared calendar close, or null. No position may carry into another session. A missing required execution reference, an intermediate open position or an execution clock that overlaps the next session is incomplete, not success. A failed coordinator cannot be retried/advanced with its partially executed account. Calendar coverage remains the declared NYSE 2024 fixture calendar.

Each symbol specifies `strategy` (`orb_close_cross_v1` or `vwap_touch_hold_v1`), optional `strategy_config` and `session_open`. Resolved strategy defaults are retained. Scanner scores are explicitly supplied synthetic `{rvol, gain}` observations; missing values exclude the symbol. Scanner exit does not disable position protection.

- Bar symbols provide the existing dated one-minute `bars` schema: `bar_start`, `bar_end`, `available_at`, OHLC and volume. VWAP begins at the session open.
- Trade symbols provide `trades`, with required `trade_id`, dated `event_time` and `available_at`, positive `price`/`size`, and boolean `quality_ok`. Optional `conditions` and `tape` are preserved. Only declared fixture condition codes are accepted. Distinct equal-market-time prints remain explicitly unsupported by this millisecond account adapter; a real-provider full-key adapter is later work.
- Optional trade `corrections` specify `original_trade_id`, dated `available_at`, `action` (`cancel` or `correct`), and replacement price/size for `correct`. Replacements affect future observations; they cannot rewrite committed fills or masquerade as fresh execution opportunities.

## Risk semantics

The example policies use the recorded D10 values: **3% current equity per trade, 5% daily loss, 20% account drawdown**. These values are explicit in the examples and never silently injected into a config.

Required supported convention labels are `daily_loss_basis: session_start_equity` and `halt_action: cancel_entries_keep_exits`. They make the previously underspecified operational semantics reviewable:

1. Equity includes published marks, realized P&L, committed fees and modeled execution costs. No future highs/lows are used as account peaks.
2. Sizing uses the current marked equity before acceptance and again before entry. Estimated stop loss includes adverse entry/stop-exit costs plus both fixed fees. Fill-time sizing can shrink but never increase the accepted quantity. Every entry retains its budget and estimated stop loss.
3. A daily loss reaching or exceeding 5% of that session's starting equity latches the entry halt through the rest of the session, even after recovery. It resets at the next session.
4. A decline reaching or exceeding 20% of the account's highest observed equity latches the account entry halt for the remainder of the attempt, including later sessions. Peak equity carries across sessions. A new CLI attempt is a new independent research account, not an implicit continuation of another run's peak.
5. Halts cancel pending entries and release their owned reservations; later proposals are rejected with `DAILY_LOSS_HALT` or `ACCOUNT_DRAWDOWN_HALT`. Existing stop/target/flatten/max-hold exits continue. A halt does not fabricate a liquidation fill, guarantee a loss ceiling across gaps or authorize a broker action.

## Costs and delays

The fixtures use D11's provisional **0 fixed commission, 5 combined adverse bps per fill, 250 ms feed delay and 0 ms decision delay**. The bps are charged once on entry and once on exit, not separately as another spread and another slippage charge. Buy price is `reference * (1 + bps/10000)`; sell price is `reference * (1 - bps/10000)`. This synthetic cost estimate also applies to target exits; it is not a broker limit-order guarantee. Fees are separate, committed exactly once. References, modeled prices, fees and execution-cost amounts are retained.

The feed delay is an additional modeled delay on declared source availability, never added to source market time. Decision latency controls when an accepted proposal is ready. Both appear separately in resolved configuration and comparisons. A delay that moves input outside its declared date is rejected.

Bar publication and modeled-open observations receive the feed delay. Their supplied scanner rows shift with the corresponding publication. A delayed decision cannot retroactively fill that minute's already-occurred open; it waits for a later eligible open. Trade-built bars close on the replay clock using only prints already available; trade scanner rows name those actual decision boundaries. A trade fill must pass both market-time readiness and availability gates. Timer exits similarly require a post-request market print, even when older prints arrive late.

## Retained evidence and reports

Every invocation receives a new run ID and authoritative SQLite lifecycle. Successful, failed and handled-interrupted version-2 attempts retain:

- `input.json`: complete parsed input with recognized credential fields redacted; the run record separately stores the original input-byte SHA-256.
- `resolved_config.json`: full settings and resolved strategy defaults.
- `trace.jsonl`: session-tagged chronological decisions, acceptance, cancellations, fills, account snapshots and risk states.
- `fills.json`, `metrics.json`, and `sessions/YYYY-MM-DD.json` for each planned session, including failed and not-started states.
- `manifest.json`: configuration/data/code/dependency identities, execution/scanner/account bases, completion status, summaries and artifact hashes.
- `report.html` when `--report` is requested, including failure details, session dates, policy settings and partial-result labels. Failure output identifies the retained run and manifest.

Two sessions with the same clock-time decision remain separate report rows. Failed attempts retain earlier completed sessions and partial current-session fills; later sessions are marked not started. Their diagnostics cannot be compared as successful results. Raw unreadable/non-JSON files follow the existing failed-attempt retention path; unparseable or secret-bearing bytes are not copied verbatim into artifacts.

Comparison verifies the sealed manifest, trace and every listed artifact, including session summaries, input, resolved config and reports. It refuses unexplained changes to dates, source inputs, risk, costs, delays, strategies, completion policy or code/dependencies. One declared comparison axis does not excuse a second changed assumption.

Drawdown includes starting capital and remains session-end-only as a descriptive statistic; the risk monitor checks intraday marks separately. Exposure unions account-availability intervals clipped to scheduled hours. An allowed final open snapshot extends exposure through scheduled close without claiming an actual exit or complete market coverage. Failed-run multi-session statistics are labeled as covering completed sessions only.

## Tests and next work

`tests/test_public_sessions.py` contains 46 cases covering subprocess runs, both strategies/modes, exact two-session balances, fees/bps, fill budgets, source and decision delays, future-session invariance, daily/account halt latches, cancellation before entry, continuing protection, zero trades, failure/interruption retention, validation, redaction, HTML escaping and artifact tampering. The full suite passes **763 tests**, Ruff is clean, and 25 public-command invocations pass their expected exits and repeatability checks: fixture/research validation, four replay modes twice, F01–F17 twice, legacy ORB/VWAP runs twice with comparisons, both new multi-session modes twice with comparisons, and the deliberately blocked final-evaluation command. Failed/handled-interrupted construction and execution attempts are tested for retained evidence and truthful lifecycle status.

```bash
uv run pytest tests/ -q
uv run ruff check .
```

Still outside these two priorities: Alpaca ingestion, real minute-addressable/as-of scanner history, verified feed/universe/corporate-action coverage, real account/settlement semantics, full equal-timestamp execution keys, loader-level holdout enforcement, frozen promotion protocol, optimizer and paper/live trading.

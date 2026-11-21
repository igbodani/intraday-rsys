# Replay historical trades and watch the scanner

Use this workflow to answer “Would this strategy have entered before the minute closed?” An existing Alpaca minute-bar download cannot tell you the order of trades inside a minute. Download the trade tape for the chosen feed once. The backtest and every experiment arm read the same local copy.

## Run from the repository root in PowerShell

1. Copy the settings. The example covers AAPL and MSFT, two prior sessions and one development session. Edit the local copy for your universe, execution dates, feed, and research partitions. For a date range, use the multi-year example below. The cache path is relative to the settings file.

   ```powershell
   Copy-Item configs/intrabar-trades.example.json configs/intrabar-trades.local.json
   ```

2. Download the individual trades. This is the only command that needs Alpaca data credentials. The command fetches every result page for each symbol and required session. Copy the full `pins.json` path it prints. These pins identify the exact tape contents.

   ```powershell
   uv run --env-file .env rsys trade-download --config configs/intrabar-trades.local.json --out runs/tape
   $pins = Read-Host 'Paste the pins.json path printed above'
   ```

3. Review splits, ticker changes and other corporate actions for each symbol over the full execution and lookback period. Enter your source and date coverage in `action_review`. Use `verified_none` only for a period you verified is free of relevant actions. For reviewed splits, select `verified_splits_only` and list the effective dates and ratios as described below. Other events that affect price or symbol continuity require a separately reviewed window.

4. Run one offline replay. Add `--watch` to print trades, forming bars, scanner standings, signals, fills and minute closes as they happen. For a large tape, omitting `--watch` avoids printing every trade to the terminal. The `evidence_detail` setting controls what is saved.

   ```powershell
   uv run rsys trade-backtest --config configs/intrabar-trades.local.json --pins "$pins" --out runs/intrabar
   ```

5. Compare ORB and VWAP with the existing experiment runner. Copy the example plan, set `trade_pins` to the path from step 2 (absolute, or relative to the plan file), and keep `base_config` pointing to your local replay settings. In JSON, use forward slashes in Windows paths. Choose a study ID for the research question before running.

   ```powershell
   Copy-Item configs/experiment.intrabar.example.json configs/experiment.intrabar.local.json
   uv run rsys experiment --plan configs/experiment.intrabar.local.json --out runs/intrabar-studies
   ```

   The example spends two trials, one per strategy. The experiment freezes the pin map, verifies each child's settings and artifacts, and counts failed attempts and reruns against the budget. Open the printed report to compare net change, trade count, exposure and session-end drawdown. Promotion remains unscored.

## Test a fixed list over five to seven years

Copy the multi-year example in place of the single-day example in step 1. Use the same download, replay and experiment commands above.

```powershell
Copy-Item configs/intrabar-multiyear.example.json configs/intrabar-trades.local.json
```

| Setting | What to choose |
| --- | --- |
| `symbols` | Your fixed stock list. Save the list before examining results. A list chosen today measures how that particular list would have performed. It can omit companies that later disappeared. |
| `date_range` | Inclusive start and end dates. The example covers January 2019 through December 2025. The trade calendar covers 2015–2026, skips closures and includes early closes. The first session needs enough earlier trading days inside calendar coverage. |
| `scanner.lookback_sessions`, `scanner.top_n` | How many prior sessions to compare against and how many leaders can propose entries. The downloader includes the necessary warmup. Account and scanner history carry across the year boundary. |
| `partitions`, `purpose` | Define the development and unseen periods before tuning. The example marks its entire span as development to demonstrate a continuous seven-year run. Reserve your actual final dates separately and keep them out of development runs, including their warmup access. |
| `session_mode` | Choose `regular`, `premarket_regular`, `regular_afterhours` or `extended` (both premarket and after-hours). Each download includes the clock window for its mode. Download matching pins when the data window changes. All times below use New York local time. |
| `strategy_anchor` | In an extended mode, `eligible_open` starts the opening range and strategy warmup when simulated orders first become eligible. `regular_open` starts them at 9:30 a.m. The scanner still processes earlier prints. |
| `print_policy`, `limit_offset_bps` | For extended modes, use `alpaca_day_limit_v1` and choose a price tolerance. The example uses 10 basis points (0.1%). Eligible whole-share buys and sells require a later print within the limit after modeled costs. A stop or timer can trigger a sell that never fills. For regular-only, use `alpaca_minute_conditions_regular_fills_v1` and remove `strategy_anchor` and `limit_offset_bps`. |
| `account.flatten_minutes_before_close` | Minutes before that day's eligible close to stop new entries and request exits. This follows the selected mode and shortened days. Choose this or a fixed `flatten_at` time. |
| `action_review` | Add an entry per ticker covering warmup through the final included date. The example ships with review pending. A split entry such as `{"date":"2024-06-10","ratio":"10","known_before_open":true}` means ten new shares per old share. Use actual verified dates, ratios and sources for your symbols. The scanner converts earlier reference prices and volume from the effective session onward. |
| `evidence_detail` | Choose `signals` for smaller files that retain proposals, decisions about those proposals, fills and timers. Choose `full` for every scanner ranking, trade batch and minute close. Both replay every cached trade. |

**Historical trading windows.** The extended-hours model uses regular-only fills before July 12, 2019, then 9 a.m.–6 p.m. on full days. From February 24, 2022 it uses 4 a.m.–8 p.m. on full days and 4 a.m.–5 p.m. on early-close days. For earlier half days it conservatively ends at the regular close. The July cutoff uses the first full day after Alpaca announced extended hours. Overnight trading falls outside all four modes. [2019 Alpaca announcement](https://forum.alpaca.markets/t/commission-free-short-selling-margin-trading/16) · [2022 Alpaca Trading API rollout](https://alpaca.markets/learn/how-to-enable-stock-market-trading-from-4-am-to-8-pm-et)

**How to read a seven-year run.** Open `result.json` and look at `calendar_year_results` for trades, starting and ending equity, net return after configured costs and drawdown measured at session closes. The first and last session dates indicate partial years. Each session also records a print count per symbol so you can spot empty recordings. Year-end equity carries into the following year, including any account drawdown halt. An experiment saves this breakdown in each strategy's scorecard. Start with a short period to measure storage and replay speed on your actual tickers. A full-span synthetic run checks calendar and software continuity. Profitability requires separate evidence on market data and dates held back from tuning.

## What happens during a replay

| When | What the simulator does |
| --- | --- |
| Before the open | Reads the pinned tape for each symbol and prior trading session. Uses the last eligible regular-session price as the prior-close reference. Fetches enough comparable sessions to account for early closes. |
| At each distinct trade timestamp | Delivers the prints after the configured feed delay. Applies prints sharing the same nanosecond together across the universe before ranking. A deterministic venue/ID order resolves ties within a symbol. |
| As the minute forms | Updates open, high, low, last price and volume using the versioned condition profile. For example, an odd lot may add volume without changing that minute's prices. |
| At each update | Ranks eligible, fresh symbols by relative volume, then gain, then symbol. Relative volume divides current cumulative volume by mean historical volume at the same elapsed instant. Opening gap stays fixed after the first eligible regular-session price. Gain follows the latest eligible price against the same previous close. |
| When a strategy signals | Checks sizing, available cash, daily loss and drawdown. An accepted signal can fill on a later eligible trade of that symbol, including within the same open minute. The account adapter uses millisecond times and skips trades in the decision millisecond. Decision latency and modeled feed delay also apply. |
| While holding a position | Checks exits even if another symbol takes the scanner lead. Stop, target, maximum hold and flatten rules use eligible subsequent prints. A session that cannot finish flat retains a failed attempt. |
| At the minute boundary | Publishes the completed bars before processing prints stamped in the new minute. Commits strategy history once. A quiet or volume-only minute emits no invented price bar. |

## Strategy settings

- `orb_forming_cross_v1` builds the opening range from completed minutes. After that range, the latest qualifying price can cross the configured breakout level mid-minute. `range_minutes` and `breakout_buffer_ticks` control the signal.
- `vwap_forming_confirm_v1` uses the existing strategy's bar-based VWAP approximation. Warmup, rising slope, arming and a pullback use committed history. The forming minute can confirm the entry. Repeated updates within one minute count once toward history.
- `scanner.top_n` limits the symbols allowed to propose new entries. Set `lookback_sessions`, `max_price_age_ms` and `min_dollar_volume` explicitly. A missing reference price or zero historical denominator makes a symbol ineligible. Cash, costs, risk and execution settings are shared across the experiment arms.
- Results carry the selected label, **IEX-based estimates** or **SIP-based estimates**. Timing is reconstructed from final historical prints plus the configured delay. Exact original receipt times, correction arrival order, order-book queues and available fill liquidity are outside this model. The example fill profile uses regular and intermarket-sweep prints as execution references. D12 and D22 still require a provider audit. [Alpaca aggregation rules](https://docs.alpaca.markets/us/docs/market-data-faq) · [Historical stock trades](https://docs.alpaca.markets/us/reference/stocktrades-1)

## Files to inspect

Under `trade-attempts/<id>/`, open `report.html` for results, `result.json` for metrics and fills, and `events.jsonl.gz` for the timeline. With full evidence, each trade batch retains its timestamps, individual prints, forming bars, scanner inputs and standings, and the signals accepted or rejected. `input.json`, `pins.json` and `manifest.json` preserve the configuration, tape identities and artifact hashes. The physical tapes live under `<cache>/trade-tapes/`. Keep those files with the pins to rerun elsewhere. Data from the configured feed is loaded one session at a time across the selected universe and lookback. A large universe requires more memory, disk and replay time.

Run `uv run pytest tests/test_intrabar_tape.py tests/test_trade_session_modes.py -q` to reproduce the offline acceptance checks, including within-millisecond price reversal, future-data mutation with a negative control, VWAP confirmation, changing ranks, off-list exits, early closes, multi-year continuity, historical extended-hours limits, split handling, cache integrity and the two-arm experiment.

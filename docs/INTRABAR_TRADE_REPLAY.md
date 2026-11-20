# Replay historical trades and watch the scanner

Use this workflow to answer “Would this strategy have entered before the minute closed?” An existing Alpaca minute-bar download cannot tell you the order of trades inside a minute. Download the trade tape for the chosen feed once. The backtest and every experiment arm read the same local copy.

## Run from the repository root in PowerShell

1. Copy the settings. The example covers AAPL and MSFT, two prior sessions and one development session. Edit the local copy for your universe, supported 2024 dates, feed, and research partitions. The cache path is relative to the settings file.

   ```powershell
   Copy-Item configs/intrabar-trades.example.json configs/intrabar-trades.local.json
   ```

2. Download the individual trades. This is the only command that needs Alpaca data credentials. The command fetches every result page for each symbol and required session. Copy the full `pins.json` path it prints. These pins identify the exact tape contents.

   ```powershell
   uv run --env-file .env rsys trade-download --config configs/intrabar-trades.local.json --out runs/tape
   $pins = Read-Host 'Paste the pins.json path printed above'
   ```

3. Review splits, ticker changes and other corporate actions for each symbol over the full execution and lookback period. Enter your source and date coverage in `action_review`. Use `verified_none` only for a period you verified is free of relevant actions. This replay uses raw prices and share quantities. Choose a verified action-free window when an action would require conversion.

4. Run one offline replay. Add `--watch` to print trades, forming bars, scanner standings, signals, fills and minute closes as they happen. For a large tape, omitting `--watch` avoids printing every trade to the terminal. The same events are saved either way.

   ```powershell
   uv run rsys trade-backtest --config configs/intrabar-trades.local.json --pins "$pins" --out runs/intrabar
   ```

5. Compare ORB and VWAP with the existing experiment runner. Copy the example plan, set `trade_pins` to the path from step 2 (absolute, or relative to the plan file), and keep `base_config` pointing to your local replay settings. In JSON, use forward slashes in Windows paths. Choose a study ID for the research question before running.

   ```powershell
   Copy-Item configs/experiment.intrabar.example.json configs/experiment.intrabar.local.json
   uv run rsys experiment --plan configs/experiment.intrabar.local.json --out runs/intrabar-studies
   ```

   The example spends two trials, one per strategy. The experiment freezes the pin map, verifies each child's settings and artifacts, and counts failed attempts and reruns against the budget. Open the printed report to compare net change, trade count, exposure and session-end drawdown. Promotion remains unscored.

## What happens during a replay

| When | What the simulator does |
| --- | --- |
| Before the open | Reads the pinned tape for each symbol and prior trading session. Uses the last eligible regular-session price as the prior-close reference. Fetches enough comparable sessions to account for early closes. |
| At each distinct trade timestamp | Delivers the prints after the configured feed delay. Applies prints sharing the same nanosecond together across the universe before ranking. A deterministic venue/ID order resolves ties within a symbol. |
| As the minute forms | Updates open, high, low, last price and volume using the versioned condition profile. For example, an odd lot may add volume without changing that minute's prices. |
| At each update | Ranks eligible, fresh symbols by relative volume, then gain, then symbol. Relative volume divides current cumulative volume by mean historical volume at the same elapsed instant. Opening gap stays fixed after the first eligible price. Gain follows the latest eligible price against the same previous close. |
| When a strategy signals | Checks sizing, available cash, daily loss and drawdown. An accepted signal can fill on a later eligible trade of that symbol, including within the same open minute. The account adapter uses millisecond times and skips trades in the decision millisecond. Decision latency and modeled feed delay also apply. |
| While holding a position | Checks exits even if another symbol takes the scanner lead. Stop, target, maximum hold and flatten rules use eligible subsequent prints. A session that cannot finish flat retains a failed attempt. |
| At the minute boundary | Publishes the completed bars before processing prints stamped in the new minute. Commits strategy history once. A quiet or volume-only minute emits no invented price bar. |

## Strategy settings

- `orb_forming_cross_v1` builds the opening range from completed minutes. After that range, the latest qualifying price can cross the configured breakout level mid-minute. `range_minutes` and `breakout_buffer_ticks` control the signal.
- `vwap_forming_confirm_v1` uses the existing strategy's bar-based VWAP approximation. Warmup, rising slope, arming and a pullback use committed history. The forming minute can confirm the entry. Repeated updates within one minute count once toward history.
- `scanner.top_n` limits the symbols allowed to propose new entries. Set `lookback_sessions`, `max_price_age_ms` and `min_dollar_volume` explicitly. A missing reference price or zero historical denominator makes a symbol ineligible. Cash, costs, risk and execution settings are shared across the experiment arms.
- Results carry the selected label, **IEX-based estimates** or **SIP-based estimates**. Timing is reconstructed from final historical prints plus the configured delay. Exact original receipt times, correction arrival order, order-book queues and available fill liquidity are outside this model. The example fill profile uses regular and intermarket-sweep prints as execution references. D12 and D22 still require a provider audit. [Alpaca aggregation rules](https://docs.alpaca.markets/us/docs/market-data-faq) · [Historical stock trades](https://docs.alpaca.markets/us/reference/stocktrades-1)

## Files to inspect

Under `trade-attempts/<id>/`, open `report.html` for results, `result.json` for metrics and fills, and `events.jsonl.gz` for the timeline. Each trade batch retains its timestamps, individual prints, forming bars, scanner inputs and standings, and the signals accepted or rejected. `input.json`, `pins.json` and `manifest.json` preserve the configuration, tape identities and artifact hashes. The physical tapes live under `<cache>/trade-tapes/`. Keep those files with the pins to rerun elsewhere. Data from the configured feed is loaded one session at a time across the selected universe and lookback. A large universe requires more memory, disk and replay time.

Run `uv run pytest tests/test_intrabar_tape.py -q` to reproduce the offline acceptance checks, including within-millisecond price reversal, future-data mutation with a negative control, VWAP confirmation, changing ranks, off-list exits, early closes, cache integrity and the two-arm experiment.

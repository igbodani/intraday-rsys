# Missing candles and feed-specific estimates

Implemented policy: `alpaca_sparse_minutes_v1`. Owner direction, 2026-11-07:
continue simulation across missing candles and label IEX results as estimates.
This changes missing-data handling, not the recorded risk, cost or promotion rules.

## Use it

1. In your download JSON, add `"gap_evidence": "trades"` at the top level.
2. Run your download again. Existing verified bars are reused; only missing-minute
   trade evidence is fetched. No `--refresh` is required. Keep the new manifest.
3. Put the **new object IDs** from that manifest into your backtest JSON's `objects`
   mapping. Include every required execution and warmup date, as before.
4. Add `"missing_bar_policy": "continue"` at the top level of the backtest JSON.
5. Run the local check, then the backtest or experiment plan.

```powershell
uv run --env-file .env rsys download --config configs/alpaca_download.local.json --out runs/acquisition
uv run rsys check-data --config configs/alpaca_backtest.local.json --out runs/preflight
uv run rsys backtest --config configs/alpaca_backtest.local.json --out runs/backtests --report
```

Only download needs credentials. Replay, preflight and comparisons read pinned local
objects. Existing version-1 cache objects and older strict configurations still work.
Without `gap_evidence`, missing bars remain unknown; continuation is possible but its
results are inconclusive. `missing_bar_policy` defaults to `strict` for old configs.
The checked-in backtest example enables `continue`; it still requires your explicit
scanner settings, date permissions, object pins and corporate-action review.
Do not copy a synthetic test's action attestation into a market experiment.

`validate-alpaca` remains the separate, explicitly enabled provider audit. Its
bar-only acquisition does not request the new supplemental trade evidence; use
`download` for this workflow. Unknown gaps cannot pass its coverage checklist.

## Meaning of a missing candle

| Evidence | Scanner/history | Price strategy and execution |
|---|---|---|
| Real candle | Use its volume and prices | Existing completed-bar signals and bar-based fill estimates |
| Complete historical trade query reports none | `NO_REPORTED_TRADES`: feed-scoped zero, explicitly an estimate | No price candle, no fill; clock advances |
| All trades are supported odd lots | `VOLUME_ONLY`: sum actual reported sizes once | No OHLC, VWAP price contribution or fill is invented |
| No trade evidence, unsupported conditions or regular trades contradicting missing bar | Volume unavailable, never zero; dependent entries stop | Position management continues; account results are diagnostic/inconclusive |

The classifier supports tapes A/B/C and odd-lot condition `I`, optionally combined
with regular-sale `@` and intermarket-sweep `F`. It requires exchange V for IEX.
Other combinations remain unknown. This is a bounded missing-candle classifier,
not a complete condition table or the real-trade execution engine (D22 remains open).
An empty completed historical response is not an independent feed-health audit,
proof of a halt, or proof that nothing traded on other exchanges.

## What continues

- The existing chronological replay engine still owns all events and fills.
- Gap evidence becomes visible at minute end plus the configured feed delay, just
  like the declared final-historical-bar availability model. It is never shown early.
- ORB may form a range from actual prices when all omitted minutes are explained.
  A range with no prices remains unavailable. VWAP's existing bar-price proxy uses
  actual candles only; volume-only observations do not enter its price denominator.
  Warmup, slope and pullback bar counts count actual candles, not made-up flat bars.
- Scanner cumulative volume adds explained missing-minute volume once. Previous-day
  close is the last actual price-eligible close. Missing selected history cannot be
  silently replaced by older dates or assigned zero volume.
- Pending intent expiry, max-hold timers, flatten deadlines and risk halts keep
  running even without a new candle. Unknown gaps cancel pending entries; existing
  positions keep their protection. An entry still requires its signal, scanner,
  cash and risk checks; continuation does not force a trade.
- A timer requests an exit immediately, but the bar model can fill only at a real
  later eligible open. A price delivered late from before the request cannot fill it.
  A next observed open below a stop uses that open, not an imagined stop-price fill.
- A completely absent day with no positions is retained and the next day runs.
  If no later price can resolve a required exit, the existing incomplete-session
  failure retains its evidence; an unresolved account is not carried forward as flat.

Real broker stops can execute between price-eligible candles, including using
information outside IEX. Our bar model cannot reconstruct that exact outcome.
**IEX-based estimates** describes the whole result, even when every omission is
explained. This is historical simulation, not Alpaca order routing or paper trading.

## Evidence and comparisons

The downloader stores raw paginated gap responses in a new immutable version-2
cache object, alongside unchanged raw bar pages. The original version-1 pins remain
readable. Each load verifies the content hash, exact feed/date/symbol requests,
complete pagination, trade identities, amounts, classification and bar coverage.
Unknown conditions are retained for review. Malformed or contradictory identities
fail acquisition rather than being repaired. Refresh creates new pins; simulations
never make provider requests.

Reports, CLI output, metrics JSON, comparison JSON and experiment scorecards carry
the feed estimate label. `quality.coverage_complete` still means a candle exists for
every scheduled minute; it is not rewritten to hide gaps. Preflight separately shows
`explained_gaps`, individual classifications and whether replay can execute.
The trace records each execution-day `missing_minute`; scanner snapshot references
retain the exact supplemental volume observations used at each decision.

Any unknown gap in the requested execution/warmup data conservatively labels the
whole account result `inconclusive_missing_data`, even if no position crossed that
gap. The manifest's metrics scope is `diagnostic_estimate_only`. Numbers are retained
for debugging, not scored as strategy evidence. This also avoids treating later
sessions funded by an uncertain account path as independent evidence. `rsys compare`
and experiment scorecards reject those results; reports can still be opened with
full integrity checks. Trial budgets still count these execution attempts.

## Tests

`tests/test_sparse_minutes.py` covers:

- Independently summed odd-lot volume, verified empty responses versus absent
  evidence, unsupported conditions, wrong venues, duplicates and pagination.
- Real public acquisition → cache reuse → preflight → multi-session replay, with
  the network denied during replay, a genuine entry/exit, and IEX report labels.
- Strict-mode compatibility, old-pin preservation, reclassification/tamper checks,
  and holdout denial before either bar or trade transport.
- Timers firing without bars, actual-price stop exits, no fabricated VWAP values,
  an opening gap and a completely missing session.
- Mutation of future gap evidence leaves earlier decisions/fills/account trace
  identical; changing already-visible volume changes the scanner (negative control).
- Subprocess backtest, diagnostic report regeneration, and comparison rejection
  for inconclusive evidence.

The full existing suite remains required. These fixtures prove software behavior;
they do not verify a live download's completeness or strategy profitability.

## Provider references

Reviewed 2026-11-07:

- [Alpaca market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq):
  single-venue IEX, minute aggregation, odd-lot field eligibility, stricter combined
  conditions, separate VWAP eligibility, and omitted candles.
- [Historical trades API](https://docs.alpaca.markets/us/reference/stocktrades-1):
  explicit feed, inclusive endpoint bounds, pagination and symbol mapping.
- [Orders at Alpaca](https://docs.alpaca.markets/us/docs/orders-at-alpaca):
  broker stop-election rules use evidence beyond a single-venue minute-bar model.

## Verification and handoff — 2026-11-07

Base: remote `docs/copilot-backtest-readiness` at
`f6a58f81bd048ca9cab29d3597d2f6a2f7a5ea7b` (the owner's latest download-config
change was preserved before implementation). This patch changes the existing
acquisition/cache/scanner/replay/report path; there is no new simulator.

Tested source fingerprint:
`b0b8ac3bddbf06871b3836b99edb6cb82974366d281713667133df3faafed3e3`.

Executed on Linux/Python 3.12, with a Python socket/network tripwire and synthetic
credentials during the full test run:

- `uv run --offline --frozen pytest tests/ -q`: **1,312 passed, 1 deselected**,
  393.77 seconds. The deselected test requires explicit real-provider enablement.
- Focused sparse, Alpaca pipeline, minute history and data-preflight tests:
  **116 passed**, including all **23** new parameterized sparse-data cases.
- `uv run --offline --frozen ruff check .`: clean.
- `uv run --offline --frozen python scripts/check_copilot_config.py`: valid.
- `git diff --check`: clean.
- Independent adversarial review reproduced and verified the opening-gap,
  completely empty session, diagnostic-report access and wrong-venue fixes.
  All 23 sparse tests passed independently; no blocking findings remained.

The test assertions include hand-calculated volume (7+11=18 with a repeated row
counted once), a 10-share timer exit at 103 after entry at 102 (profit 10), a later
stop-gap open at 90 (loss 120), and VWAP proxy (1000+1020)/20=101 without a fake
candle. They also prove that unavailable future gap evidence cannot alter a
prefix containing an actual fill, while changed visible volume changes the result.
Known and unknown two-arm experiments both consume two trial slots; only the
explained-data arms receive scorecards.

Not run here: Windows execution or a real-provider download of the new gap
requests. The next operational check is to rerun the owner's local download with
`gap_evidence: trades`, inspect its classifications, pin those new objects and run
preflight/backtest with `missing_bar_policy: continue`. Historical IEX estimates
still do not establish full-market execution fidelity, live/paper readiness or
profitability. D22 and the remaining research promotion decisions stay open.

# Implementation contracts

These concrete semantics are proposed implementation requirements. They define test behavior; unresolved market settings still block research runs.

## Data and event clock

- Store UTC `bar_start`, `bar_end`, `available_at`, instrument ID, source/feed and OHLCV. Bars are start-labeled half-open intervals `[bar_start, bar_end)`. Exchange sessions use `America/New_York`, including daylight-saving changes and early closes.
- A finalized historical bar becomes visible at `bar_end + configured_feed_delay`. Finalized bars cannot recreate unrecorded live arrival/revision history; label this approximation.
- Emit separate open events. The execution adapter may use that open; strategies cannot see the interval's final high, low, close or volume until availability.
- Sort by `(available_at, phase, stable_sequence)`. At equal times, publish all eligible symbols' completed observations before ranking. Require a stable instrument-ID tie rule.

| Phase | Name | Behavior |
|---|---|---|
| 10 | INTERVAL_SETTLE | Settle protective orders eligible during a completed interval; update ledger before new decisions. Keep bar OHLCV private to execution until publication. |
| 20 | BAR_PUBLISH | Expose completed observations and mark equity in that interval's price units. |
| 25 | CORPORATE_ACTION | Apply known splits effective at this boundary after the old interval settles; convert positions, retained marks and historical feature units before new-unit orders. |
| 26 | CASH_RELEASE | Apply explicitly available settlement/cash-flow events; update spendable cash before acceptance. |
| 30 | TIMER | Session/reset/entry-cutoff/flatten timers at their scheduled time; exit requests have priority. |
| 40 | SCAN_SIGNAL | Update causal features in the current share units, refresh candidate ranking, then evaluate strategies against the same snapshot. |
| 50 | ACCEPT | Sort proposals; apply risk and cash reservations; record acceptance/rejection. |
| 60 | OPEN_EXIT | On a new bar open, execute eligible time exits and gap-triggered protection before entries. |
| 70 | OPEN_ENTRY | Execute previously eligible entry intents, rechecking price, quantity, expiry and cash. |
| 80 | RECORD | Write ordered transitions and account checks. |

`ready_at = decision_available_at + decision_latency_ms`. An entry may use an open at or after `ready_at` only if that open's event key follows the decision. With zero delay, the previous bar close and following open can share a clock time but occur in separate phases. This is an idealized assumption to declare and stress. A positive delay past that boundary moves entry to the next available open. No order uses the close that created its signal as its fill price. Reject an entry at fill time if the actual modeled open is at/below its stop even when the earlier signal price had valid risk (F11).

Missing required symbols/observations at a decision boundary produce declared eligibility exclusions. Do not wait indefinitely or silently carry stale values. Set explicit maximum age. Historical latency settings must remain consistent with features, reservations and execution; late information never changes an earlier decision.

## Position and exit behavior

- Open positions receive every relevant event independently of the scanner. One active position per instrument in v1; compare strategies in separate runs before combinations.
- Stops/targets become active after entry. A newly opened position can hit either later in its entry bar. If OHLC cannot order both touches, stop wins. Reject a new long entry when its modeled entry is at or below the intended protective stop.
- A long stop gapped through sells at the observed open minus configured adverse execution costs, not at the old stop. A favorable target gap uses the target price conservatively in the simple adapter.
- Protective orders already active govern the completed bar. Changes requested at its close apply only to later event keys. Cancel the other protective order when one closes the position.
- Interval fills without tick timestamps are tagged `time_precision: bar_interval`; their ledger availability is the bar's availability time. Do not fabricate intrabar ordering or reuse proceeds earlier than that.
- Exit ties: scheduled flatten/time exit first at an open, then protective gap exits; each position closes once. Intrabar stop takes priority over target. New entries are last.
- The flatten timer is `session_close - flatten_buffer_minutes`; it submits an exit eligible at the first open at/after that timer. Require a buffer of at least one base bar and an available execution event before the close. Missing exit data marks the run incomplete; do not invent a closing fill.

## Scanner baseline (D14, proposed)

1. Declare eligible instruments using prior completed-session dollar volume and a coverage manifest. The minimum dollar-volume value remains unset until the data audit.
2. At each completed minute, cumulative RVOL is today's cumulative regular-session volume through that minute divided by the arithmetic mean cumulative volume through the same elapsed minute in the prior `lookback_sessions` calendar-comparable sessions. Use `data_rules.md` to select sessions and convert historical volumes into current split units.
3. Exclude a symbol if selected history is missing or the denominator is zero; do not substitute average full-day volume or skip a missing-data session to find a better replacement. Session gain is latest completed raw close / previous regular-session close converted to current split units - 1. Cash-dividend price effects remain in price gain and are flagged.
4. Rank descending by RVOL, then descending by session gain, then ascending stable symbol/instrument identifier. Take `top_n`. No opaque weighted score.
5. Refresh every completed base bar. Persist compact decision evidence under the retention contract below. A fixed-universe control bypasses ranking but uses the same availability/cash/cost rules.

## Acceptance and pending-order order

At one decision boundary, all proposals use the same candidate snapshot. Sort ascending by `(decision_available_at, scanner_rank, instrument_id, strategy_id, proposal_id)`. Rank 1 has priority. Scanner rank already reflects descending RVOL, then descending session gain, then stable instrument identity. A missing rank rejects the baseline proposal with `NO_CURRENT_RANK`; do not invent rank zero.

`proposal_id` is deterministic from strategy/version, instrument, setup identity, decision event key and a strategy-local deterministic sequence. Deduplicate the same proposal ID. The first accepted proposal for an instrument reserves its cash; another pending/held position in that instrument rejects subsequent proposals. Multi-strategy combination experiments remain later work.

Record acceptance sequence and the snapshot's rank. Revalidate fills in `(ready_at, acceptance_sequence)` order; do not re-rank accepted intents using later information. Apply available exits before these fills. F05 injects the declared RVOL/gain values, so the canonical snapshot ranks ABC ahead of XYZ even if the JSON input is reversed.

## Ranking evidence retention

Proposed default `ranking_storage: compact` records each decision's top-N rows, eligible/excluded counts by reason, universe/data/feature/config hashes, and a SHA-256 digest of the full ordered ranking. Persist exclusion state changes and all proposal/acceptance/rejection records with their feature snapshots. Position-management evidence is retained independently.

Hash canonical UTF-8 JSON with sorted keys and no insignificant whitespace. Decimal values are fixed-point strings with trailing fractional zeros removed and zero normalized to "0". Finite float features use their Python `float.hex()` strings, with negative zero normalized to positive zero; nonfinite/missing values use null plus a reason field. Timestamps use UTC ISO-8601 with explicit precision. Include encoding version `ranking-json-v1` and the feature registry version. This pins the stored representation; it does not change a feature's calculation or the strategy's comparison rules. Pin inputs and code so full rankings can be reconstructed and checked against the digest; a hash alone is not adequate evidence without those inputs.

Optional `ranking_storage: proposals` stores full rankings at decision boundaries that emit a proposal; `full` stores every ranking for bounded diagnostic runs. Record bytes, record counts, wall time and projected storage on a declared sample before choosing a large-run retention policy. Never silently drop required evidence or prune it while an experiment is active. Full-ranking capture is not a default prerequisite for every research run.

## Sizing and ledger

Use Decimal for prices, quantities and money; use instrument tick and quantity steps from the profile. Floor long protective stop levels to a tick and round long profit targets up to a tick. Reject nonpositive risk distance. Round quantity down to a permitted increment, never up.

For risk sizing, choose the largest permitted quantity `q` that satisfies all three constraints:

1. `q * (modeled_entry - planned_stop) + estimated_entry_and_stop_exit_costs(q) <= risk_fraction * equity_at_decision`.
2. Entry notional plus entry costs fits currently spendable cash after other reservations.
3. Entry notional fits the declared position/exposure cap.

Fixed fees and minimum quantities can make sizing nonlinear: reduce quantity by valid steps or use a bounded integer-step search. If no positive quantity fits, reject with a reason. Recheck at the actual entry event; reduce or reject rather than silently overspend. Reserve cash immediately when accepting proposals. Releasing a canceled reservation does not release unsettled proceeds.

Keep cash, spendable cash, unsettled proceeds, reservations, marked holdings and realized P&L separately. For the v1 unlevered long account, `equity = total_cash + marked_holdings`. Fees reduce cash once. Stops express intended risk, not a guaranteed maximum loss through gaps.

## Parameter registry

Each parameter records type, units, default or `unset`, valid range/set, dependencies, decision ID, status and rationale. Proposed starting values live in the draft config; unresolved required settings have no fallback. Freeze resolved settings and hashes into every run.

| Parameter | Proposed value | Valid range / constraint |
|---|---:|---|
| base_interval_minutes | 1 | 1 in v1; extensions require parity tests |
| scanner.top_n | 20 | Integer 1–500; cannot exceed eligible symbols |
| scanner.lookback_sessions | 20 | Integer 2–252; require full selected history under `data_rules.md` |
| scanner.max_lookback_calendar_sessions | 252 | Integer >= lookback_sessions; fewer comparable sessions means unavailable |
| reporting.ranking_storage | compact | compact, proposals or full; full requires a bounded diagnostic run |
| scanner.min_prior_dollar_volume | unset | Positive USD threshold; D12–D14 |
| orb.range_minutes | 15 | 1–60; integer multiple of base interval |
| breakout_buffer_ticks | 1 | Integer 0–10; applied using instrument tick |
| vwap.warmup_bars | 20 | Integer 6–120; greater than slope lookback |
| vwap.slope_lookback_bars | 5 | Integer 1–30; completed bars only |
| vwap.pullback_max_bars | 5 | Integer 1–30; count includes first touch and confirmation |
| target_r | 2 | Positive finite value; baseline search candidate 1–4 |
| maximum_hold_minutes | 60 | Positive multiple of base interval; flatten may occur earlier |
| entry_cutoff_local | 11:00 | Exchange-local; must precede flatten time |
| flatten_buffer_minutes | 5 | At least one base bar; less than session duration |
| max_fills_per_symbol_day | 1 | Positive integer; v1 proposed baseline 1 |
| costs, delays, risk limits | unset | Nonnegative costs/delays; positive risk/exposure limits; require D09–D11 |
| dates, trial budget, gates | unset | Chronological nonoverlap; positive bounded budget; require D16–D17 |

Ranges are validation domains, not permission to sweep every combination. Search requires an explicit subset and trial budget. Identical fingerprints do not justify silently overwriting previous attempts.

## CLI target

| Command | Contract |
|---|---|
| `rsys validate --config PATH` | Validate types, dependencies, provenance and unresolved decisions; list all blockers |
| `rsys fixture --case ID_OR_ALL --out DIR_OR_AUTO` | Execute account/execution cases from `fixtures/cases.json`; network disabled; compare expected checkpoints. `all` means that account suite; strategy and data fixtures belong to the separate test suites in the tickets. `--out auto` creates a unique empty directory through `tempfile.mkdtemp(prefix="rsys-fixture-")` and prints its absolute path. |
| `rsys run --config PATH --out DIR` | Run one validated development experiment; reject final-holdout access |
| `rsys compare --runs ID1 ID2 --out DIR` | Reject incompatible dates/capital/costs unless explicitly reporting descriptive differences |
| `rsys final-evaluate --manifest PATH --out DIR` | Verify frozen inputs and holdout authorization recorded in the manifest; log access |

Exit codes: 0 success; 2 invalid/unresolved configuration; 3 data/coverage error; 4 invariant or fixture failure; 5 blocked holdout access; 1 other execution failure. Emit a short human error and structured `error.json` in the run directory when one exists. Do not create a success record for a partial failure. Explicit output paths must be new or empty; no overwrite flag in v1. Fixture `--out auto` is a reserved convenience value: every invocation receives a different temporary directory, which is kept for inspection. It must not reuse or delete an earlier run. Tests use the test framework's temporary-directory fixture.

Minimum run record: ID, hypothesis, phase, status, parent trial, resolved config, config/data/code hashes, dependency lock, seed, account/cost profile, UTC start/end, artifacts, exclusions, metrics with missingness, and decision/gate status. The sample under `examples/` is illustrative.

Status lifecycle (M0-02): `pending -> running -> {success, failed, interrupted, crashed}`. Resolved inputs (hypothesis, resolved config, config/data/code/dependency-lock hashes, seed, account/cost profile, start time) are frozen once a run is created; only status/results fields may change afterward, and only along this lifecycle -- a finalized attempt never resurrects in place, and identical resolved configs always get separate run IDs. `interrupted` records a handled stop (e.g. a caught signal) with a true end time; `crashed` is the recovery status applied to a run found still `running` with no process behind it -- it never claims the dead process cleaned up after itself.

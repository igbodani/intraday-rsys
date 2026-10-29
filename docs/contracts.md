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
| `rsys replay --config PATH --out auto\|DIR` *(implemented, R3)* | Offline synthetic replay with a retained SQLite attempt, deterministic JSONL event/evaluation trace and provenance manifest. `--out auto` uses `<config parent>/../runs/replay/<config stem>`. Exit 0 success, 2 invalid config, 3 data/coverage failure, 4 invariant failure, 1 other execution failure. |

Exit codes: 0 success; 2 invalid/unresolved configuration; 3 data/coverage error; 4 invariant or fixture failure; 5 blocked holdout access; 1 other execution failure. Emit a short human error and structured `error.json` in the run directory when one exists. Do not create a success record for a partial failure. Fixture/experiment output paths must be new or empty; no overwrite flag in v1. Replay output paths are evidence roots: they may already exist, but each invocation must create a fresh `attempts/<run_id>` directory and append a distinct SQLite attempt without overwriting previous attempts. Fixture `--out auto` is a reserved convenience value: every invocation receives a different temporary directory, which is kept for inspection. It must not reuse or delete an earlier run. Tests use the test framework's temporary-directory fixture.

Minimum run record: ID, hypothesis, phase, status, parent trial, resolved config, config/data/code hashes, dependency lock, seed, account/cost profile, UTC start/end, artifacts, exclusions, metrics with missingness, and decision/gate status. The sample under `examples/` is illustrative.

Status lifecycle (M0-02, hardened under H2): `pending -> running -> {success, failed, interrupted, crashed}`. Resolved inputs (hypothesis, resolved config, config/data/code/dependency-lock hashes, seed, account/cost profile, start time) are frozen once a run is created; only status/results fields may change afterward, and only along this lifecycle -- a finalized attempt never resurrects in place, and identical resolved configs always get separate run IDs. `interrupted` records a handled stop (a caught `KeyboardInterrupt` or an explicit `mark_interrupted` call) with a true end time; `crashed` is the recovery status applied to a run found still `running` whose owning process is verified dead -- recovery never marks a live-owned run crashed, and a run's owning process/host, once recorded, cannot be reassigned.

Persistence (H2): one SQLite database per storage root (`runs.db`) holds the current state (`runs` table) and an append-only transition log with a payload-integrity hash per entry (`transitions` table); a run's state update and its transition entry commit in one transaction, so a mid-write failure rolls back the whole thing rather than leaving the two halves disagreeing. `load_record`/`read_transitions` remain the read API; JSON is a derived export, never the source of truth. `recover_crashed_runs` takes an exclusive local lock file so two recovery sweeps cannot race. Run IDs are restricted to a single safe path component (no nested/traversal segments). The code fingerprint is a content hash over the actual tracked source/config/fixture files (including uncommitted changes) with the git commit+dirty-flag kept only as separate informational metadata; two differently-dirty trees always produce different fingerprints.

## Implemented replay contract after the R3 historical-visibility repair

This section governs the current synthetic replay; execution and account phases in the earlier table remain future M2 requirements.

- Event time assigns a trade to a half-open minute. Availability determines when the system can use it. A delayed timer or action never publishes at an earlier event/effective time.
- The runner schedules phase-20 minute closures over the declared window independently of input ticks/timers. Only known contributors are closed; an empty interval stays empty. A first trade arriving after its bucket elapsed is published as closed at its actual availability.
- Phase 25 applies available corporate actions. Phase 30 processes external timers. `trade_replay_v1` additionally applies trades/quotes/corrections at phase 35 and publishes their bar versions at phase 37. External delayed timers publish closure effects at their actual availability, phase 37. Phase 40 evaluates observers after the complete same-time batch across all symbols.
- A `ReplayKey` contains availability, phase, sequence and a stable tie break. Source events use source sequence plus provider/feed/instrument/kind/event ID; bar publications use monotonic per-series publication sequence. Timestamp-only queries mean end-of-timestamp; explicit keys can bound phases/publications. Saved contexts capture immutable event/version collections and a fixed cutoff, never a mutable router.
- A completed-bar observer runs on a newly published closed bar for its instrument. Trade/quote arrivals and exact redelivery do not trigger it again. Revision callbacks require `observation.on_revisions: true`. Intrabar `availability_batch` observers run once per declared instrument after each batch, including clock/action batches. These are distinct modes.
- `observation.observer_id` supports `none`, `synthetic_threshold:v1` and `synthetic_closed_recorder:v1`. The threshold observer requires an explicit synthetic threshold and intrabar capability. The closed recorder requires closed-only capability. They produce diagnostics, not orders or strategy evidence. Every evaluation, including `None`, records identity, cutoff, cadence and the exact input references read.
- An unavailable/invalidated bar is absent from the actionable bar view. Known split-straddled buckets are invalidated even if the action arrives after closure or before the first late contributor. The full CLI attempt is non-success for an unsupported unit transition. Historical saved contexts remain unchanged.
- Quotes have an explicit capability and synthetic freshness policy. `latest_quote()` returns only a currently usable quote; a newer bad quote cannot resurrect an older good one. Diagnostic `quote_observation()` exposes unusable state. The current freshness clock measures age since availability, not a claim about real provider transport latency. Quote data is never added to trade-bar OHLCV.

### Replay configuration and source coverage

`interval_policy` supports `1Min`, `regular`, `raw`; `bars.rule_set_id` supports only the registered synthetic rules. The current calendar is `nyse-2024-v1`. Coverage must fit one actual regular session, including DST and early close. Use `window_end` for a short fixture; `session_close` must equal the actual exchange close.

`coverage` requires `window_start_at < required_boundary_at <= available_through_at`, provider, feed, instruments and session version. Input `coverage` additionally requires `available_through`, an `expected_event_ids` list (including repeated deliveries), and an `intervals` list with one `{instrument_id, bar_start, state}` per requested minute. State is `observed`, `verified_no_trade` or `missing_delivery`. Source inventory and interval evidence establish the declared synthetic coverage; input/generated timers do not. Source assertions are not independently verified provider history.

Recorded external events require `timing_label: recorded` and `received_at` no later than availability. Modeled external events require `timing_label: modeled`, no claimed receipt, and availability equal to the declared nonnegative `modeled_delay_ms` after their event base (bar end for provider bars; maximum event/effective/known time for actions). Source/provider/session and interval/unit compatibility are checked before dispatch. Relative input paths resolve from the config directory.

### Evidence and comparison

Replay reads the input bytes once, hashes and decodes the same bytes, and retains an attempt even when the input cannot be read. Unreadable input has explicit unavailable provenance. Malformed data is a failed attempt; incomplete coverage is a retained non-success with trace, manifest and error artifact. These guarantees assume the evidence destination itself is writable; storage/media failure cannot guarantee a complete artifact set.

Trace rows contain source input payloads and publication keys; evaluation rows reference consumed versions. Manifest schema 2 records config/data/code/dependency-lock/rule-implementation hashes, timing, coverage and observer settings. Semantic hashes exclude volatile run IDs and wall-clock timestamps.

Parity requires matching provider/feed/instrument/session/interval/bucket/unit basis and valid closed publications. Invalidated/forming bars are not comparable. Comparing revised series requires an explicit as-of cutoff and excludes publications after it; equal local revision counters alone do not establish a shared knowledge state. The comparator still requires its caller to choose the appropriate versions. Provider-rule certification remains blocked on D12/D22.


## Implemented synthetic session boundary (M2 repair, 2026-10-29)

`rsys run` uses its own strict JSON schema (`schema_version: 1`, `evidence_type: synthetic_standalone`). It accepts one dated 2024 NYSE regular session of one-minute raw bars, supplied scanner scores, fixed fees, quantity increments, absolute synthetic risk/cap settings, optional decision latency/max-hold/flatten and per-symbol `strategy_config`. Unknown fields are errors. This is not ReplayConfig, a research profile or a provider loader. `rsys replay` retains `execution.data_basis: inactive`.

For this synthetic model an open is an execution-only observation at interval start; final OHLC becomes available at its declared publication. Strategy decisions use bounded ReplayRunner contexts. Accepted intents reserve owned cash and are revalidated at the actual modeled entry price. Entry-interval protection settles when extrema become available; no exact touch timestamp is fabricated. Timer exits use the first eligible subsequent open. Missing required execution data, including overlapping unpublished position intervals, produces non-success. A null flatten setting is a finite-input diagnostic that may end with marked open positions, not permission for overnight market trading.

Run manifests are version 2 and identify source/dependency/config/input hashes, resolved settings, execution/account/scanner bases and semantic trace hash. Comparison requires successful sealed attempts and matching implemented assumptions except one explicitly permitted axis. Older manifests require rerunning their source input. Failed/malformed/unreadable invocations remain retained. `final-evaluate` exits 5 while blocked. See `CURRENT_SYSTEM_AND_NEXT_STEPS.md` for remaining gates.

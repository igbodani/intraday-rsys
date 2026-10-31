# Data, adjustments and calendar contract

These are proposed v1 semantics (D20). The provider manifest must identify feed, requested adjustment, symbol mapping, coverage, calendar version and action source. Missing required data blocks affected evidence; it never becomes a guessed default.

## Price and corporate-action units

- Fetch/cache `adjustment=raw` explicitly. Raw contemporaneous prices drive executions, current-session ORB levels and current-session VWAP. Keep the raw cache immutable.
- For comparisons spanning sessions, convert historical prices and volumes using only splits effective by the decision time and known from the action record. For a split ratio `f = new_shares / old_shares`, historical price becomes `old_price / f`; historical volume becomes `old_volume * f`. Compose multiple effective ratios. Prior-session dollar volume is unchanged by a consistent price/volume conversion.
- Scanner gain uses the previous regular-session raw close converted into current share units. RVOL lookback volumes and any prior-day ATR inputs use the same current-share basis. Never mix adjusted historical feature prices with unconverted current prices.
- A split at a bar/session boundary is applied after the old interval settles and publishes, but before new-unit opening orders. Convert retained account marks with the position so the old raw mark cannot double equity. A mid-bar effective action requires finer data or excludes the affected interval. Seeded positions change quantity by `f`, per-share cost/stop/target by `1/f`, leaving cash and total basis unchanged. Cancel/rebuild pending quantities and protective levels in the new units; revalidate tick/quantity rules before orders can fill. F17 uses an exact 2-for-1 ratio. A result requiring unsupported fractions/cash-in-lieu is blocked rather than rounded into a fabricated transfer.
- An action requires instrument identity, ratio/type, effective time, known/available time, source and revision. Dispatch the action at `max(effective_at, known_at)` and retain both timestamps; apply it only when effective and known. If essential action information is discovered after an affected decision, mark the research interval invalid/incomplete; do not silently revise earlier decisions. Future splits cannot change earlier outputs.
- If delayed old-unit observations still have unresolved execution when the split becomes effective, block the affected interval in v1. Do not settle an old-unit bar against an already converted position. Supporting that overlap later requires an explicit unit-versioned execution contract and additional fixtures.
- Do not use today's provider-adjusted history as a point-in-time feature series unless its action boundary has been verified. The API's symbol `asof` parameter concerns symbol/entity mapping; it is not proof of a complete historical universe or a historical corporate-action availability boundary.
- Ordinary cash-dividend price effects remain in raw price gain. Store known ex-dividend flags and report that this is a price-return measure, not total return. The proposed intraday baseline has no overnight entitlement; a fixture/extension needing dividend ledger credits must supply an explicit entitlement/cash-flow contract before running.
- Mergers, spin-offs, unsupported symbol transformations and unknown action types are excluded or block the affected run until explicitly supported. Map symbols through stable instrument identities. Do not synthesize shares, cash or tradeability from ticker text.

## RVOL and early-close sessions

Let `m` be elapsed regular-session minutes through the current completed bar. Select the most recent L prior sessions whose **scheduled duration is at least m**, searching at most `max_lookback_calendar_sessions` sessions (proposed 252). A prior early-close session ending before m is ineligible; its nonexistent afternoon volume is never treated as zero.

Select sessions by the dated exchange calendar before inspecting price/volume values. For each selected session, require complete declared feed coverage through m for that instrument. Missing selected-session data makes its current RVOL unavailable; do not skip it and substitute an older session. If fewer than L comparable sessions exist, mark unavailable.

At a morning minute, both full and shortened sessions can qualify because both contain that minute. At an afternoon minute beyond the shortened close, only longer sessions qualify. The current session can itself be shortened; compare results by session type because calendar matching does not eliminate holiday-volume seasonality. Session history uses the same feed and split units throughout.

Examples H01–H03 in `data_cases.json` establish the exact selection rule. F06 still handles flattening at the actual current session close.

## Minute-addressable, as-of history (`data/history.py`)

`MinuteObservation` carries one revision of one elapsed minute's declared coverage for one instrument/session/feed: instrument identity, feed, source, session date, 0-based elapsed minute index, an `available_at` timestamp (when that exact revision became knowable), a `revision` number, a `TRADED`/`NO_TRADE` status, a declared unit basis and the close/volume. `MinuteHistoryProvider` selects only the highest revision visible at or before the explicit decision `as_of` cutoff. An unavailable future correction does not displace an already-visible earlier revision; a delayed first publication remains missing until it becomes visible. Equal highest revisions must agree on all record fields, including source and availability timestamp. Conflicting provenance or payloads raise in either input order; identical duplicates contribute once.

The provider and minute-session builder require positive integer `elapsed_minutes`; zero, negative, fractional and boolean values are rejected instead of fabricating complete zero-volume history. Cutoffs and considered observation timestamps must have explicit timezones, and visible revision numbers must be nonnegative integers. Selected visible payloads require nonnegative finite Decimal volume, a finite positive Decimal close for `TRADED`, and zero volume/no close for `NO_TRADE`. Unknown statuses raise. Economic payload validation happens after visibility/revision selection, so an unavailable future correction cannot affect an earlier result.

A minute absent from the declared observations entirely -- or whose only revisions are not yet visible by the cutoff -- is a coverage gap (`MISSING_MINUTE`): the affected session's cumulative volume stays unavailable, never zero. A minute explicitly recorded as `NO_TRADE` (verified zero trades) contributes a real zero to that sum; a session built entirely from verified no-trade minutes is coverage-complete with a legitimately zero cumulative volume, which `features.compute_cumulative_rvol` then reports as `ZERO_DENOMINATOR`, distinct from `MISSING_SELECTED_HISTORY`.

`MinuteHistoryProvider` only accepts `UnitBasis.RAW` observations as a point-in-time feature input; a `SPLIT_ADJUSTED` observation is excluded with `UNSUPPORTED_UNIT_BASIS` rather than silently treated as comparable raw history, since its action boundary is unverified (D20). It does not itself apply `convert_split_units`'s as-of split conversion; a caller needing that must still convert explicitly.

Candidate prior-session dates come strictly from the declared calendar (`candidate_prior_session_dates`), never from whichever dates a provider's observations happen to include, and the search stops at the calendar's declared coverage boundary (treated as insufficient history, not an error) instead of raising past it. A session shorter than the requested elapsed minute retains its scheduled duration with `SESSION_TOO_SHORT`, unavailable volume and no selected revisions; its observations are not inspected. This preserves the calendar search budget and lets RVOL skip that session without ineligible data aborting the calculation. When the same session is comparable at an earlier minute, normal validation applies.

One `MinuteHistoryProvider` instance is fixed to one `elapsed_minutes`/`as_of`/`feed` decision, because the existing `HistoryProvider` protocol carries no elapsed-minute parameter; construct a fresh instance for a different elapsed-minute or cutoff. Composing it across a session's multiple elapsed minutes through `strategies.history.build_scanner_inputs` remains a separate, not-yet-connected next step. `MinuteHistoryProvider.session_builds`/`.previous_session_build` retain the exact denominator sessions, selected revisions and exclusion reasons used for the most recent call, for later reconstruction.

## Feed consistency (D12)

Alpaca distinguishes the single-exchange IEX feed from consolidated SIP data [2]. Therefore volume, RVOL and dollar-volume eligibility describe the selected feed's coverage. A limited-feed value must not be presented as total-market volume or pooled with another feed under an unchanged threshold.

Pin one feed across current observations and their lookbacks. Reject mixed-feed calculations. Changing feed produces a new data/experiment version, a fresh warmup and revalidation of thresholds. A limited-feed software test can check pipeline behavior; profitable results from that feed require their own evidence and may not transfer to a broader feed. Feed/data cost remains open; no subscription is selected here.

## Quality and time

- Canonical bar identity is `(instrument_id, feed, interval, bar_start, adjustment_mode)`. Exact duplicates may be deduplicated with a count; conflicting payloads for that identity fail validation until reconciled by a declared version policy.
- Validate nonnegative volume, finite positive prices, `low <= min(open,close) <= max(open,close) <= high`, interval length and explicit timezone. Reject conflicting/invalid rows; do not repair them by guessing.
- Detect missing required intervals from the supplied calendar and coverage policy. Missing bars are not proof of zero trades or a halt. Mark affected warmup/setup unavailable; no forward-filled price signal or invented execution.
- Convert exchange-local session times with `America/New_York` and the dated calendar. Never hard-code 14:30 UTC as every US opening. Provider timestamps require offsets/UTC or explicit source-zone metadata. Ambiguous or nonexistent local times require disambiguation, not the host timezone.

## Primary references reviewed 27 October 2026

[1] Alpaca historical bars: raw/split/dividend adjustment and symbol mapping parameters. https://docs.alpaca.markets/us/reference/stockbars

[2] Alpaca historical stock data sources. https://docs.alpaca.markets/us/docs/historical-stock-data-1

The provider facts above motivate our proposed contracts; they do not verify the user's entitlement, historical action coverage or account behavior.

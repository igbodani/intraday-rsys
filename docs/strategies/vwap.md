# Rising VWAP touch-and-hold — proposed baseline

Spec ID STRAT-VWAP-001. Long only. One-minute completed bars. Variant name `vwap_touch_hold_v1`. This tests a touch-and-hold pullback: a bar may trade below VWAP intrabar but must not close below it while the setup is active. It does not test recovery after a close below VWAP. A true reclaim is a separate later hypothesis. The earlier identifier vwap_touch_reclaim_v1 is retired; do not silently alias it in experiment records.

## Inputs and definitions

Use regular-session bars and reset at session open. Define bar-price proxy `p = (high + low + close) / 3` and session VWAP proxy `sum(p * volume) / sum(volume)`. It is a declared bar approximation, not trade-level VWAP. Zero cumulative volume is unavailable. A zero-volume bar contributes zero weight; missing bars invalidate affected signals until a declared recovery rule is satisfied.

Proposed parameters: warmup 20 completed bars; rising slope when current VWAP > VWAP five completed bars ago; pullback window five bars; one-tick confirmation buffer; entry deadline 11:00 exchange time; fixed 2R target; maximum hold 60 minutes; flatten five minutes before close; one filled entry per symbol/day. Risk/cost settings come from the portfolio profile.

## State transitions

| State | Condition on a completed bar | Action |
|---|---|---|
| WARMUP | Enough complete history and positive cumulative volume | IDLE |
| IDLE | Close > current VWAP and slope is rising | ARMED; earliest touch is a subsequent bar |
| ARMED | A subsequent bar low <= its current VWAP and close >= VWAP | PULLBACK; save touch bar low and start age at 1 |
| ARMED | Close < VWAP or slope no longer rising | IDLE; no proposal |
| PULLBACK | Later bar within five-bar window closes > current VWAP + buffer and > previous completed bar high + buffer; slope rising; scanner candidate; before deadline | PROPOSED; stop reference is minimum low from touch through confirmation |
| PULLBACK | Close < current VWAP, non-rising slope, missing required input, or window expires without confirmation | IDLE; discard setup |
| PROPOSED | Rejection/cancel/expiry | IDLE; require a new arm and later touch |
| PROPOSED | Fill | IN_POSITION |
| IN_POSITION | Protective/time/flatten exit | DONE for the day |
| Any unfilled state | Entry deadline | EXPIRED |

Evaluate invalidation before confirmation. A touch bar cannot also confirm. Age includes the touch bar; confirmation is allowed on ages 2–5. At the close of age 5, evaluate confirmation before expiring an otherwise valid setup. A noncandidate confirmation produces no order; it does not pause the window. Keep setup state for eligible universe symbols even when they are outside current top-N; scanner eligibility is required at proposal time.

## Proposal and exits

Stop = minimum observed low of the pullback/confirmation window minus one instrument tick, then rounded down to a tick. Reject nonpositive risk distance at actual entry. Entry uses the shared next-eligible-open contract. Target = actual fill + 2 × initial price risk, rounded up to a tick. Use fixed protection, max-hold and flatten exits exactly as ORB; no trailing or partial exits. Proposal expiry matches ORB. A filled position remains managed if VWAP or scanner membership later changes.

## Required tests

VWAP-01: hand-calculated weighted average and session reset. VWAP-02: warmup and zero volume. VWAP-03: slope uses only completed bars. VWAP-04: arm, later touch and later confirmation are separate events. VWAP-05: previous high excludes the confirmation bar. VWAP-06: invalidation/expiry ordering. VWAP-07: stop uses observed pullback lows only. VWAP-08: future mutation cannot change prior proposals.

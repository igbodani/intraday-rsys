# ORB15 — proposed opening-range breakout baseline

Spec ID STRAT-ORB-001. Long only. One-minute completed bars. Variant name `orb_close_cross_v1`. No result or profitability claim.

## Inputs and parameters

Regular-session OHLCV and calendar; scanner eligibility; instrument tick/quantity increments; prior close for scanner only. Defaults are proposals: 15-minute opening range, one-tick breakout buffer, entry deadline 11:00 exchange time, target 2R, maximum hold 60 minutes, flatten five minutes before session close, one filled entry per symbol/day. Risk and costs remain required external settings. No premarket level, RVOL filter or ATR filter is added inside this baseline strategy.

## State transitions

| State | Event / condition | Next state / output |
|---|---|---|
| COLLECT_RANGE | Completed bars within `[session_open, session_open + range_minutes)` | Accumulate range high/low; any missing required bar makes the symbol unavailable for this session |
| COLLECT_RANGE | Entire range available; high > low | ARMED; freeze range levels for the day |
| ARMED | A later completed bar closes strictly above range high + buffer, while the previous completed close is at or below that trigger; symbol is a current scanner candidate; before entry deadline | PROPOSED; emit one market-entry proposal |
| PROPOSED | Portfolio rejects/cancels/expires the intent | ARMED only after a later close returns to/below trigger; require a fresh crossing |
| PROPOSED | Entry fills | IN_POSITION; increment daily fill count |
| IN_POSITION | Stop, target, max hold or flatten exit | DONE for the day under one-fill baseline |
| Any unfilled state | Entry deadline or invalid data | EXPIRED; no new entries |

The range's last bar does not count as the breakout bar. For a 09:30 start and 15-minute range, the range is complete at 09:45; the first possible breakout bar is 09:45–09:46, with a decision no earlier than its availability.

## Proposal and exits

- Stop = frozen range low rounded down to a tick. Planned entry is the next eligible open under the shared execution contract; the signal close is not the fill.
- Quantity is calculated by portfolio risk/cash rules, not the strategy. If the actual entry is at/below the stop or arrives at or after the deadline, reject/cancel.
- On fill, initial per-share price risk is `entry_fill - stop`; target = `entry_fill + target_r * risk`, rounded up to a tick. Record intended monetary risk including cost estimates separately.
- Stop and target remain fixed for this variant. No trailing stop, partial exit, discretionary confirmation or same-day second fill.
- Maximum hold timer starts at fill time. Timer exits and session flatten use the shared first-eligible-open rule; exits continue after the entry deadline.
- A proposal expires at the earlier of the entry deadline or the second base-bar boundary after its decision. Scanner removal alone does not cancel an accepted intent; risk/expiry rules still apply.

## Required tests

ORB-01: no signal before the range is fully available. ORB-02: breakout bar excluded from range. ORB-03: equality to trigger does not qualify; first strict crossing does. ORB-04: noncandidate crossing gives no proposal. ORB-05: no re-entry after one fill. ORB-06: delayed entry after deadline rejected. ORB-07: future-bar perturbation leaves prior range/proposals unchanged.

Initial positive and negative examples are in `fixtures/strategy_cases.json`. Expand these to cover every listed condition. Mechanical correctness does not validate a profitable edge.

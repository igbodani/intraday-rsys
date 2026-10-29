# Decision register

Status: **Decided** = established direction; **Proposed** = specific candidate for review; **Open** = required information is absent. Proposed values below are experimental starting points, not optimized settings or approved financial risk.

| ID | Decision | Status | Value / proposed direction | Blocks |
|---|---|---|---|---|
| D01 | Starting capital and goal | Decided | $1,000; $1M aspiration; no withdrawals before that goal; no return deadline | Research account initialization |
| D02 | Markets/provider | Decided | Stocks and ETFs; Alpaca; crypto tentative | Provider design |
| D03 | Core architecture | Decided | Preserve event flow; modular experiments; simple conservative fills first | All implementation |
| D04 | Initial implementation stack | Proposed | Python 3.12, uv, pandas/NumPy, Pydantic, PyYAML, Parquet, SQLite, pytest; verify compatibility | M0 environment |
| D05 | First bar interval | Proposed | One-minute storage and decisions; five-minute aggregation is a later variant | Market baseline |
| D06 | Trading sessions | Proposed | Long-only regular-session entries; no borrowing or overnight positions | Market baseline |
| D07 | Data sessions | Proposed | ORB/VWAP baseline uses regular-session data; premarket data is an optional dependency for later strategies | Premarket strategies only |
| D08 | Acceptance policy | Proposed | Deterministic mechanical risk/portfolio acceptance | Market baseline |
| D09 | Account/quantity rules | Open | Verify actual Alpaca account, settlement, proceeds availability, whole/fractional share increments, minimum orders and supported order types | Broker-realistic market runs |
| D10 | Risk policy | Open | Per-trade risk, daily loss stop, maximum account drawdown, exposure and simultaneous-position limits | Any market experiment |
| D11 | Costs and latency | Open | Feed delay, decision delay, spread, slippage, fees and stress variants; no silent zero-cost default | Any market experiment |
| D12 | Data coverage/budget | Open | Feed entitlement and consistency, volume coverage, dates, inactive symbols, corporate actions, calendar, storage and budget; limited-feed results must be labeled feed-specific | Data acquisition and evidence |
| D13 | Historical universe | Open | Audit historical coverage; declare a dated universe or a documented restricted-universe proxy | Generalized universe claims |
| D14 | Scanner baseline | Proposed | Prior-day dollar-volume eligibility; rank by same-time cumulative RVOL, then session gain, then symbol; top 20; RVOL lookback 20 sessions | Scanner market run |
| D15 | Strategy/exit variants | Proposed | ORB15 and VWAP touch-and-hold variants in `strategies/`; 2R target, 60-minute hold, 11:00 entry deadline; one fill per symbol/day | Strategy market runs |
| D16 | Splits and trial budget | Open | Chronological development/validation/final dates, bounded parameter list, budget and seeds | Search or scoring |
| D17 | Promotion gates | Open | Minimum observations, uncertainty procedure, return/control requirement, drawdown ceiling and paper cohort duration | Promotion decision |
| D18 | Initial interface | Proposed | CLI and portable HTML/JSON reports | M0 command interface |
| D19 | Research expense accounting | Open | Whether data/compute costs or later deposits reduce or augment the $1,000 account | Economic interpretation |
| D20 | Data adjustment/session semantics | Proposed | Raw execution prices; as-of split conversion for historical features; calendar-aware RVOL lookback in `data_rules.md` | Data implementation and market baseline |
| D21 | Ranking evidence storage | Proposed | Compact per-decision snapshots with deterministic reconstruction; measure storage and enable full snapshots only for declared diagnostic runs | Scanner/report implementation |
| D22 | Trade-driven in-progress bars | Open | Requested capability (`IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md`): when input is individual trades, construct an evolving bar from only the trades available so far, using a versioned provider/feed-specific aggregation rule set; keep completed-bar replay and optional quote state as separate, non-additive inputs | Replay (R1–R3) design; exact feed, timestamp interpretation, delivery/correction history, condition-code rules, revision retention and quote access/entitlement remain unresolved |

**D22 software-capability note (2026-10-29):** the synthetic/software side of this capability (forming bars, an immutable version store, optional quote state, a `SeriesRouter`/`ReplayRunner`) is now implemented and tested (`docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md` R3-B). D22 stays **Open** because its provider-facing dependencies -- real feed entitlement, verified condition-code eligibility table, delivery/correction history and quote access -- remain unresolved and blocked on D12's audit; `FixtureBarRuleSet` and the offline parity comparator are declared fixture-only, never a substitute for that audit.

## Registered replay/data settings (implemented in `src/rsys/config.py`'s `ReplayConfig`; D22)

Per `IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md` section 9 -- declared as required interface points for R1–R3. As of the 2026-10-29 pass these are typed, validated fields on `ReplayConfig` (`configs/replay_bar_fixture.yaml`/`configs/replay_trade_fixture.yaml`), not merely proposed:

| Setting | Purpose / rule |
|---|---|
| `replay.input_mode` | `completed_bars`, `historical_trades`, or `recorded_events` |
| `replay.ordering_profile` | Explicit version: existing `bar_v1` phase profile or the new tested `trade_replay_v1` profile |
| `replay.availability_model` | Recorded receipt or explicitly modeled delay; label the evidence class |
| `bars.authority` | Provider snapshots or trade builder for a given series, never additive |
| `bars.rule_set_id` / hash | Exact provider or synthetic fixture aggregation semantics, versioned |
| `bars.interval` / session policy | Declared bucket boundaries; initially regular-session one-minute baseline |
| `bars.revision_policy` | Versioned as-known updates; no retrospective decision mutation |
| `bars.retention_policy` | Bounds and recovery behavior for correction lookup/state |
| `strategy.observation_mode` | `closed_only` baseline; forming-bar access requires a separately specified variant |
| `strategy.evaluation_cadence` | Closed-bar boundaries initially; intrabar cadence must be declared before its experiment |
| `quotes.enabled` / freshness policy | Optional; enabling dependent behavior requires valid data and entitlement |
| `execution.data_basis` | Distinguish conservative bar fills from subsequent-event trade estimates |



1. Review the baseline bundle D04–D08, D14–D15 and D18. The exact definitions in this pack are ready for review.
2. Confirm D09–D11 before market-account experiments. A fixture account does not answer these questions.
3. Audit D12–D13 early. A minimal real-data sample can reveal missing fields without opening final-evaluation outcomes.
4. Set D16–D17 before viewing comparative market results. Do not choose thresholds after seeing which strategy passes.

## Universe limitation

Ranking prior-day dollar volume is causal only within the supplied symbol coverage. It does not repair missing delisted stocks or a universe built from today's survivors. Preserve instrument identity, coverage dates and exclusions. Until historical membership is adequate, label conclusions as applying to the declared restricted universe. New daily asset snapshots help future studies, not reconstruction of unavailable past membership.

## Session distinction

Trading session and data session are separate fields. Regular-session trading may use earlier premarket observations when the relevant data is explicitly enabled. The proposed first two strategies do not require premarket levels. Gap-and-go remains disabled until its data and rules are specified.


## Deliberate baseline specializations

- **Scanner:** liquidity determines eligibility; RVOL determines rank; session gain is a tie-break, not a meaningful second ranking factor for most observations. This deliberately narrows the original product's broad movement/volume/liquidity wording. A composite or bucketed ranking is a later version requiring a separate hypothesis and comparison.
- **VWAP:** the first baseline is touch-and-hold. It does not answer the original broader reclaim question. A close-below-then-reclaim variant remains unimplemented and unspecified.
- Early-close lookbacks, corporate actions and feed limitations now have exact proposed contracts in `data_rules.md`; they are not silently inherited provider defaults.

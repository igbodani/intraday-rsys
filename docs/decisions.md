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
| D09 | Account/quantity rules | Open | Settlement: synthetic/sim accounts for backtesting (matches current `Portfolio` behavior); real Alpaca account/settlement rules apply only once live paper trading is built (not started). Whole/fractional share increments, minimum orders and supported order types remain **unverified** pending an actual Alpaca API check -- whole-shares-only stays the safe coded default until confirmed | Broker-realistic (paper/live) market runs only; backtesting is unblocked |
| D10 | Risk policy | Decided | Per-trade risk: 3% of current equity. Daily loss stop: 5% of equity (halts trading for the remainder of the day). Maximum account drawdown: 20% from all-time peak equity (separate from the daily stop). Simultaneous-position limit: no fixed count cap -- bounded only by available allocated cash, already enforced by `Portfolio`'s cash-reservation mechanism (`portfolio.py`, `acceptance.py`); no new code required | Any market experiment |
| D11 | Costs and latency | Decided | Commission: $0 (Alpaca standard commission-free equities). Slippage/spread: 5 bps (provisional). Feed delay: 250 ms (provisional). Decision delay: 0 ms (minimum achievable; owner intent is "as fast as the constraint allows," not an added buffer). Slippage, feed delay and decision delay are explicitly provisional and must be recalibrated once real paper-trading telemetry exists; do not treat as optimized | Any market experiment |
| D12 | Data coverage/budget | Open | Scope is backtesting-only for now -- no live/real-time feed is in scope. Historical data access is confirmed available, but no feed-entitlement/coverage/corporate-action audit has been performed; limited-feed results must still be labeled feed-specific until that audit happens | Data acquisition and evidence; live/paper runs |
| D13 | Historical universe | Proposed | Starting universe restricted to a single symbol: AAPL. This is a documented restricted-universe proxy per D13's own escape hatch, not a generalized/audited universe; expansion requires a new, explicit owner decision | Generalized universe claims (single-symbol scope is unblocked) |
| D14 | Scanner baseline | Proposed | Prior-day dollar-volume eligibility; rank by same-time cumulative RVOL, then session gain, then symbol; top 20; RVOL lookback 20 sessions | Scanner market run |
| D15 | Strategy/exit variants | Proposed | ORB15 and VWAP touch-and-hold variants in `strategies/`; 2R target, 60-minute hold, 11:00 entry deadline; one fill per symbol/day | Strategy market runs |
| D16 | Splits and trial budget | Proposed | Chronological 70% development / 15% validation / 15% final-holdout. Trial budget: 50 parameter/config trials max during dev+validation tuning (owner delegated the number; recorded as a Proposed experimental cap, not an owner-approved optimum). Seeds: not yet specified | Search or scoring |
| D17 | Promotion gates | Proposed | Minimum 30 closed trades in the validation window before promotion to paper-candidate status (owner delegated the number; recorded as Proposed, not approved). Uncertainty procedure, return/control requirement and paper cohort duration remain unspecified; drawdown ceiling reuses D10's 20% | Promotion decision |
| D18 | Initial interface | Proposed | CLI and portable HTML/JSON reports | M0 command interface |
| D19 | Research expense accounting | Decided | Only trading P&L changes the account's value; the owner will never deposit or withdraw funds. Data/compute costs are tracked entirely external to the $1,000 account, not as deductions against it | Economic interpretation |
| D20 | Data adjustment/session semantics | Proposed | Raw execution prices; as-of split conversion for historical features; calendar-aware RVOL lookback in `data_rules.md` | Data implementation and market baseline |
| D21 | Ranking evidence storage | Proposed | Compact per-decision snapshots with deterministic reconstruction; measure storage and enable full snapshots only for declared diagnostic runs | Scanner/report implementation |
| D22 | Trade-driven in-progress bars | Open | Requested capability (`IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md`): when input is individual trades, construct an evolving bar from only the trades available so far, using a versioned provider/feed-specific aggregation rule set; keep completed-bar replay and optional quote state as separate, non-additive inputs | Replay (R1–R3) design; exact feed, timestamp interpretation, delivery/correction history, condition-code rules, revision retention and quote access/entitlement remain unresolved |

**Step 6 owner decision round (2026-11-05):** D09–D13, D16–D17 and D19 were reviewed directly with the owner. D10 (3%/5%/20% risk policy, cash-bounded position limit), D11 ($0 commission; 5 bps slippage and 250 ms feed delay provisional; 0 ms decision delay) and D19 (no deposits/withdrawals; costs tracked outside the account) are owner-confirmed and **Decided**. D13 is recorded **Proposed** as a documented single-symbol (AAPL) restricted-universe proxy, not an audited universe. D16/D17's numeric budgets (50 trials; 30 trades minimum) were delegated by the owner rather than chosen directly; per the "do not invent a... trial budget or promotion threshold" rule they are recorded as **Proposed** experimental caps, not owner-approved optimums, and need explicit confirmation before being used to gate any real promotion decision. D09 (share increments/order types) and D12 (feed entitlement/coverage audit) stay **Open**: the owner has confirmed backtesting-only scope with historical data access, but no live-account or data-provider audit has been performed. D22 remains Open and still blocked on D12.

**Implementation/provenance note (2026-10-29):** The preceding imported entry states 2026-11-05, which is after this review; its owner-round date needs verification. Its recorded values and decision statuses are preserved, not re-approved or silently activated by the repair. D10 equity-fraction sizing, daily-loss/drawdown halts and D11 research cost/feed-delay settings are not yet enforced by the synthetic run configuration. The pending research baseline remains blocked until executable settings, audits and the remaining experiment protocol are complete.

**D22 software-capability note (2026-10-29):** the synthetic/software side of this capability (forming bars, an immutable version store, optional quote state, a `SeriesRouter`/`ReplayRunner`) is now implemented and tested (`docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md` R3-B). D22 stays **Open** because its provider-facing dependencies -- real feed entitlement, verified condition-code eligibility table, delivery/correction history and quote access -- remain unresolved and blocked on D12's audit; `FixtureBarRuleSet` and the offline parity comparator are declared fixture-only, never a substitute for that audit.

## Registered replay/data settings (implemented in `src/rsys/config.py`'s `ReplayConfig`; D22)

The implemented configuration fields are listed below. Their synthetic values are software test settings, not approved market assumptions.

| Setting | Implemented rule |
|---|---|
| `replay.input_mode` | `completed_bar`, `historical_trade`, `recorded_event` |
| `replay.ordering_profile` | `bar_v1` or `trade_replay_v1`; trades/quotes/recorded-event input require the latter |
| `replay.availability_model`, `modeled_delay_ms` | Recorded receipt evidence or an explicitly declared modeled delay |
| `bars.authority`, `rule_set_id` | One authority: provider publication or trade builder; only registered synthetic rules |
| `interval_policy.interval`, `.session`, `.unit_policy` | `1Min`, `regular`, `raw` only |
| `revision.revision_policy`, `.retention_limit`, `.unavailable_history_policy` | Latest version as of cutoff; declared contributor retention; explicit unavailable-history failure |
| `observation.mode`, `.cadence`, `.observer_id`, `.threshold`, `.on_revisions` | Closed-only/intrabar capability, publication/batch cadence, optional named synthetic observer and revision opt-in |
| `quotes.enabled`, `.freshness_limit_ms`, `.quality_policy` | Optional quotes with explicit synthetic freshness/quality policy |
| `coverage.*` | Requested window, available-through time, source identity and instruments; separate source inventory/interval evidence required |
| `execution.data_basis` | `inactive`; no execution exists yet |

**R3 repair (2026-10-29):** frozen context collections and publication keys, availability-timed actions/closures, internally scheduled minute boundaries, enforced configuration, public quote usability and CLI synthetic observers are implemented. See `R3_REPAIR_RESULTS.md`. These are software semantics; D22 remains **Open**, as do the existing provider/market decisions. Raw-unit-only replay does not claim split-adjusted rolling features or position conversion, and unsupported straddled intervals produce non-success. ORB/VWAP baseline definitions are unchanged.

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

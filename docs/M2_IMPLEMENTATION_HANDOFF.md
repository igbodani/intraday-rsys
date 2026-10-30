# M2 implementation handoff for Copilot

> Historical planning or implementation record. Starting-state descriptions and paths reflect its original revision. Use [ARCHITECTURE.md](ARCHITECTURE.md) for current packages and [CURRENT_SYSTEM_AND_NEXT_STEPS.md](CURRENT_SYSTEM_AND_NEXT_STEPS.md) for current status. Behavioral requirements still apply unless superseded by the decision register.

**Current-state update (2026-10-29):** this document remains the detailed target specification. Its starting-state descriptions predate the standalone M2 implementation and subsequent repairs. Read [CURRENT_SYSTEM_AND_NEXT_STEPS.md](CURRENT_SYSTEM_AND_NEXT_STEPS.md), [M2_IMPLEMENTATION_STATUS.md](M2_IMPLEMENTATION_STATUS.md) and [M2_REPAIR_RESULTS.md](M2_REPAIR_RESULTS.md) before assigning the next ticket; do not rebuild repaired components or claim the full handoff complete.

Date: 2026-10-29. Project: `igbodani/intraday-rsys`.

**Objective:** turn the repaired offline replay foundation into an inspectable synthetic trading-research path: causal observations → features/scanner → strategy proposals → acceptance/reservations → modeled execution → account state → results. Preserve decisions from forming bars as a supported capability, while implementing the existing ORB and VWAP strategies exactly as their completed-bar specifications.

This is a new implementation plan, not a defect report. Unless a step identifies a regression found during implementation, its tasks describe work still to build or behavior to preserve. Do not rewrite working R3 components merely because they are mentioned here.

## 1. Starting point and authority

The repository was read at commit `2cd1e4a3173b6ea923f0a5ecbd93166af181cc93`, branch `fix/r3-replay-causality`. At the time of this handoff, `develop` still points to `7084d4a03026755693b7f8ce0bb11da41808a6db`; `main` is older. **Do not start M2 from the old `develop` without the replay repairs.**

`docs/R3_REPAIR_RESULTS.md` records 426 passing tests, clean Ruff, and eight retained replay CLI attempts. Those are the preceding repair pass's results, not tests rerun while writing this handoff. Re-establish the baseline on the actual checkout. Preserve any correct newer work.

Read these authoritative inputs before implementation:

1. `AGENTS.md` and `docs/decisions.md`.
2. `docs/contracts.md`, `docs/data_rules.md`, and `docs/experiments.md`.
3. `docs/tickets.md`, `docs/requirements.md`, and `docs/R3_REPAIR_RESULTS.md`.
4. `fixtures/README.md`, `fixtures/cases.json`, `fixtures/data_cases.json`, and `fixtures/strategy_cases.json`.
5. `docs/strategies/orb.md` and `docs/strategies/vwap.md`.
6. `docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md`, especially the M2-01T and forming-bar requirements. Earlier R3 repair handoffs are historical context, not instructions to undo the newer fixes.

Use this handoff for sequencing and implementation detail; preserve the repository's established research semantics. If a genuine conflict changes a market assumption or a strategy rule, record the exact conflict and continue independent work. Do not silently choose a different strategy, risk rule, execution model, or fixture outcome.

### Existing code to reuse

| Existing component | Role in M2 |
|---|---|
| `replay.py`, `data/keys.py` | Availability clock, `ReplayKey`, declared phases and stable ordering |
| `replay_runtime.py` | `ReplayRunner`, `SeriesRouter`, scheduled closures, observer dispatch |
| `views.py` | Frozen `ReplayView`, `ClosedOnlyContext`, `IntrabarContext`, consumed input references |
| `data/bar_builder.py`, `data/bar_version_store.py` | Forming/closed/revised publications and immutable versions |
| `data/actions.py`, `calendar.py` | Existing conversion helpers and declared 2024 calendar; inspect limits before reuse |
| `replay_validation.py`, `data/manifest.py` | Source identity, coverage and timing checks |
| `config.py`, `registry.py` | Profile isolation, supported settings, method/evidence registration |
| `experiments.py`, `storage.py` | Retained attempts, frozen provenance, transactional lifecycle |
| `replay_io.py`, `cli.py` | Replay traces, manifests, errors and configured synthetic observers |

The current CLI replay supports a bounded single regular session in 2024, one-minute/raw data, and synthetic rules. It is not a ready-made multi-session research runner. The execution/account phases are declared but not active. `execution.data_basis` is currently `inactive`. Account, scanner and strategy modules below are **proposed new modules**, not existing APIs.

## 2. Scope, order and stopping points

Implement one reviewable slice at a time. Complete each slice's behavior and tests before making dependent code rely on it. Do not stop merely to ask permission for routine engineering choices within the assigned implementation scope.

| Step | Repository ticket | Deliverable | Gate before continuing |
|---|---|---|---|
| 0 | M2 preparation | Correct starting source, baseline checks, traceable implementation checklist | Repaired baseline established |
| 1 | M2-01a | Typed proposals/orders/fills/account events and phase-aware engine extension | Event order and capability isolation tested |
| 2 | M2-01b | Decimal ledger, risk sizing, acceptance and reservations | Accounting/risk invariants tested |
| 3 | M2-01c | Conservative bar execution, positions, timers and supported split conversion | F01–F17 pass through real code |
| 4 | M2-01d | Public fixture CLI with retained evidence | All 17 cases executable and inspectable |
| 5 | M2-01T | Subsequent-event trade execution | Self-fills and stale-print fills rejected |
| 6 | M2-02a | Causal historical features and deterministic scanner | H01–H03, DQ08, ranking reconstruction pass |
| 7 | M2-02b | `orb_close_cross_v1` | ORB-01–07 and S01/S02 pass |
| 8 | M2-03 | `vwap_touch_hold_v1` | VWAP-01–08 and S03/S04 pass |
| 9 | M2-04 | Synthetic `run`/`compare`/report path and protected holdout boundary | End-to-end traces, reconciliation and causality pass |

The suffixes `a`–`d` above are work subdivisions, not replacement milestone IDs. Retain the repository's M2-01, M2-01T, M2-02, M2-03 and M2-04 identifiers in requirement mappings.

**First independently deliverable milestone:** M2-01, steps 0–4. **Full handoff target:** offline synthetic M2 through step 9, if the implementation prompt assigns the full document. M2-01T must pass before any trade-input P&L is emitted; a bar-only path may progress independently while that adapter is incomplete.

### Work outside this scope

- No market-data purchase/download, broker connection, live/paper orders, account changes or trade recommendations.
- No optimizer, parameter sweep, Kronos/model integration, Nautilus backend or order-book/queue simulation.
- No claims about current Alpaca account/settlement/fee rules; those require separate verification.
- No new live intrabar ORB/VWAP variant. Prove the intrabar proposal/execution plumbing with an explicitly named fixture-only test strategy; research variants require their own specification.
- No M3 statistical promotion implementation or real holdout evaluation. Build the access boundary and synthetic tests; preserve unresolved research gates.

### Settings that must remain explicit

| Topic | Instruction |
|---|---|
| D09 account/quantity rules | Implement configurable mechanics using declared fixture profiles; do not assume real settlement, fractional shares, leverage or order support |
| D10 risk | Implement declared limits and method semantics; synthetic values are allowed. Do not choose approved research risk fractions, drawdown limits or daily-loss behavior |
| D11 costs/timing | Every executable profile identifies its costs and timing model. Zero costs are explicit fixture simplifications, never a research fallback |
| D12/D13/D22 provider/universe | Keep source/feed/coverage/unit checks and fixture-only aggregation labels. Synthetic execution does not certify provider behavior |
| D16/D17 research | Leave splits, trial budgets and promotion methods unresolved until supplied; no scoring or tuning around these blockers |
| Proposed D05–D08/D14–D15 | Implement the written variants under explicit settings; their existence does not mark the proposed market baseline approved |

## 3. Rules every step must preserve

1. **One causal clock.** A decision, an accepted intent, an eligible execution observation and a recorded fill have distinct identities and keys. Event time is not information availability. Nothing publishes, fills, releases cash or changes a saved decision in the past.
2. **Frozen decision inputs.** Strategies see bounded immutable contexts and a read-only portfolio snapshot. No mutable router, builder, raw future arrays, execution-private OHLCV, broker handle or ledger mutation capability is exposed.
3. **No signal self-fill.** A closed-bar signal cannot fill at its signal close. A trade-driven decision cannot fill on any trade already consumed by that decision's batch. Moving the same observation to a later phase is not a new execution opportunity.
4. **No fabricated path.** OHLC describes an interval, not the sequence of prices inside it. Never use its final volume to size a position at that interval's open. Never apply its pre-entry extremes to a mid-interval entry.
5. **Separate scanner and position management.** Candidate removal stops new proposals as defined by the strategy; it does not unsubscribe a held position from protection/timers or silently cancel an already accepted intent.
6. **Atomic, idempotent account changes.** Invalid transitions leave all balances, reservations, orders and positions unchanged. Duplicate proposals/fills/actions must not charge fees or move money twice; conflicting duplicate payloads fail explicitly.
7. **Explicit uncertainty and missingness.** Missing data, unmodeled unit transitions and unavailable execution events cannot become invented zero volume, a closing price or a successful flat account.
8. **Retained evidence.** Every attempt has its own run ID, inputs, state transitions and final status. Failures and interrupted runs remain visible. Synthetic results are labeled as such.

## 4. Step 0 — establish the baseline

### Do

1. Read the current branch, HEAD, status and relevant instructions. Preserve unrelated work. Use an isolated branch/worktree based on the repaired commit or a verified descendant. Do not reset user changes or merge/push merely to begin coding.
2. Confirm the checkout contains the R3 clock/config/context repairs and the four replay example configurations. Inspect newer changes rather than requiring the historical test count exactly.
3. Recreate the environment using the existing lockfile. Do not copy a Windows virtual environment or pin fresh dependency versions unnecessarily.
4. Run the baseline commands below. Record failures before making changes. Distinguish environment/tool failures from code failures.
5. Create `docs/M2_IMPLEMENTATION_STATUS.md` with ticket, requirement IDs, proposed tests, status, actual commands, results and blockers. Map existing requirements rather than inventing duplicate definitions.

```bash
git status --short
git rev-parse HEAD
uv sync --frozen --group dev
uv run pytest tests/ -q
uv run ruff check .
```

Run each of these replay commands twice and retain the output paths:

```bash
uv run rsys replay --config configs/replay_bar_fixture.yaml --out auto
uv run rsys replay --config configs/replay_trade_fixture.yaml --out auto
uv run rsys replay --config configs/replay_intrabar_observer_fixture.yaml --out auto
uv run rsys replay --config configs/replay_closed_observer_fixture.yaml --out auto
```

### Test / accept

- Required R3 checks pass on the actual checkout. Each repeat gets a distinct retained attempt and the same semantic trace hash for identical inputs/settings/source.
- Reuse `test_r3_clock_fixes.py`, `test_replay_contract_fixes.py`, `test_r3_acceptance.py`, `test_replay_cli.py`, `test_event_order.py` and `test_causality.py` as regression coverage; do not replace them with weaker M2 tests.
- A genuine baseline regression is fixed and explained before dependent implementation. An unavailable tool is reported, not counted as a pass.

## 5. Step 1 — contracts and phase-aware runtime (M2-01a)

### Do

Add small typed immutable domain records, using existing validation/serialization conventions. Suggested homes are `orders.py` for order/proposal contracts and `engine.py` for orchestration; splitting differently is acceptable if responsibilities remain clear.

| Record | Minimum content |
|---|---|
| Strategy evaluation | Strategy/version, instrument, full cutoff, cadence, exact input references, state transition, proposal/no-proposal and reason |
| Proposal | Deterministic ID, strategy/version, instrument, side, decision key, setup identity, entry rule, expiry, stop/exit policy, feature/ranking references |
| Accepted intent | Proposal ID, acceptance key/sequence, ready time, reserved cash, quantity/risk basis, expiry and status |
| Execution reference | Source event ID/key, market time, availability, instrument/unit basis, reference price, data mode and eligibility reason |
| Fill | Deterministic fill ID, order/proposal ID, quantity, raw reference, modeled price, itemized costs, execution/ledger keys and timestamp precision |
| Position | Instrument, quantity, basis, entry fill/key, protective levels with activation keys, hold/flatten state and originating strategy |
| Ledger transition | Event ID/key, immutable before/after summary or balanced entries, fees/flows, reservation changes and account version |

Use deterministic semantic IDs. Do not derive proposal order or IDs from input-list positions, wall-clock time, random UUIDs or unrelated future events. Run IDs remain independently unique. Reject invalid/nonfinite/negative amounts and bool-as-number inputs according to the current schema rules.

### Extend the runtime carefully

The current `ReplayRunner.run_with_observers()` drains a timestamp using `run_through(point)` and then evaluates observers. That is safe for the current data-only stream, whose source dispatch stops before phase 40. **It cannot simply gain phases 50–80 and keep draining the whole timestamp before making a decision.** That would put acceptance/fills/account changes ahead of the decision that should precede them.

Implement phase-bounded dispatch or a shared phase scheduler. The existing data-only runner and M2 engine must share ordering and view construction; do not create a second incompatible replay clock. Domain events can be a separate typed union from provider `MarketEvent`, provided they use the same keys/queue and never masquerade as provider data.

Preserve the declared order:

| Phase | Required M2 behavior |
|---|---|
| 10 | Settle eligible pre-existing protection for the completed interval; keep its OHLCV execution-private |
| 20 | Publish completed observations and mark account equity in the correct units |
| 25 | Apply effective, available actions and atomic supported unit conversion |
| 26 | Release only explicitly available settlement/cash flows |
| 30 | Process session, cutoff, expiry, max-hold and flatten timers |
| 35/37 | Apply trade/quote/correction input and publish versions under the trade profile |
| 40 | Finish same-time market updates for all relevant symbols, freeze feature/ranking/account views, then evaluate strategies |
| 50 | Deterministically accept/reject proposals and reserve cash |
| 60/70 | Execute eligible exits before entries using the selected execution adapter |
| 80 | Record transitions, diagnostics and invariant checks |

Derived events must not be inserted behind the current phase/key. Specify a deterministic sub-order for expiry versus other timers and for account/strategy notifications. Emit fill/rejection notifications once; strategy state changes from those notifications must also be recorded.

For bar mode, a zero-latency decision based on the previous bar may use a **separate next-bar open** at the same clock time in a later phase, as the existing contract explicitly allows. Mark this as an idealized model. A positive decision delay past that open skips it. A final bar packet alone must never make its close available early or pretend it contains recorded open-arrival evidence.

### Test

Create `tests/test_engine_order.py` and `tests/test_order_contracts.py` (proposed names):

- Two symbols with reversed input order produce the same frozen ranking, acceptance order and account transitions.
- A phase-40 strategy cannot see phase-60/70 fills or next-bar final OHLCV; its read-only account snapshot does not change afterward.
- Settlement occurs before a new decision; a stop change at that decision cannot affect the preceding interval.
- A derived event inserted into a completed phase fails without mutation.
- Duplicate proposal/notification/fill IDs are idempotent; conflicting reuse fails.
- `ready_at` and full event keys both constrain execution; equal timestamps alone do not establish eligibility.
- Existing replay-only behavior and frozen saved contexts remain unchanged when execution is inactive.

**Accept:** a tiny public-engine trace proves the order above. No strategy implementation or fixture harness may manually do the missing runtime work outside the engine.

## 6. Step 2 — ledger, acceptance, risk and reservations (M2-01b)

### Do

Implement `portfolio.py` with one account owner and explicit monetary state. `positions.py` owns position lifecycle; execution requests changes through validated transitions rather than writing balances independently.

1. Parse money, prices and quantities directly from strings into `Decimal`. Declare precision and instrument tick/quantity rules; do not pass through binary float.
2. Keep total cash, spendable cash before reservations, unsettled proceeds, reserved cash, holdings, fees, external flows, realized completed-trade P&L and open-position expenses distinct.
3. Under the supported fixture cash partition, total cash equals spendable plus unsettled cash; reservation is an allocation of spendable cash, not an extra expense. Available-to-reserve cash equals spendable cash minus live reservations. Equity equals total cash plus marked holdings.
4. Buying consumes notional plus entry fees once and creates basis/position state. Selling removes basis, adds modeled proceeds less exit fees, recognizes net completed-trade P&L, and moves proceeds into the bucket specified by the account profile. An explicit settlement release transfers between buckets without changing total cash or equity.
5. Track slippage/spread through the modeled execution price and fees separately. Do not deduct the same slippage again as an extra cash charge. Expose costs on open positions so equity reconciles even when completed-trade P&L is still zero.
6. Seeded fixture positions have already paid their purchase out of initial cash; do not buy them twice. Store their basis and mark explicitly.

### Sizing and acceptance

For a long, choose the largest allowed quantity satisfying all declared bounds:

```text
intended stop loss(q) = q × (modeled_entry - stop)
                       + estimated entry costs(q)
                       + estimated stop-exit costs(q)

intended stop loss(q) <= decision risk budget
entry notional(q) + entry costs(q) <= unreserved spendable cash
entry exposure(q) <= the declared remaining exposure/position capacity
```

Risk budget is the explicit fixture amount or configured fraction of the frozen decision equity. Do not accidentally recompute it using a future fill price/equity. At execution, recheck actual modeled entry, the original permitted risk, current cash and limits; reduce quantity to a valid step or reject according to the declared policy. Do not increase quantity above the accepted amount simply because later conditions improve.

Round quantity downward, long stops down to the tick and long targets up to the tick. Test arbitrary decimal increments, not only two decimal places: `Decimal.quantize()` alone does not ensure multiples of a tick such as `0.05`. Reject a zero/nonpositive risk distance and zero feasible quantity. Fixed fees make one-share cases especially important.

Sort proposals by the existing contract `(decision_available_at, scanner_rank, instrument_id, strategy_id, proposal_id)`. Require a current rank for the baseline. Reserve cash immediately in that order. Sort pending fills by readiness and acceptance sequence, not a later scanner ranking. Account state, not scanner membership, prevents duplicate pending/held positions for the same instrument.

Cancellation, expiry, rejection and a fill-time reduction release only the appropriate reservation. Reservation release never makes unsettled proceeds spendable. Exits processed at the same execution opportunity may free usable cash for later eligible intents according to the cash model; do not resurrect a rejected proposal retroactively.

Daily loss, drawdown, maximum positions and exposure checks must be named, configured mechanisms with explicit trigger/reset/action semantics. If the repository lacks a material semantic definition, implement the interface and a declared synthetic test method; retain the research blocker instead of inventing a market rule. Never disable protective exits merely because new entries are blocked.

### Test

Create `tests/test_portfolio.py`, `tests/test_sizing.py` and `tests/test_acceptance.py`:

- Exact money identities after every transition, including fees on an open position.
- Two simultaneous proposals cannot reserve the same cash. F05 gives ABC six shares and XYZ four, independent of JSON order.
- F13: `2q + 1 + 1 <= 10` gives `q=4`, reservation `401`, and intended stop risk `10`.
- F14: a risk budget of `3` cannot fund one share with a `2` price distance and `2` total fees.
- F04 and F15 distinguish insufficient cash from insufficient spendable cash; total balance alone does not authorize a purchase.
- Rejected, duplicate and conflicting transactions leave every relevant state component unchanged.
- Cancel/expire/fill-time reduction releases exactly the remaining reservation; repeated release is harmless or explicitly rejected without a second release.
- Actual open moves above the sizing reference: reduce or reject without overspending or exceeding risk. Entry at/below stop rejects and releases cash (F11).
- Quantity/tick rounding, exact step boundary, one step too large, fixed fees, fractional fixture increments, zero/negative/nonfinite input.
- Property-style generated event sequences with fixed seeds: no negative available cash, no negative positions, no double fees, no stranded terminal reservations. These tests call the real transition API; expected invariants are independently calculated.

**Accept:** ledger transitions can be replayed/reconciled from their evidence; accepted size and cash effects have independent arithmetic tests. No placeholders for F13/F15 are allowed.

## 7. Step 3 — conservative bar execution and position lifecycle (M2-01c)

### Do

Implement `execution.py` and `positions.py` using the shared engine. Keep the price/cost model replaceable but explicit and versioned.

1. **Separate open from completed-bar information.** The execution adapter receives a narrow open-price observation or an explicitly modeled open extracted from a historical bar. That open can be available at the interval start only under the declared historical execution proxy; the remaining OHLCV stays private until publication. The execution adapter may later inspect completed OHLCV to settle protection that was active during that interval.
2. **Entry eligibility.** Require correct instrument/units, a subsequent eligible open, readiness, unexpired intent, permitted session, valid price/risk and current cash/limits. Do not fill at the close that generated the proposal.
3. **Price model.** Specify exactly how spread, adverse slippage, fees and tick rounding turn a raw reference into a modeled entry or exit. Reproduce F08. Do not infer a spread from unavailable quotes or use a stale quote.
4. **Protection.** Activate stop/target after entry. Use the levels active during the interval. A stop wins if both stop and target were touched and their order is unknowable. Stops gapped through use the observed open with adverse modeled costs. A favorable target gap uses the target reference conservatively (F12).
5. **Protection changes.** A change requested at a completed-bar decision affects later keys only. Preserve level activation history so settlement cannot read a newer stop retroactively (F07).
6. **Exit priority.** At a common open, scheduled time/flatten exits precede gap protection, then entries. Close once and cancel the other protection. At interval settlement, stop precedes target when ambiguous.
7. **Time precision.** An OHLC-inferred exit has `time_precision: bar_interval`, its interval bounds and ledger availability. Do not fabricate a tick timestamp or spend inferred proceeds earlier than their recorded availability.
8. **Timers.** Schedule max-hold from actual entry and flatten from the calendar's actual session close. A flatten buffer must be at least one base bar. Require a usable execution event before close; missing data produces retained incompleteness, not a fill at the last remembered mark.
9. **Scanner independence.** Route events/timers to all held positions and pending intents. A symbol with no current candidate rank still receives gap exits, protection and flattening. Do not use the candidate set as the execution subscription list.

### Splits and units (required by F17)

Reuse existing action utilities, but wire them through account/position/feature-reference state. A helper-only test is not sufficient.

- For an available, effective supported boundary split, first settle old-unit work, then atomically convert quantity, per-share basis, stops, targets and retained marks; preserve total basis, total cash and equity.
- Convert prior reference prices/volumes to current units using only available/effective actions. Do not rewrite raw history or saved earlier contexts.
- Cancel/rebuild pending intents and reservations in the new units and revalidate increments. Define and record the identity/link between the replaced and rebuilt order.
- Unknown fractions/cash-in-lieu, mid-interval splits and unresolved delayed old-unit execution remain explicit unsupported/non-success cases. Do not weaken R3's conservative guards to make F17 green.
- A late-known essential action invalidates affected evidence and prevents further dependent fills; it does not silently rewrite completed fills or earlier decisions.

### Test

- Run all F01–F17 through the actual engine, not case-ID branches returning expected values.
- Same-price entry and stop rejects; actual gap through stop rejects despite earlier acceptance.
- Stop/target ambiguity, gap exit, favorable gap, entry-bar protection, stop updates and fee-once rules.
- Max-hold and flatten share an event: one exit and one fee. Test both full and early-close sessions and missing final execution data.
- A pending intent expires even if no more trades or opens arrive; reservations are released by the timer.
- Removing a candidate before a gap exit, a later interval stop and flatten does not change position-management outcomes.
- Future/revised bar information never retroactively changes a settled fill. A bar execution model unable to reconcile revised execution evidence must mark the affected result invalid/incomplete under an explicit policy, not quietly restate P&L.
- F17 preserves equity at the intermediate action checkpoint as well as after the new-unit open. A future split cannot change an earlier mark, feature or fill.

**Accept:** full F01–F17 trace/checkpoints pass with Decimal equality. Synthetic bar fills are labeled as modeled interval/open estimates, not tick-accurate or real liquidity evidence.

## 8. Step 4 — public fixture runner and evidence (M2-01d)

### Do

Implement the existing target command:

```bash
uv run rsys fixture --case all --out auto
```

Support `--case F01` through `--case F17` and an explicit output directory. `all` means the account/execution suite only. Do not silently substitute data or strategy tests for account cases.

- Load fixture inputs into typed engine events; separate `expected*` fields from engine inputs. Keep expected values available only to the assertion/report layer.
- Normalize fixture-only shortcuts, such as injected proposals, initial positions and F05 ranks, in an adapter. Document them so they do not bypass real engine acceptance and execution.
- Where a single-symbol account case supplies no scanner, inject an explicitly fixture-only singleton candidate/rank snapshot; F05 supplies the ranked multi-symbol snapshot. Never use this convenience as a missing-rank fallback in a strategy/research run.
- These account cases are intentionally targeted streams, not complete market sessions. They may end with the positions shown in their expected checkpoints (for example F05, F10, F15 and F17). Do not require a fabricated session-close liquidation or full feature history to run them. Complete end-to-end session runs still enforce coverage and required flattening.
- F06's explicit synthetic session close is a fixture override; do not infer that its global fixture date is an actual early-close date or weaken market calendar validation to accommodate it. Test the dated calendar separately.
- Run each case in isolated account state. Attach per-case results and traces to a retained invocation attempt, or use a parent with child attempts if that fits existing storage conventions. State clearly which ID represents which unit.
- Preserve `fixture --out auto` semantics from `docs/contracts.md`: a new `tempfile.mkdtemp(prefix="rsys-fixture-")` directory, kept and printed. Explicit fixture output must be new or empty. Replay evidence-root semantics remain separate.
- Reuse `start_run`/`run_context` and SQLite lifecycle. Write resolved fixture profile, input/code/lock hashes, ordered event/account trace, checkpoint comparison, manifest and structured error where applicable.
- Use exit 0 for all checks passing, 2 for invalid configuration/unknown case, 3 for input/data failure, 4 for invariant/fixture mismatch and 1 for another execution failure. Do not mark a partially failed fixture invocation successful.

### Canonical account acceptance matrix

Exact JSON inputs and every checkpoint in `fixtures/cases.json` remain authoritative. These values summarize the independent expected outcomes:

| Case | Behavior to exercise | Expected result |
|---|---|---|
| F01 | Buy, mark and target exit | Cash `500` after entry; equity `1010` at mark `102`; final cash `1020`, net P&L `20` |
| F02 | Both levels touched in entry bar | Stop at `98`; final cash `990` |
| F03 | Existing stop gapped through | Exit `95`; final cash `975`, P&L `-25` |
| F04 | One share plus fee unaffordable | `INSUFFICIENT_CASH`; cash `100` unchanged |
| F05 | Shared-cash rank tie | ABC `6`, XYZ `4`, acceptance ABC first; cash `0`, equity `1000` |
| F06 | Synthetic early-close flatten | Timer `12:55`; exit `101`; cash `1005` |
| F07 | No retroactive stop update | Equity `1005` before change; next-open exit `99`; cash `995` |
| F08 | Adverse prices and fees once | Entry `100.10`, cash `499`, equity `999`; exit `101.90`, total fees `1`, cash `1008`, net P&L `8` |
| F09 | Zero stop distance | `INVALID_STOP_DISTANCE`; cash `1000` |
| F10 | Positive latency misses open | Skip `09:31`; fill `09:32` at `101`; cash `899`, equity `1000` |
| F11 | Actual entry through stop | `ENTRY_THROUGH_STOP`; cash `1000`, reserved `0` |
| F12 | Favorable target gap | Reference exit `104`, not `107`; cash `1020` |
| F13 | Cost-aware risk sizing | `q=4`, reservation `401`, risk `10`; entry cash `599`; stop-exit cash `990` |
| F14 | One share exceeds risk budget | `RISK_BUDGET`; no entry |
| F15 | Explicit unsettled release | First `INSUFFICIENT_SPENDABLE_CASH`; after release buy one at `500`; cash `400`, unsettled `0`, equity `900` |
| F16 | Candidate removed, position protected | Gap exit `97`; cash `985`, P&L `-15` |
| F17 | Supported 2-for-1 split | Quantity `10`, entry `50`, stop `49`, target `52`, cash `500`, equity `1000`; prior close `50`, prior volume `2000`, gap `0` |

### Test / accept

Create `tests/test_fixture_cases.py` and `tests/test_fixture_cli.py`:

- Parameterize all 17 cases and assert every supplied checkpoint plus additional reservation/order/account invariants.
- Run CLI subprocess tests, not just direct function calls. Validate exit codes, output schemas and retained failure attempts.
- Mutate an expected checkpoint in a temporary fixture copy: the CLI must fail visibly. This negative control proves that expected values are actually checked.
- Repeat identical fixture invocations: distinct directories/run IDs, equal semantic outputs. Unknown case and malformed input produce actionable errors.
- Reverse simultaneous input order; account outcomes remain identical under declared tie rules.
- No networking during unit/fixture execution. No golden output generated from the implementation under test is used as an independent oracle.

**M2-01 gate:** steps 1–4, F01–F17, existing R3 suite and Ruff pass; one trace can explain every change in cash, quantity, protection and reservation. Only then rely on this account/execution layer for strategy results.

## 9. Step 5 — trade-event execution adapter (M2-01T)

### Do

Add a separate versioned execution mode such as `subsequent_trade_estimate_v1`; the precise identifier is a new implementation choice to register consistently. Keep conservative bar execution separate. Do not infer the execution model merely from which data files happen to be present.

1. Define activation/readiness and eligible trade-reference semantics in `docs/contracts.md` before coding the adapter. Synthetic numeric delays/quality thresholds are explicit fixture settings; real values remain blocked.
2. Eligible entry or market-exit references must be subsequent to intent creation/readiness and not part of the observations already consumed by that decision. All trades in the decision's same-time batch are already consumed. Replaying one at phase 70 does not make it eligible.
3. A late print that occurred before the order could have been active cannot represent a new opportunity merely because it arrives afterward. Check market-time/activation compatibility as well as receipt/availability order. Validate source, feed, instrument, units, quality and the explicitly declared staleness policy. If actual activation cannot be established, label the modeled assumption and block unsupported cases.
4. Process eligible existing exits before new entries, with deterministic cross-symbol/account ordering. Recheck costs, cash, quantity, stop validity, expiry and session restrictions at the selected reference.
5. Stops/targets become active after entry; the entry reference cannot also close the new position. A subsequent print through a stop uses the adverse print reference and configured costs. Use the declared conservative target reference for a target crossing. Keep trigger/reference/cost fields distinct.
6. Time/flatten requests use the next eligible trade under this adapter, not an invented bar open. With no eligible trade before the required boundary, mark the result incomplete and retain the still-open position.
7. Corrections/cancellations update observations causally; they are not fresh executable trades. Do not undo earlier fills. Retain linkage to corrected evidence and invalidate unsupported execution assumptions explicitly.
8. Quote state remains optional context. It is not a quote-based fill model. Full modeled fills at a print are a declared simplification without order-book/size assurance; reject requests for unsupported partial-fill or queue behavior.

### Test

Create `tests/test_trade_execution.py` with at least:

- T01: trade triggers fixture strategy; no fill on that trade or another already-consumed same-time trade.
- T02: a genuinely subsequent eligible trade fills at its reference plus declared costs; zero and positive latency boundaries differ correctly.
- T03: a delayed pre-activation print cannot fill; an expired intent cannot fill; cancellation releases its reservation.
- T04: stop and target observations in a known sequence use that sequence; bar-mode stop-first ambiguity is not imposed on an observed later trade order.
- T05: no entry-reference self-exit; gap-through stop is adverse; an invalid actual entry stop rejects.
- T06: exits/entries compete for shared cash; priority and settlement policy determine affordability deterministically.
- T07: duplicate trade delivery, a correction and a cancellation produce no duplicate fill/fee.
- T08: stale/wrong-source/wrong-unit input is unusable; missing flatten reference yields non-success.
- T09: future trades/quotes/actions/corrections cannot change earlier account/decision/fill prefixes.
- T10: a fixture-only intrabar strategy emits via the real bounded context, then acceptance and a subsequent event cause a fill. No test-only callback loop supplies missing engine behavior.

**Accept:** `uv run pytest tests/test_trade_execution.py` passes, and an inspectable synthetic trade-input trace proves signal → readiness → later execution reference → fill. Reject trade-input P&L when this adapter is unavailable or its required settings are missing.

## 10. Step 6 — features, historical inputs and scanner (M2-02a)

### Do

Implement `features.py` and `scanner.py` around immutable as-of views. Features return value, units, availability, version/input references and missingness reason. Do not turn unavailable into zero.

1. Add a narrow history interface for prior-session observations, with calendar/source/coverage/action metadata. Load only history available by the current cutoff. The single-session replay CLI's coverage declaration is not a reason to expose an entire multi-session raw array to a strategy.
2. Keep historical preload separate from the active session; validate its as-of availability and feed consistency. Cache keys include data/version, instrument, feature definition, parameters, unit basis and cutoff. A late revision may alter a later computation but cannot mutate a saved feature snapshot.
3. Compute prior-session dollar-volume eligibility under an explicitly named bar/trade approximation from the contract or a registered specification. If the exact dollar-volume formula is absent, record it as a semantic decision and use a labeled fixture method; do not silently substitute a formula into market research.
4. For cumulative RVOL at elapsed minute `m`, select the most recent `L` calendar sessions whose scheduled duration reaches `m`, within the declared search bound. Select dates before examining volumes. Require complete selected-session coverage through `m`.
5. Convert historical volume to current split units. Divide today's cumulative session volume by the arithmetic mean of those selected cumulative histories. Missing selected history or a zero denominator means unavailable; do not skip bad data to find an older convenient replacement.
6. Session gain is latest available completed close divided by previous regular-session close in current split units minus one. Keep known cash-dividend flags without inventing total-return adjustment.
7. Refresh baseline ranks only at declared completed-bar boundaries after same-time publication across symbols. Exclude stale/missing inputs with reasons. Rank by RVOL descending, gain descending, stable instrument identity ascending; take up to `top_n` actual eligible rows without inventing candidates.
8. Preserve state for eligible universe instruments outside top-N so strategy setups can progress. Keep position management independent. A fixed-universe control bypasses ranking only under an explicit separate configuration, not a missing-rank fallback.
9. Persist top-N, counts/reasons, source/feature/config references and the full-ranking digest. Implement the canonical `ranking-json-v1` encoding from `docs/contracts.md`; retain enough pinned inputs to reconstruct the full ranking.

### Test

Create `tests/test_features.py` and `tests/test_scanner.py`:

- H01: morning history selects 2024-12-02 and 2024-11-29; mean `600`, RVOL `2`.
- H02: afternoon history excludes the shortened session; selects 2024-12-02 and 2024-11-27; mean `1000`, RVOL `2`.
- H03: the selected newest session is missing; result stays unavailable, not an older-session replacement.
- DQ08: mixed current/history feeds fail. Also test missing prior close, zero denominator, insufficient calendar history and invalid unit basis.
- F17-style split conversion leaves comparable gain/RVOL consistent and dollar volume invariant under consistent price/volume units.
- Tie ordering and top-N are independent of input permutation. Missing rank rejects a baseline proposal rather than defaulting to zero.
- All same-time symbols enter one ranking snapshot; a missing symbol is explicitly excluded under the age/coverage policy.
- A late revision changes only later feature/ranking versions; saved snapshots and old proposal references remain unchanged.
- Reconstruct a full ranking from pinned inputs and match its digest; deliberately change a ranked value and require a different digest.
- No future daily volume, end-of-day gain or final-session summary influences an intraday rank.

**Accept:** scanner outputs and exclusions can be explained from exact known observations. No strategy-specific private RVOL or ranking implementation duplicates shared feature semantics.

## 11. Step 7 — ORB completed-bar baseline (M2-02b)

### Do

Implement `strategies/orb.py` with strategy ID `orb_close_cross_v1` and the state machine in `docs/strategies/orb.md`.

- Collect the full opening range over the configured half-open window. Missing required bars make that symbol unavailable for the session. Freeze high/low after the range is complete and available.
- The range's last bar is not a breakout bar. For a 09:30 open and 15-minute range, earliest qualifying breakout interval is 09:45–09:46, with a decision only after publication.
- Require a strict close crossing above range high plus buffer, with the preceding completed close at/below the trigger, current candidate membership and a decision before the deadline.
- Emit a proposal, never a quantity/ledger mutation or immediate fill. Stop is range low rounded down. Entry is delegated to the selected execution adapter.
- Target is computed from the actual fill and initial risk, rounded up, not frozen from the signal close. Risk/cost sizing belongs to acceptance.
- Rejection/cancel/expiry requires a later return at/below the trigger and a fresh crossing before another proposal. A filled entry consumes the daily fill allowance. Manage the position through exits even after cutoff or scanner removal.
- Expire at the earlier of entry deadline or second base-bar boundary after the decision. Derive boundaries from the declared calendar/interval; do not simply add two minutes to a delayed publication timestamp. An actual fill at/after the deadline is not allowed.
- Keep ordinary closed-only cadence and the established revision opt-in semantics. Do not silently change this baseline into an intrabar or revision-triggered strategy. Record references used to freeze the range.

### Test

Create `tests/test_orb.py` covering S01/S02 and ORB-01–07:

- S01 gives a decision at 09:46 with range high `101`, trigger `101.01`, stop `99.00`; S02's equality produces no proposal.
- No signal before all range bars are available; delayed publication delays the decision.
- Breakout bar excluded from the range; equality is not strict crossing; no proposal for a noncandidate.
- Pending intent prevents repeated proposals; rejection and fresh-cross rearm; one fill then no re-entry.
- Deadline/expiry boundaries and no eligible open/trade; scanner removal does not cancel an accepted intent by itself.
- Fill price differs from signal close: target and costs use the actual fill.
- Run with both provider-completed and trade-built completed bars. Equal observable bar histories yield the same strategy proposals; execution outcomes may differ by explicitly selected adapter.
- Perturb future prices, corrections, quotes and actions through complete engine runs; earlier range/proposal/account prefixes and re-queried contexts remain identical.

**Accept:** strategy state and every proposal/no-proposal reason are traceable. No extra ATR, premarket or hidden RVOL entry filter is added.

## 12. Step 8 — VWAP touch-and-hold baseline (M2-03)

### Do

Implement `strategies/vwap.py` with strategy ID `vwap_touch_hold_v1`. Do not revive or alias the retired `vwap_touch_reclaim_v1` identifier.

1. Use the exact bar-price proxy `(high + low + close) / 3` and cumulative `sum(proxy × volume) / sum(volume)`, resetting each regular session. Keep it separately named from provider VWAP and trade-level VWAP.
2. Require warmup and a rising comparison against the configured number of **completed** bars ago. A zero-volume bar adds zero weight; zero cumulative volume is unavailable. Missing required bars invalidate the affected setup. Do not invent an automatic recovery rule; use the documented rule or an explicit blocker/conservative fixture policy.
3. Implement WARMUP → IDLE → ARMED → PULLBACK → PROPOSED → IN_POSITION → DONE, including the invalidation/expiry branches.
4. Arming, touch and confirmation are distinct events. The touch bar cannot confirm itself. Pullback age includes touch as 1; confirmation is allowed at ages 2–5 for the specified baseline.
5. Evaluate invalidation before confirmation. At age 5, valid confirmation is evaluated before window expiry. A noncandidate confirmation does not pause the window.
6. Confirmation compares to current VWAP plus buffer and the **previous completed bar's** high plus buffer. Stop uses only lows from touch through confirmation, minus one tick, rounded down.
7. Apply the shared expiry, fill-time risk, target-from-actual-fill, max-hold, flatten and one-fill rules. After rejection/cancel/expiry, return to IDLE and require a new arm and later touch.

### Test

Create `tests/test_vwap.py` covering S03/S04 and VWAP-01–08:

- S03 states: ARMED 09:51, PULLBACK 09:52, PROPOSED 09:53; stop `99.99`; final VWAP equals `230280/2300` within the fixture's `1e-8` tolerance. Money/account assertions remain exact Decimal.
- S04 close below VWAP invalidates before apparent confirmation.
- Independent weighted-average arithmetic, zero-weight bar, zero denominator, session reset and warmup off-by-one boundaries.
- Slope excludes forming/future bars. Previous-high comparison excludes the confirmation bar.
- Same-bar arm/touch/confirm is impossible. Ages 2 and 5 can confirm; age 6 cannot. Non-rising slope and missing inputs invalidate first.
- Noncandidate setup progresses without an order; later candidate status cannot backdate an earlier confirmation.
- Stop includes confirmation low when appropriate but never future lows; rejection requires a new setup.
- Shared engine/account tests remain unchanged and pass. Future mutation leaves full earlier decision/account prefixes invariant.

**Accept:** independently replaceable strategy code produces the specified states/proposals. Changing VWAP does not alter scanner, replay or ledger behavior for ORB.

## 13. Step 9 — complete synthetic run, comparison and reports (M2-04)

### Configuration and orchestration

1. Add a validated end-to-end synthetic configuration path without relaxing `ResearchConfig` blockers or allowing fixture-only methods to satisfy research gates. Prefer an explicitly discriminated schema/profile consistent with the current architecture; document its version and migration. Do not smuggle unvalidated strategy settings through a test-only dictionary.
2. Register supported strategy IDs, execution modes, cost/risk methods, history dependencies and cadence combinations. Any accepted setting must affect behavior or be rejected as unsupported. `execution.data_basis: inactive` remains data-only replay.
3. Connect the public CLI and shared engine: universe/history → replay → positions/features → scanner → strategies → proposal → acceptance → execution → results. Log the shared snapshot/rank used for each decision. Avoid a second private path used only by tests.
4. Compose bounded sessions in chronological order for synthetic multi-day verification. Each active session must have its own coverage/calendar validation. Account cash/proceeds can persist; session features, fills-per-day, daily limits and timers reset only under their declared rules. Do not weaken the single-session replay validator to accept unexplained multi-day input.
5. Add small, explicit synthetic configurations such as `configs/m2_orb_bar_fixture.yaml`, `m2_vwap_bar_fixture.yaml`, and `m2_trade_fixture.yaml` (new proposed filenames). These pin complete test assumptions and input references. They are not approved market baselines.

### Run evidence and report content

Implement `evaluation.py` and `reporting.py` with JSON plus a portable HTML report. Reuse lifecycle/storage/fingerprint/redaction behavior. No external network assets are required to open a report.

Retain at minimum:

- Run ID/status, hypothesis/phase, synthetic evidence label, code/config/input/lock/rule/strategy hashes and all execution/account assumptions.
- Source events/publications and coverage; decision cutoffs and exact consumed versions; feature/rank snapshot references and exclusions.
- Every strategy evaluation, including no proposal; proposals, acceptance/rejection reason, reservation, order transitions, fills and canceled protection.
- Ledger transitions, equity/mark series with availability and units, fees/costs, completed trades and open/incomplete positions.
- Timing/reference details sufficient to trace decision → readiness → execution reference → fill → ledger effect.
- Structured errors and partial trace for failed/interrupted/incomplete attempts where storage remains writable.

Calculate independent reconciled metrics: starting/ending cash and marked equity, external flows, net account change/return under a declared flow convention, realized/unrealized P&L, costs, drawdown, trade counts, expectancy, win/loss statistics, profit factor, turnover and exposure. Define units and sample basis. Do not silently treat unavailable metrics as zero.

For zero trades, win rate/expectancy are unavailable with a reason. For no losses, profit factor needs an explicit nonfinite/not-defined state rather than invalid JSON `Infinity`; encode the reason. If time precision or mark coverage is inadequate for an exposure/holding-time metric, label the interval resolution or missingness. Synthetic promotion status remains unscored; a successful software run is not strategy approval.

Report measured runtime, peak memory where available, trace bytes and record counts on a declared bounded synthetic sample. Keep scaling claims scoped to that measurement; M2 does not establish a streaming/bounded-memory market-scale system.

### CLI and comparison

Implement the existing target interfaces as real behavior:

```bash
uv run rsys run --config configs/m2_orb_bar_fixture.yaml --out <new-empty-directory>
uv run rsys compare --runs <RUN_ID_A> <RUN_ID_B> --out <new-empty-directory>
```

Angle-bracket arguments are placeholders. Document how `compare` resolves IDs/storage roots. Do not implement a command that parses arguments and returns success without producing its required artifacts.

Comparison must reject unexplained differences in dates/windows, eligible universe, data/feed/adjustment/availability, capital/cash profile, costs, latency, risk/sizing, execution mode or required coverage. Identify the deliberately changed component and hypothesis. Require common opportunity/cost/account assumptions when isolating an entry rule; compare scanners and exits separately. A diagnostic comparison of different execution/data models must label those differences and cannot attribute P&L change solely to a strategy edge.

Use the union of known attempts under the comparison protocol, including failed/zero-trade runs; do not hide inconvenient attempts or overwrite reruns. Reject or separately label incomplete runs rather than calculating a winner from partial evidence. A checksum match is not sufficient to establish comparability.

### Holdout boundary

- Normal `run`, `compare`, report helpers and history/data loaders must not open unauthorized final-holdout observations, including as warmup or through a config/path alias. Enforce the boundary before content is loaded, not only after reading returns.
- Implement manifest/schema validation, hash/frozen-candidate checks and access-log behavior as testable contracts. Research `final-evaluate` remains blocked while the required D16/D17 decisions and owner-approved scope are absent; a block must never look like a successful evaluation.
- Do not implement a permissive final runner as an M2 shortcut. A guarded command may return exit 5 with the missing requirements; document that final evaluation is not delivered. Any future authorized implementation must consume a frozen manifest and log actual access.
- Use synthetic segregated partitions to test access denial and admissible development loading. No real final data is needed to test this boundary.

### End-to-end tests

Create `tests/test_research_path.py`, `tests/test_reporting.py`, `tests/test_comparison.py` and `tests/test_holdout_boundary.py`:

1. Public CLI runs ORB and VWAP separately on small synthetic histories; a real proposal becomes an accepted/reserved intent, then a modeled fill, protected position, exit and reconciled account result.
2. At least one entry is rejected, one expires, two instruments compete for cash, one held symbol leaves candidates, and one no-trade run produces valid reports.
3. Provider-bar and trade-built completed-bar paths are both exercised. The trade-input P&L path uses M2-01T; a fixture intrabar entry is visibly labeled and uses a later execution reference.
4. Multi-session test proves as-of historical warmup, calendar changes, correct session reset and account continuity; no future session is visible early.
5. Expected metric calculations use independently specified small traces. Reconcile starting equity plus flows and net gains/losses to final equity without double-counted costs.
6. Same config twice: distinct persisted attempt IDs/directories, identical semantic decision/order/fill/account outputs. Changed provider prices/costs must change relevant evidence and results.
7. Malformed input, missing history/exit event, unsupported action and injected engine exception each retain a non-success attempt and usable diagnostic evidence.
8. Comparison rejects one changed invariant at a time; an explicitly declared permitted variant reports the difference honestly.
9. Holdout denial occurs before data access. Monkeypatch the data-read operation to fail the test if called on a forbidden partition, including warmup/alias requests.
10. Open the generated HTML locally and inspect representative winning, losing, no-trade and incomplete examples. Verify tables/labels/links match JSON and no value is silently truncated or absent. Do not require a browser to test core logic.

**Accept:** one can reconstruct why each decision happened, why each fill was eligible, and how every dollar changed by following the saved artifacts. This completes synthetic M2, not real-market validation.

## 14. Causality acceptance matrix for the integrated engine

Run these through the actual engine and public CLI where appropriate. Existing R3 data-only tests remain necessary; adding strategies and execution creates new boundaries they cannot prove by themselves.

Build two complete runs A and B with identical observable history through cutoff K and deliberately different later information. Compare the ordered semantic prefix through K **after both runs finish**, then re-query saved contexts and snapshots. Compare feature values, states, proposals, acceptance, reservations, fills and account snapshots, not merely copied bar values.

Exclude run IDs/wall time and whole-input provenance hashes from prefix equality: changing the future correctly changes the whole-input hash. Do not exclude the consumed input-version references or decision/fill semantics that would reveal a leak. Deterministic IDs for earlier decisions must not depend on an entire future stream fingerprint.

| Case | Mutation/scenario | Required result |
|---|---|---|
| C01 | Change future bar/trade price and volume | Earlier features, decisions, fills and balances unchanged |
| C02 | Append later correction/cancellation/revision | Earlier versions/decisions unchanged; later effects explicit |
| C03 | Append future quote or make it stale/bad | Earlier quote access unchanged; unusable quotes never silently fill |
| C04 | Delayed closure timer/action | No backdated publication, account conversion or fill |
| C05 | Reuse context after entire replay | Same values, references, missingness and cutoff |
| C06 | Shuffle delivery list preserving event metadata | Same semantic outputs under the declared stable ordering |
| C07 | Multiple symbols, equal times | Complete batch ranking; deterministic cash allocation |
| C08 | Same-batch trade triggers a proposal | No trade already consumed by that decision can fill it |
| C09 | Positive latency crosses next open | Skip that open; no retroactive readiness |
| C10 | Quiet/missing minute and missing flatten reference | No fabricated price; coverage/error behavior explicit |
| C11 | Remove scanner membership after entry | Protection/timers continue; no lost position |
| C12 | Future split or unsupported late unit transition | No earlier changes; affected evidence explicitly invalid/non-success |
| C13 | Later higher volume/order affordability | Cannot change earlier size or free cash before availability |
| C14 | Repeated event/proposal/fill IDs | No duplicate monetary effect; conflicting payload fails |
| C15 | Intentionally leaky strategy/test adapter | The prefix harness detects the difference (negative control) |

A negative-control test passes only when it demonstrates that the harness rejects the introduced leak. No skipped/xfail causality requirement counts as completion. A passing synthetic matrix establishes the specified software behavior; do not claim a mathematical guarantee for all possible strategies/streams.

## 15. Validation commands and evidence

The following new test files/commands are targets to implement, not claims that they exist at the starting commit. Run only targets belonging to the current slice, then integrate. Keep unit/fixture tests offline and deterministic.

```bash
# M2-01
uv run pytest tests/test_order_contracts.py tests/test_engine_order.py
uv run pytest tests/test_portfolio.py tests/test_sizing.py tests/test_acceptance.py
uv run pytest tests/test_fixture_cases.py tests/test_fixture_cli.py
uv run rsys fixture --case all --out auto

# M2-01T
uv run pytest tests/test_trade_execution.py

# M2-02 / M2-03
uv run pytest tests/test_features.py tests/test_scanner.py tests/test_orb.py
uv run pytest tests/test_vwap.py tests/test_fixture_cases.py

# M2-04
uv run pytest tests/test_research_path.py tests/test_reporting.py tests/test_comparison.py tests/test_holdout_boundary.py

# Full integration
uv run pytest tests/ -q
uv run ruff check .
```

Use temporary directories in tests; CLI evidence runs intended for inspection must remain available. Run each new synthetic end-to-end config twice and retain the distinct attempts and semantic-hash comparison. Re-run the four R3 replay examples after the engine/CLI integration changes.

Acceptance tests should assert behavior using hand calculations, small independent reference functions or public trace comparisons. Do not mirror the production formula line-for-line and call that independent validation. Do not add a large brittle test count as a substitute for the specific cases above.

If test discovery or environment tools differ, document the actual equivalent command. Do not report a planned command as executed. Required checks that could not run remain explicitly unverified.

## 16. Completion checklist and agent report

### M2-01

- [ ] Repaired baseline preserved; phase-aware runtime and bounded strategy/account views tested.
- [ ] Decimal ledger, cost-aware sizing, reservations and declared cash behavior implemented.
- [ ] Conservative bar execution, position-independent management, timers and supported split conversion implemented.
- [ ] Every F01–F17 checkpoint passes through the same production path used by the CLI.
- [ ] Fixture CLI retains both passing and failed attempts with correct exit codes.

### Full synthetic M2

- [ ] Subsequent-trade execution tested before trade-input P&L; intrabar capability remains available.
- [ ] H01–H03/DQ08, ranking reconstruction and scanner-removal behavior pass.
- [ ] ORB-01–07/S01–S02 and VWAP-01–08/S03–S04 pass without strategy-definition drift.
- [ ] Public end-to-end runs, metrics, comparison rejection and reports are implemented and inspectable.
- [ ] Full decision/account prefix invariance and saved-context reuse pass with a leaky negative control.
- [ ] Missing data, unsupported units and failed execution retain explicit non-success evidence.
- [ ] No required acceptance check is skipped or replaced by a helper-only assertion.
- [ ] Existing suite and Ruff pass; repeated runs retain distinct attempts with stable semantic outputs.
- [ ] Documentation states delivered capabilities and limits; D09–D13/D16–D17/provider-facing D22 remain correctly classified.
- [ ] Real data, paper/live execution, M3 controls/statistics and final evaluation remain separately scoped.

Update `docs/requirements.md`, `docs/tickets.md`, README and the implementation status report as behavior lands. Do not mark all of M2 complete after only the fixture ledger works. Do not mark an open decision resolved merely because a synthetic profile supplies a value.

The agent's final report must include:

1. Starting/final source identity and whether any commits/pushes occurred.
2. Changed files grouped by ticket and a brief explanation of implemented behavior.
3. Requirement → test mapping, including F01–F17, strategy IDs and causality cases.
4. Exact executed commands/results, environment limitations and required checks not run.
5. Paths/run IDs/hashes for retained fixture and end-to-end evidence, including a representative failure.
6. Reconciliation example showing decision cutoff → accepted size/reservation → eligible fill → account balance.
7. Open assumptions/blockers and a truthful status: M2-01 complete, partial M2, or full synthetic M2 complete.
8. The next dependency. After synthetic M2, provider/data feasibility and research-design work follow the existing roadmap; no profitability or promotion claim follows from fixture success.

## 17. Copy-ready Copilot prompt

```text
Implement docs/M2_IMPLEMENTATION_HANDOFF.md in intraday-rsys. If it is attached,
place it at that path first. This is an implementation request: make the code
changes, add meaningful tests, run the checks and update documentation.

Read AGENTS.md and the referenced contracts, decisions, fixtures and strategy
specifications. The handoff was grounded in fix/r3-replay-causality at
2cd1e4a3173b6ea923f0a5ecbd93166af181cc93; develop was still older. Verify the
actual checkout, preserve correct newer work and unrelated changes, and use the
repaired baseline or a verified descendant. Do not reset to the old develop.

Work through steps 0–9 one reviewable slice at a time: contracts/phase-aware
runtime; Decimal ledger, acceptance and reservations; conservative bar fills,
positions/timers/splits; F01–F17 fixture CLI; subsequent-trade execution;
causal features/scanner; exact ORB; exact VWAP touch-and-hold; synthetic
run/compare/report and holdout protection. Finish M2-01 before relying on its
account layer, and finish M2-01T before emitting trade-input P&L.

Preserve the core workflow and repaired no-lookahead behavior. Strategies use
frozen bounded contexts and read-only account snapshots. No close self-fills,
same-consumed-trade fills, future entry-bar volume, backdated cash, retrospective
stop changes or position management limited to scanner candidates. Extend the
phase scheduler deliberately; do not drain future execution phases before the
decision phase. Keep forming-bar capability and prove its order path using a
separately identified fixture-only intrabar strategy. ORB and VWAP remain their
specified completed-bar baselines.

Run each acceptance through real public engine/CLI paths. Do not copy expected
fixture values into execution logic or make a test-only loop provide missing
runtime behavior. Compare full completed-run prefixes after changing future
events, then re-query saved contexts. Include a deliberately leaky negative
control. Use independent hand calculations for account and feature expectations.

Keep everything offline and synthetic. Do not invent unresolved market risk,
cost, account, provider, strategy, date-split or promotion assumptions. Use
explicit test profiles, retain research blockers, record genuine semantic
questions and continue independent work. Do not fetch market data, contact a
broker, implement an optimizer/M3, or access real final-holdout data.

Retain all attempts and failure evidence. Keep exact Decimal money, nanosecond
keys, deterministic ordering, source/unit identity and existing profile
isolation. Run targeted checks during implementation, then the full suite and
Ruff; repeat synthetic CLI configurations and verify distinct retained attempts
with identical semantic outputs. Report unavailable checks honestly.

Make routine engineering choices independently. Continue through the assigned
synthetic M2 scope until the acceptance gates pass or a genuine dependency
blocks work. Do not commit, push or merge unless separately requested.

Finish with changed files by ticket, requirement-to-test mappings, exact
commands/results, retained artifact paths/run IDs/hashes, an account
reconciliation example, open blockers, and truthful milestone status. Test
count alone is not proof of completion.
```

To assign only the first milestone, replace the prompt's scope with: “Implement steps 0–4 only (M2-01); use the remaining sections as interface constraints and stop before M2-01T/scanner/strategies.” This narrows delivery without removing account/causality requirements.

## 18. Source anchors

All repository references below are pinned to the source inspected for this handoff. Re-read the actual checkout before implementation.

- [Repaired source](https://github.com/igbodani/intraday-rsys/tree/2cd1e4a3173b6ea923f0a5ecbd93166af181cc93)
- [Agent rules](https://github.com/igbodani/intraday-rsys/blob/2cd1e4a3173b6ea923f0a5ecbd93166af181cc93/AGENTS.md)
- [Decision register](https://github.com/igbodani/intraday-rsys/blob/2cd1e4a3173b6ea923f0a5ecbd93166af181cc93/docs/decisions.md)
- [Contracts](https://github.com/igbodani/intraday-rsys/blob/2cd1e4a3173b6ea923f0a5ecbd93166af181cc93/docs/contracts.md)
- [Tickets](https://github.com/igbodani/intraday-rsys/blob/2cd1e4a3173b6ea923f0a5ecbd93166af181cc93/docs/tickets.md)
- [R3 repair evidence and limits](https://github.com/igbodani/intraday-rsys/blob/2cd1e4a3173b6ea923f0a5ecbd93166af181cc93/docs/R3_REPAIR_RESULTS.md)
- [F01–F17 inputs and expectations](https://github.com/igbodani/intraday-rsys/blob/2cd1e4a3173b6ea923f0a5ecbd93166af181cc93/fixtures/cases.json)
- [ORB specification](https://github.com/igbodani/intraday-rsys/blob/2cd1e4a3173b6ea923f0a5ecbd93166af181cc93/docs/strategies/orb.md)
- [VWAP specification](https://github.com/igbodani/intraday-rsys/blob/2cd1e4a3173b6ea923f0a5ecbd93166af181cc93/docs/strategies/vwap.md)
- [Research/holdout protocol](https://github.com/igbodani/intraday-rsys/blob/2cd1e4a3173b6ea923f0a5ecbd93166af181cc93/docs/experiments.md)

This document was created without modifying or pushing project source code.

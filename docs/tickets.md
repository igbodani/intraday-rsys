# M0–M2 implementation tickets

Each row is one reviewable task. Build only after the owner requests implementation. Paths and commands below are proposed interfaces to create, not existing code.

**Current R3 repair status (2026-10-29):** the earlier 348/381-test completion summaries are superseded by `R3_REPAIR_RESULTS.md`. The delayed-event visibility leaks, missing internal closures, observer cadence, permissive policies, public quote freshness and weak acceptance harness have been repaired. The current suite has 426 passing tests. Real CLI fixtures cover provider bars, trade-built bars and named synthetic observers. M2 remains unimplemented; the next implementation ticket is M2-01, with its existing fixture/Decimal/position-management obligations unchanged. Provider verification and open market decisions are still blockers for market research.

**M2-01 partial progress (2026-10-29):** `src/rsys/orders.py`, `portfolio.py`, `sizing.py` and a standalone `fixture_engine.py`/`fixture_cli.py` now make all 17 `fixtures/cases.json` checkpoints pass through real code via `uv run rsys fixture --case all --out auto` (459 tests total, Ruff clean). This is a fixture-only, single-case interpreter, **not** the phase-aware `ReplayRunner` extension M2-01a calls for; see `docs/M2_IMPLEMENTATION_STATUS.md` for the exact gap list before marking M2-01 complete.

**Ticket-order reconciliation (2026-10-28):** `docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md` supersedes this file's immediate next-task ordering after M1-01. It splits the former single `M1-02` row into `D1` (extend M1-01's data contracts: typed market events, provider bar/trade/quote envelopes, universe-coverage manifest) and `R1`/`R2`/`R3` (replay clock and views; trade-driven incremental bar builder with revisions; wiring and provider-bar parity). It also inserts `H1`/`H2` hardening passes on top of the already-built M0-01/M0-02 (closing gaps an independent review found after the fact) and a new `M2-01T` trade-event execution adapter. Product goals, existing requirement IDs, strategy definitions (ORB/VWAP stay completed-bar decisions), and the M2/M3 research sequence below are unchanged. See that document for full rationale, the trade-driven-bar requirement, and provider-fact citations.

| Ticket | Inputs → deliverable | Files primarily affected | Acceptance / completion command |
|---|---|---|---|
| M0-01 Config and decisions | Register/contracts → typed config, registry, explicit blockers and profile isolation | `src/rsys/config.py`, `registry.py`, `cli.py`, `tests/test_config.py` | Draft market config exits 2 with every unresolved field; fixture config passes. `uv run pytest tests/test_config.py` |
| H1 Config hardening (on M0-01) | Independent review gaps → implemented-only schema versions, bool-safe numerics, offset-free local times, partial-pair date checks, versioned gate-method references, safe decoding/error messages | `src/rsys/config.py`, `registry.py`, `tests/test_config.py` | Regressions in `IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md` section 4 pass; blocker set stays exact and justified, not a target count. `uv run pytest tests/test_config.py` |
| M0-02 Run evidence | Record example → unique run IDs, resolved settings, hashes, status transitions and atomic artifacts | `src/rsys/storage.py`, `experiments.py`, `tests/test_run_record.py` | Failed run retained; duplicate attempt does not overwrite; secrets excluded. `uv run pytest tests/test_run_record.py` |
| H2 Run-evidence hardening (on M0-02) | Independent review gaps → content-hash provenance, transactional consistency, interrupt/recovery ownership, credential boundaries, safe run-ID paths | `src/rsys/experiments.py`, `storage.py`, `tests/test_run_record.py` | Regressions in `IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md` section 5 pass. `uv run pytest tests/test_run_record.py` |
| M1-01 Data and calendar | Synthetic events → normalized bars, instrument/session contracts and data validation | `src/rsys/data/`, `calendar.py`, `tests/test_data_contract.py`, `test_calendar.py` | TZ01–TZ04 and DQ01–DQ07; calendar, split/action contracts and future-action checks; invalid bars/gaps visible. `uv run pytest tests/test_data_contract.py tests/test_calendar.py` |
| D1 Typed market events and coverage (extends M1-01) | Immutable event envelope (trade/quote/provider-bar/action/timer), `NormalizedBar`, DATA-002 universe/coverage manifest | `src/rsys/data/events.py`, `bars.py` (extend), `manifest.py`, `tests/test_market_events.py`, `test_universe_manifest.py` | Nanosecond-safe timestamps, strict TZ/interval/identity checks, late-split invalidation, universe manifest. `uv run pytest tests/test_data_contract.py tests/test_calendar.py tests/test_market_events.py tests/test_universe_manifest.py` |
| R1 Replay clock and views | Typed events → single availability clock, versioned phases, bounded immutable as-of views, same-time batching | `src/rsys/replay.py`, `views.py`, `tests/test_event_order.py`, `test_causality.py` | Prefix invariance and input-order invariance; no future access; existing `bar_v1` phase contract intact. `uv run pytest tests/test_event_order.py tests/test_causality.py` |
| R2 Trade-driven bar builder | Trade fixtures + provider rule table → forming/closed/revised bar snapshots, correction/cancel recomputation | `src/rsys/data/bar_builder.py`, `bar_rules.py`, `providers/alpaca_rules.py`, `tests/test_bar_builder.py`, `test_bar_rules.py`, `test_bar_revisions.py` | BAR-001…012 (section 8 of the replay plan). `uv run pytest tests/test_bar_builder.py tests/test_bar_rules.py tests/test_bar_revisions.py` |
| R3 Replay wiring, quotes, provider parity | R1 + R2 → one authoritative bar source per series, optional quote state, provider-bar comparison | Replay/view integration, `src/rsys/data/quotes.py`, `tests/test_replay_inputs.py`, `test_quote_state.py`, `test_provider_bar_parity.py` | REPLAY-001…005, QUOTE-001…002; both completed-bar and trade-built-bar profiles pass. `uv run pytest tests/test_replay_inputs.py tests/test_quote_state.py tests/test_provider_bar_parity.py` |
| M2-01 Account and execution | F01–F17 → ledger, sizing/reservations, basic entries/protection/timers | `src/rsys/portfolio.py`, `execution.py`, `positions.py`, `tests/test_fixture_cases.py` | All checkpoints match Decimal expectations; removed candidates still managed. `uv run rsys fixture --case all --out auto` |
| M2-01T Trade-event execution adapter | Trade-input runs → subsequent-eligible-trade fills, exit-before-entry priority, cash checks | `src/rsys/execution.py` (extend) | Signal-event self-fill rejected; late prints, expired orders, stops/targets, insufficient cash covered | (completion command to be set by that ticket) |
| M2-02 Scanner and ORB | Scanner contract + ORB spec → shared features, ranking and first strategy | `src/rsys/features.py`, `scanner.py`, `strategies/orb.py`, `tests/test_scanner.py`, `test_orb.py` | H01–H03, DQ08; exact RVOL/ties/history; reconstructed ranking digest; ORB-01…07 and S01/S02. `uv run pytest tests/test_scanner.py tests/test_orb.py` |
| M2-03 VWAP variant | Shared contracts + VWAP spec → second independently replaceable strategy | `src/rsys/strategies/vwap.py`, `tests/test_vwap.py` | VWAP-01…08 plus S03/S04 for touch-and-hold; no changes to unrelated replay/account behavior. `uv run pytest tests/test_vwap.py tests/test_fixture_cases.py` |
| M2-04 First complete research path | Both strategies + evidence/contracts → run/compare/report path and protected final boundary | `src/rsys/evaluation.py`, `reporting.py`, CLI, `tests/test_research_path.py` | End-to-end synthetic run, fair comparison rejection, report labels, full trace, repeatability. `uv run pytest tests/test_research_path.py` |

## Milestone completion

- M0: configuration and evidence checks pass (including H1/H2 hardening); exact core dependencies locked after compatibility checks.
- M1: data/calendar, typed-event/coverage (D1), and future-perturbation checks pass with no provider dependency.
- Replay (R1–R3): both the completed-bar and trade-built-bar profiles pass their declared contract before M2 strategy work depends on them.
- M2: F01–F17 and both strategy suites pass; one end-to-end synthetic run can be inspected and reproduced.
- Run targeted checks first, then the full core suite once when integrating the milestone: `uv run pytest tests/` and `uv run ruff check .`. A failing or skipped required check keeps the milestone incomplete. Do not run nonexistent later-ticket targets and call the resulting skips completion.

## Early feasibility task (independent)

Audit current Alpaca feed access, historical coverage, instrument identifiers/inactive symbols, adjustment modes and calendar fields using a tiny declared development sample. Record limitations and estimated storage/cost. Use no final-holdout outcome data. Keep downloads/integration checks outside unit tests. Report evidence rather than asserting a historical-universe capability from current listings. Resolve D12–D13 before general-market performance claims.

## Later work

Expanded strategies, model integration, Nautilus comparison and paper operations follow separate tickets. Parameter optimization starts only after splits, budget and gates are frozen. Do not build an optimizer to compensate for failing correctness checks.


## Research tickets to specify before M3 implementation

These are scoped follow-up tickets; their fixtures and statistical choices remain prerequisites, not completed work.

| Ticket | Required design and inputs | Acceptance fixture required before implementation |
|---|---|---|
| M3-01 Eligible-universe controls | Freeze universe/candidate timing, common costs/capital and baseline policy | A stock first selected at 10:00 cannot be bought at 09:30; identical opportunity sets when comparing only entry rules |
| M3-02 Matched random entries | Specify the eligible sampling population, matching variables, seeds and number of replicates without future outcomes | A small known eligible list gives reproducible samples; forbidden symbols/times never selected; insufficient matches reported |
| M3-03 Full-window exit study | Fix entries and complete observation windows, then rerun each exit and the shared-cash portfolio | One policy exits early while another later profits/loses; both use the same raw path; freed cash changes a later account opportunity correctly |
| M3-04 Uncertainty and promotion | Freeze D16–D17, dependence-aware resampling design, trial accounting and failure/inconclusive rules | Hand-calculated block-index aggregation with correlated symbols/loss days; inadequate sample is inconclusive; final holdout cannot tune the method |

Do not expand M3 into an optimizer before these controlled comparisons are specified and tested. M0–M2 can progress on explicit synthetic inputs while market gates remain unresolved.

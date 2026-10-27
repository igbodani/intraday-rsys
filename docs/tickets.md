# M0–M2 implementation tickets

Each row is one reviewable task. Build only after the owner requests implementation. Paths and commands below are proposed interfaces to create, not existing code.

| Ticket | Inputs → deliverable | Files primarily affected | Acceptance / completion command |
|---|---|---|---|
| M0-01 Config and decisions | Register/contracts → typed config, registry, explicit blockers and profile isolation | `src/rsys/config.py`, `registry.py`, `cli.py`, `tests/test_config.py` | Draft market config exits 2 with every unresolved field; fixture config passes. `uv run pytest tests/test_config.py` |
| M0-02 Run evidence | Record example → unique run IDs, resolved settings, hashes, status transitions and atomic artifacts | `src/rsys/storage.py`, `experiments.py`, `tests/test_run_record.py` | Failed run retained; duplicate attempt does not overwrite; secrets excluded. `uv run pytest tests/test_run_record.py` |
| M1-01 Data and calendar | Synthetic events → normalized bars, instrument/session contracts and data validation | `src/rsys/data/`, `calendar.py`, `tests/test_data_contract.py`, `test_calendar.py` | TZ01–TZ04 and DQ01–DQ07; calendar, split/action contracts and future-action checks; invalid bars/gaps visible. `uv run pytest tests/test_data_contract.py tests/test_calendar.py` |
| M1-02 Replay and as-of views | Validated events → single clock, phases, bounded views and same-time batching | `src/rsys/replay.py`, `views.py`, `tests/test_event_order.py`, `test_causality.py` | Prefix invariance and input-order invariance; no future access. `uv run pytest tests/test_event_order.py tests/test_causality.py` |
| M2-01 Account and execution | F01–F17 → ledger, sizing/reservations, basic entries/protection/timers | `src/rsys/portfolio.py`, `execution.py`, `positions.py`, `tests/test_fixture_cases.py` | All checkpoints match Decimal expectations; removed candidates still managed. `uv run rsys fixture --case all --out auto` |
| M2-02 Scanner and ORB | Scanner contract + ORB spec → shared features, ranking and first strategy | `src/rsys/features.py`, `scanner.py`, `strategies/orb.py`, `tests/test_scanner.py`, `test_orb.py` | H01–H03, DQ08; exact RVOL/ties/history; reconstructed ranking digest; ORB-01…07 and S01/S02. `uv run pytest tests/test_scanner.py tests/test_orb.py` |
| M2-03 VWAP variant | Shared contracts + VWAP spec → second independently replaceable strategy | `src/rsys/strategies/vwap.py`, `tests/test_vwap.py` | VWAP-01…08 plus S03/S04 for touch-and-hold; no changes to unrelated replay/account behavior. `uv run pytest tests/test_vwap.py tests/test_fixture_cases.py` |
| M2-04 First complete research path | Both strategies + evidence/contracts → run/compare/report path and protected final boundary | `src/rsys/evaluation.py`, `reporting.py`, CLI, `tests/test_research_path.py` | End-to-end synthetic run, fair comparison rejection, report labels, full trace, repeatability. `uv run pytest tests/test_research_path.py` |

## Milestone completion

- M0: configuration and evidence checks pass; exact core dependencies locked after compatibility checks.
- M1: data/calendar and future-perturbation checks pass with no provider dependency.
- M2: F01–F17 and both strategy suites pass; one end-to-end synthetic run can be inspected and reproduced.
- Run targeted checks first, then the full core suite once when integrating the milestone: `uv run pytest tests/` and `uv run ruff check .`. A failing or skipped required check keeps the milestone incomplete.

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

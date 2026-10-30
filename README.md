# intraday-rsys

Offline, synthetic-first implementation of the intraday research system governed by `AGENTS.md` and `docs/`. No market data acquisition, broker access or trading is implemented.

Start with [the package and model map](docs/ARCHITECTURE.md) and [the documentation index](docs/README.md). Each functional package owns a `models.py`; runtime services live beside it.

For agent-assisted development, use [the Copilot workflow](docs/COPILOT_WORKFLOW.md): four specialist agents, project/path instructions, reusable skills, task prompts and automated configuration/test checks.

## Current behavior

- Configuration: isolated fixture, research and replay profiles; unresolved research decisions remain named blockers.
- Run evidence: transactional SQLite attempts, immutable resolved inputs, provenance hashes, failure retention, crash recovery and credential redaction.
- Data: typed nanosecond events, quality checks, coverage manifests, and a declared 2024 NYSE calendar with DST and early closes.
- Replay: availability-ordered events, internally scheduled minute closures, immutable historical contexts, trade-built forming/closed/revised bars, optional quote state and an offline parity comparator.
- Observations: completed-bar callbacks on new completed publications; separately opted-in intrabar callbacks after each availability batch. Two named synthetic observers can run through the CLI and record the versions they read, including evaluations that produce no decision.

- Synthetic account sessions: scanner ranking, ORB/VWAP, owned cash reservations, fill-time sizing, entry-interval protection, independent timers, net metrics, guarded comparison and portable reports. The bar session runner now uses ReplayRunner-backed source views and the shared phase clock.
- Public account execution: version-2 `rsys run` configuration runs several bar-input or trade-input sessions with fresh daily strategies, persistent equity and risk state, complete retained inputs/fills, partial failure evidence, metrics and reports. Trade decisions run at phase 40, exits at 60 and rank-ordered entries at 70.
- Explicit policies: current-equity sizing, daily-loss and account-drawdown entry halts, adverse execution costs, fixed fees, feed delay and decision latency. Halts release pending reservations while protective exits continue. Fixture-backed historical scanner inputs remain a Python API.

Read [PUBLIC_SESSION_RUNS.md](docs/PUBLIC_SESSION_RUNS.md) for the new interface, policy semantics and retained evidence, and [CURRENT_SYSTEM_AND_NEXT_STEPS.md](docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md) for the module/test inventory and Copilot work order. [DEVELOP_REPAIR_RESULTS.md](docs/DEVELOP_REPAIR_RESULTS.md), [M2_REPAIR_RESULTS.md](docs/M2_REPAIR_RESULTS.md) and [R3_REPAIR_RESULTS.md](docs/R3_REPAIR_RESULTS.md) retain the repair history.

## Run locally

Python 3.12 or newer and `uv` are required. Recreate your environment locally; do not reuse a virtual environment copied from another operating system.

```bash
uv sync --frozen --group dev
uv run rsys validate --config configs/fixture.yaml
uv run rsys validate --config configs/baseline_pending.yaml  # expected exit 2: unresolved research decisions
uv run rsys replay --config configs/replay_bar_fixture.yaml --out auto
uv run rsys replay --config configs/replay_trade_fixture.yaml --out auto
uv run rsys replay --config configs/replay_intrabar_observer_fixture.yaml --out auto
uv run rsys replay --config configs/replay_closed_observer_fixture.yaml --out auto
uv run rsys fixture --case all --out runs/fixture
uv run rsys run --config fixtures/standalone_run_fixture.json --out runs/orb --report
uv run rsys run --config fixtures/standalone_vwap_fixture.json --out runs/vwap --report
uv run rsys run --config fixtures/sessions_bar_fixture.json --out runs/sessions-bar --report
uv run rsys run --config fixtures/sessions_trade_fixture.json --out runs/sessions-trade --report
uv run pytest tests/ -q
uv run ruff check .
```

The verified suite contains **774 passing tests**, including 11 Copilot-configuration checks. Replay input paths resolve relative to the config file. `--out auto` uses `<config parent>/../runs/replay/<config stem>`; each invocation creates a fresh attempt under `attempts/<run_id>/`, with its own trace and manifest. An explicit output root may be reused to accumulate attempts without overwriting them. Repeated identical inputs have distinct run IDs and identical semantic trace hashes.

## Replay limits and configuration migration

The `rsys replay` CLI supports one declared regular session in the 2024 calendar, one-minute intervals, raw units and synthetic aggregation/quote policies. Unknown rules, unsupported intervals/unit policies, incompatible profiles and invalid coverage dates fail validation. `recorded_receipt` requires receipt timestamps; `modeled_availability` requires an explicit nonnegative `modeled_delay_ms`. Receipt timestamps in the supplied fixtures are synthetic examples, not captured market traffic.

Coverage now declares `window_start_at`, `required_boundary_at`, `available_through_at`, provider, feed, instruments and calendar version. Input JSON separately declares the expected event inventory and observed/no-trade/missing-delivery state for each requested minute. A timer alone never establishes completeness. A verified quiet minute does not create a fabricated flat bar. See the supplied configs and [contracts](docs/contracts.md) for the schema.

Late trades, corrections and actions may change later observations. They cannot change a previously saved context or publish a new version into the past. Unsupported split-straddled bars are invalidated and the run is retained as non-success. This is a conservative rejection, not a complete cross-split feature/position conversion implementation.

Quote freshness is measured from availability at the decision clock under the declared synthetic policy. Stale, invalid, crossed, locked or one-sided quotes are unavailable through `latest_quote()`; diagnostic state remains accessible. Quotes never contribute trade volume or create trade bars. No real feed policy or entitlement has been verified.

## Current limits and next ticket

The account/session layer is a tested synthetic subset of M2. Public trade/multi-session execution and explicit research risk/cost policies now exist. Next, complete dated/as-of historical loading and loader-level holdout protection. Historical scanner helpers still consume caller-prepared same-minute observations; no audited provider history exists. The detailed gates are in [CURRENT_SYSTEM_AND_NEXT_STEPS.md](docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md).

`fixture`, `run` and `compare` execute real software paths. `final-evaluate` is deliberately blocked with exit 5. `rsys replay` remains data-only; `rsys run` accepts legacy synthetic bars or the new explicit bar/trade session schema. Version-2 composition requires flat intermediate sessions; a final open snapshot must be explicitly allowed and remains diagnostic. No real data acquisition, broker account, historical-performance evidence, optimizer, paper/live trading or final evaluation is implemented. D09/D12/D22 remain Open; D13/D16/D17 are Proposed. D10/D11 are enforced when explicitly configured through the version-2 synthetic interface; the pending research YAML and historical experiment protocol remain blocked.

# intraday-rsys

Implementation of the intraday strategy research system. `AGENTS.md` and `docs/` (contracts, decisions, tickets, strategy specs) are copied from the planning pack and govern this repo directly.

Start with `docs/tickets.md` for the ticket order (M0–M2) before writing logic into the modules below. `docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md` supersedes `docs/NEXT_STEPS.md` for current ordering after M1-01 (H1/H2 hardening, then D1/R1-R3 typed events and replay). `configs/` and `fixtures/` hold the synthetic inputs tickets are built and tested against.

## Status

**M0-01 (configuration validation, hardened under H1), M0-02 (run evidence, hardened under H2), M1-01 (data/calendar contracts), D1 (typed market events and universe coverage), R1 (replay clock and bounded views), and R2 (trade-driven bar builder) are implemented, and have since been corrected and completed through R3 (see below).** `rsys validate --config PATH` loads a `fixture` or `research` profile, type-checks it, and reports every unresolved/invalid field by name with its decision ID (`docs/decisions.md`). Config validation rejects booleans masquerading as numbers, unimplemented schema versions, offset-bearing local cutoff times, placeholder gate-method strings, and blank identifiers/list elements; error messages never echo the submitted or resolved value. `rsys.experiments`/`rsys.storage` provide `start_run`/`run_context` helpers backed by a transactional SQLite store, with immutable resolved inputs, a frozen status lifecycle (`pending -> running -> {success, failed, interrupted, crashed}`), `KeyboardInterrupt` handling, owner-process-verified crash recovery with an exclusive recovery lock, single-component run-ID path safety, a content-based code fingerprint (differs for any dirty-tree change), and camelCase/header-aware secret redaction. `rsys.calendar` and `rsys.data` provide a declared 2024 NYSE calendar (DST-correct UTC conversion, early closes), bar-quality checks (DQ01-DQ07), corporate-action dispatch-timing/unit-conversion contracts with tz-aware validation and late-split bar invalidation (F17), a frozen nanosecond-precise market-event envelope (trade/correction/quote/provider-bar/corporate-action/timer), a `NormalizedBar` contract with canonical identity and interval/availability validation, and a universe-coverage manifest (DATA-002) distinguishing verified no-trade intervals from missing delivery. `rsys.replay`/`rsys.views` provide a deterministic, profile-aware (`bar_v1` / `trade_replay_v1`) availability clock with stable same-time batching, a late-insertion queue guard, and bounded immutable as-of views (`closed_bars`/`latest_trade`/`latest_quote`; `forming_bar` explicitly awaits R3's wiring) split into a close-only baseline context and an opted-in intrabar context, with a future-perturbation harness proving earlier outputs can't change. `rsys.data.bar_builder`/`bar_rules` build FORMING -> CLOSED_AS_OF -> REVISED bar snapshots from a declared synthetic rule set (field eligibility per trade condition code, with real-provider semantics explicitly unresolved pending D12), fully recomputing OHLC/volume/VWAP on every correction/cancel (never patching incrementally), with idempotent redelivery, unresolved-reference/eviction/split-overlap guards, and immutable per-call snapshots. `fixture`, `run`, `compare`, and `final-evaluate` remain CLI stubs (later tickets); no replay/bar-builder wiring, quote state, provider-bar parity, scanner, strategy, ledger, or trading logic exists yet, and no market data or backtest evidence is produced by this repo.

```bash
uv run rsys validate --config configs/fixture.yaml           # exits 0: synthetic fixture profile, no blockers
uv run rsys validate --config configs/baseline_pending.yaml  # exits 2: draft market profile, lists every unresolved field

uv run pytest tests/
uv run ruff check .
```

### R3 corrections and completion (2026-10-29 pass, `docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md`)

`FIX-01`..`FIX-05` fixed ten reproduced/inspection defects (C01-C10: a fingerprint exclusion bug, a config/storage transaction race, a swallowed finalization error, a credential-redaction gap after reload, under-validated bar/trade events, a historical-query backdating bug, an ordering tie-break bug, an unregistered-method-reference bypass, and several builder/rule-interface gaps) with regression tests proving each defect before fixing it. `R3-A`/`R3-B`/`R3-C` then completed the replay path: a validated `ReplayConfig` profile (`src/rsys/config.py`), an immutable `BarVersionStore` (`src/rsys/data/bar_version_store.py`), a `SeriesRouter`/`ReplayRunner` (`src/rsys/replay_runtime.py`) that wires `ReplayView.forming_bar` to a real trade-driven builder, optional quote state (`src/rsys/data/quotes.py`), an offline provider/builder parity comparator (`src/rsys/parity.py`), and a working `rsys replay --config PATH --out auto` CLI command (`src/rsys/replay_io.py`) that produces a deterministic JSONL trace plus run manifest for both a completed-bar and a trade-built-bar synthetic fixture. All of this remains offline/synthetic: no market data was fetched or purchased, and `FixtureBarRuleSet`/the parity comparator's fixtures are explicitly NOT verified Alpaca behavior (a real provider adapter remains blocked on D12's audit). M2 (ledger, execution, scanner, strategies) is not yet implemented.

```bash
uv run rsys replay --config configs/replay_bar_fixture.yaml --out auto     # completed-bar profile
uv run rsys replay --config configs/replay_trade_fixture.yaml --out auto   # trade-built-bar profile

uv run pytest tests/      # 348 passed
uv run ruff check .       # clean
```


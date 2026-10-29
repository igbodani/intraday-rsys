# intraday-rsys

Implementation of the intraday strategy research system. `AGENTS.md` and `docs/` (contracts, decisions, tickets, strategy specs) are copied from the planning pack and govern this repo directly.

Start with `docs/tickets.md` for the ticket order (M0–M2) before writing logic into the modules below. `docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md` supersedes `docs/NEXT_STEPS.md` for current ordering after M1-01 (H1/H2 hardening, then D1/R1-R3 typed events and replay). `configs/` and `fixtures/` hold the synthetic inputs tickets are built and tested against.

## Status

**M0-01 (configuration validation, hardened under H1), M0-02 (run evidence), and M1-01 (data/calendar contracts) are implemented.** `rsys validate --config PATH` loads a `fixture` or `research` profile, type-checks it, and reports every unresolved/invalid field by name with its decision ID (`docs/decisions.md`). Config validation rejects booleans masquerading as numbers, unimplemented schema versions, offset-bearing local cutoff times, placeholder gate-method strings, and blank identifiers/list elements; error messages never echo the submitted or resolved value. `rsys.experiments`/`rsys.storage` provide `start_run`/`run_context` helpers with immutable resolved inputs, a frozen status lifecycle (`pending -> running -> {success, failed, interrupted, crashed}`), crash recovery, path-safe storage, and secret-redacted records. `rsys.calendar` and `rsys.data` provide a declared 2024 NYSE calendar (DST-correct UTC conversion, early closes), bar-quality checks (DQ01-DQ07), and corporate-action dispatch-timing/unit-conversion contracts (F17). `fixture`, `run`, `compare`, and `final-evaluate` remain CLI stubs (later tickets); no replay, trade-driven bar builder, scanner, strategy, ledger, or trading logic exists yet, and no market data or backtest evidence is produced by this repo.

```bash
uv run rsys validate --config configs/fixture.yaml           # exits 0: synthetic fixture profile, no blockers
uv run rsys validate --config configs/baseline_pending.yaml  # exits 2: draft market profile, lists every unresolved field

uv run pytest tests/
uv run ruff check .
```


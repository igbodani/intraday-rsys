# intraday-rsys

Implementation of the intraday strategy research system. `AGENTS.md` and `docs/` (contracts, decisions, tickets, strategy specs) are copied from the planning pack and govern this repo directly.

Start with `docs/tickets.md` for the ticket order (M0–M2) before writing logic into the modules below. `configs/` and `fixtures/` hold the synthetic inputs tickets are built and tested against.

## Status

**M0-01 (configuration validation) and M0-02 (run evidence) are implemented.** `rsys validate --config PATH` loads a `fixture` or `research` profile, type-checks it, and reports every unresolved/invalid field by name with its decision ID (`docs/decisions.md`). `rsys.experiments`/`rsys.storage` provide `start_run`/`run_context` helpers with immutable resolved inputs, a frozen status lifecycle (`pending -> running -> {success, failed, interrupted, crashed}`), crash recovery, path-safe storage, and secret-redacted records -- but `fixture`, `run`, `compare`, and `final-evaluate` remain CLI stubs (later tickets). No replay, scanner, strategy, ledger, or trading logic exists yet, and no market data or backtest evidence is produced by this repo.

```bash
uv run rsys validate --config configs/fixture.yaml           # exits 0: synthetic fixture profile, no blockers
uv run rsys validate --config configs/baseline_pending.yaml  # exits 2: draft market profile, lists every unresolved field

uv run pytest tests/
uv run ruff check .
```


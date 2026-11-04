# BR-04: offline data preflight and resource inventory

Based on upstream `f5e992c9951a2ebf4e9eac82fcab9b65d67737f2`, tree
`d400b7b25bdb0711e42f711675315f843530928b`, on
`docs/copilot-backtest-readiness`. The working tree was clean. This is one bounded
BR-04 implementation; publication stays on that branch, with no merge.

## Goal and design

Before a strategy runs, `rsys check-data --config BACKTEST_JSON --out ROOT` explains
which pinned local objects will be used, whether execution is permitted and where
history is incomplete. The [operator guide](../DATA_PREFLIGHT.md) defines the
commands, retained evidence, counted units, error exits and limits.

Shared `plan_backtest`, `open_backtest_cache`, `compose_backtest`, `BarCache.load`
and `validate_batch` prevent a second set of validation rules. The complete
calendar search budget is authorized before all cache path resolution/inspection.
Equivalent cache paths are canonicalized after authorization, preserving existing
comparison provenance. The full allow/deny prefix is journaled before any read.

The command retains partial per-date reports without executing a strategy or
constructing an HTTP client. Missing execution minutes and invalid/missing objects
block eligibility; sparse but valid warmup objects remain explicit warnings.
Observed zero volume never fills an absent minute. History coverage, calendar
comparability, execution eligibility and research validity remain separate.

The early invocation links to a file-only preflight manifest. Reports and their
hashes precede readiness/sealing; the final atomic manifest commits the outcome.
Required persistence failures remain non-success. Inspection verifies completed
reports; early orphan recovery retains the existing conservative owner policy.
No SQLite research trial, cache refresh, policy change, credential lookup, real
provider request or broker action occurs.

| Owner | Changed behavior |
|---|---|
| `application/historical.py`, `models.py` | Shared plan/composition; plain plan record; canonical paths after authorization; per-date permission journal |
| `data/bar_cache.py` | Actual selected-object byte count through the existing guarded path |
| `application/preflight.py`, `reporting/preflight.py` | Partial report, coverage/resource inventory, escaped HTML and authoritative save lifecycle |
| `research/diagnostics.py`, `models.py`, `inspection.py` | Link a preflight without creating a run; shared status/recovery; verify successful report hashes |
| `cli/commands.py` | Public local-only command and nonzero blocked/error exits |
| `scripts/benchmark_backtest_evidence.py` | Declared offline workloads; actual time, memory and per-file byte measurements |

## Acceptance evidence

[test_data_preflight.py](../../tests/test_data_preflight.py) covers T12–T14:

- Independently known 390/210/390-minute dates, exact 990-bar count, execution/
  warmup/both roles, actual object bytes and compact serialization measurement.
- Fatal provider, credentials, acquisition and strategy/runner spies; repeat checks
  leave cache bytes unchanged and create no research database.
- Two missing adjacent minutes form a half-open interval; a present zero-volume
  minute remains observed. Empty warmup is unavailable, with no invented zeros.
- Denied execution, deep warmup and unknown dates, plus unsupported calendar year;
  cache constructors/stat/resolve/open/read spies remain untouched before denial.
  A real child hard-exits at the first cache read: all allowed dates already exist
  in the journal even though no final report exists.
- Altered bytes, missing objects, wrong date pins, schema/provider/calendar/timing/
  request/feed/mapping/raw identity, false coverage, subminute timestamps, negative
  volume, bad OHLC, conflicting duplicates and symlink escape fail in both paths.
  Equivalent numeric duplicates count once; corrupt request aliases are ignored.
- Shared risk/cost/strategy/scanner/flatten/action/order/pin validation; alternate
  spellings of one cache path produce equal resolved comparison provenance.
- Real public CLI success/blocked exits and status; malformed input retention,
  HTML escaping, report tampering, collisions, report/first/final-manifest write
  failures and preservation of a primary error when its evidence write also fails.
- Known benchmark workloads: 600 bars/two objects and 1,770 bars/five objects;
  independent seven-byte file inventory and explicit unavailable resource values.

Existing [streaming tests](../../tests/test_streaming_run_evidence.py), especially
`test_callback_mutation_and_operational_clock_do_not_change_decisions`, retain
exact prior semantic trace/P&L oracles while changing observer timing and work.
Journal/stream lifecycle tests remain enabled. Resource reporting uses the existing
incremental journal writer; it does not add a second diagnostic event buffer.

## Executed verification

Verification date: 2026-11-04 UTC, locked Python 3.12 Linux environment.

- Focused starting baseline: **61 passed** (`test_alpaca_pipeline.py` and
  `test_historical_comparison.py`).
- Intermediate integration batch: **225 passed in 135.43 seconds**, covering the
  new preflight file plus historical pipeline/comparison and diagnostic lifecycle,
  journal and public operator tests. Final path-canonicalization regression follows
  in the complete suite below.
- Final full suite: **1,163 passed in 228.95 seconds**, zero failures/skips.
  Command: `PYTHONPATH=/tmp/rsys-offline-guard APCA_API_KEY_ID=TEST_ONLY_OFFLINE_KEY APCA_API_SECRET_KEY=TEST_ONLY_OFFLINE_SECRET UV_CACHE_DIR=... uv run --offline --frozen pytest tests/ -q`.
  The temporary guard blocks socket connect/connect_ex/create_connection and DNS
  resolution in this process and inherited subprocesses. An explicit connection
  attempt confirmed the guard was active before the suite. No real credentials used.
- `uv run --offline --frozen ruff check .`: **clean**; `git diff --check`: **clean**.
- `uv run --offline --frozen python scripts/check_copilot_config.py`: **valid**.
- Public `uv run --offline --frozen rsys check-data --help`: correct interface;
  real success/failure/status subprocess commands also passed in the full suite.
- Additional buffering check: wrote and sealed **5,001 journal rows** while a spy
  required `collect_records=False`; returned record storage stayed empty and the
  computed digest matched the seal. No extra complete journal buffer was allocated.
- Independent read-only reviewer: **45 new tests passed**, changed Python lint
  passed, **no blocking findings**. Hard-exit permission retention, canonical path
  equivalence and interrupted finalization were independently reproduced. Reviewer
  did not independently rerun the full suite or benchmark, contact Alpaca or test
  native Windows. The engineer's checks above cover the full suite.
- Reviewed/tested changed-Python fingerprint:
  `69e6fc3eaff43e3c673788b8abde7134be8fba538ab3a993685a6426cfbddad1`
  (sorted changed `.py` paths, each path + NUL + file bytes + NUL, SHA-256).

## Measured resource inventory

Both final benchmarks ran sequentially in fresh processes with the same network
tripwire and dummy credential environment as the tests. Measured output totals
were independently checked by summing every retained run file after completion.

| Workload | Execution minutes | Pinned objects / bars | Cache bytes | Backtest output bytes | Peak RSS bytes | Backtest seconds |
|---|---:|---:|---:|---:|---:|---:|
| early-close | 210 | 2 / 600 | 101,792 | 23,253,892 | 155,623,424 | 3.82 |
| multi-session | 780 | 5 / 1,770 | 299,840 | 191,415,081 | 1,343,410,176 | 22.15 |

Environment: Linux-6.18.57-x86_64-with-glibc2.39, x86_64, Python 3.12.14, 9 reported logical CPUs, 10,451,427,328 bytes reported physical host RAM. Host RAM is not the container allowance. Peak RSS covers the whole process including fixture generation, preflight and execution; it is not an isolated strategy-memory measurement.

Backtest output includes SQLite, journal/state, resolved inputs, trace, manifests,
session/scanner artifacts and HTML; excludes cache, preflight and benchmark JSON.
The workloads preserve all declared lookback dates and normal audit evidence.
Timings and memory are one local measurement, not portable performance promises.

The larger workload's largest artifacts were:

| Artifact | Actual bytes |
|---|---:|
| `scanner_history.json` | 108,935,620 |
| `manifest.json` | 35,732,771 |
| `trace.jsonl` | 16,398,924 |
| `report.html` | 10,766,911 |
| `2024-12-02.json` | 8,543,744 |

This explains why cache bytes cannot predict evidence or peak RAM. Resource costs
are operational measurements; they do not change simulated profit or risk.

## Research and operational limits

No research assumption, strategy rule, account/cost default, promotion threshold,
holdout partition or decision-register status changes. Fixtures are software
evidence. Actual Alpaca credentials/entitlement/response quality and native Windows
behavior remain unverified. Coverage eligibility is not profit evidence, a
corporate-action audit or a promise of trades/completed position exits.

The benchmark exposes repeated scanner evidence and high memory/output growth;
see [BR-04-F01](evidence-compaction-followup.md) for a separate implementation and
test plan. This change reports that cost without dropping history or audit evidence.
The next planned feature is **BR-05**, the opt-in provider validation harness,
which can also be built/tested with fakes. Its real sample needs explicit provider
access, selected feed/date window/partitions and the required action review.
Credentials should be set locally, never pasted into chat or committed.

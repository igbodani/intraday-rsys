# Check local data before a backtest

```bash
uv run rsys check-data --config configs/my_backtest.json --out runs/data-checks
```

Use the same pinned JSON request as `backtest`. This command reads local objects
only: no HTTP client, credentials, download, strategy construction or simulation.
It does not change the cache or create a research trial in SQLite. Each invocation
gets its own retained evidence directory, even when its input or checks fail.

## Read the result

- `can_execute: true` means the shared historical loader and batch validators
  accept the configuration and data. It does not promise a completed simulation,
  trades, profit or enough usable history for every decision.
- `history_coverage_complete` covers observations across the entire declared
  warmup search budget. Missing warmup minutes are warnings. Missing execution
  minutes and invalid/missing objects block execution. A date used for both roles
  follows the execution requirement.
- `history_windows` separately counts prior sessions long enough to cover each
  execution session's full duration. A 210-minute prior session cannot supply a
  390-minute denominator. Earlier minutes may still be usable. The check neither
  substitutes older sessions nor calculates a strategy's RVOL or profitability.
- Complete observed zero-volume minutes remain distinct from absent minutes.
  Complete coverage can still yield a zero denominator. Research remains unscored.
- Corporate-action coverage is the caller's recorded attestation. A passing check
  does not independently verify that attestation or Alpaca feed entitlement.

Every dataset row identifies execution/warmup/both, exact object ID, feed, mapping
date, raw units, timing basis, expected and observed minute counts, duplicate
count, observed zero-volume count and missing UTC intervals. Intervals include
their start and exclude their end. Unverified objects have unknown counts, not
zero counts. Numerically equivalent duplicates count once; conflicts fail.

All execution and full lookback-budget dates are authorized before constructing
the cache or resolving/inspecting its paths. Allowed dates and the first denied
date survive in the early journal; a final or unknown date blocks every cache
read. Exact object hashes, request identity, schema, calendar, normalized bars
and coverage are rechecked using the backtest's cache reader. Aliases are ignored.

## Retained files and exits

Under the chosen output root:

- `invocations/ID/invocation.json` and `diagnostics.jsonl`: start, input hash,
  authorization, observed cache reads and failures, including early failures.
- `preflights/ID/preflight.json`: sanitized request, access log, per-date checks,
  errors, warnings and inventory, including partial results when possible.
- `preflights/ID/report.html`: escaped readable version of that report.
- `preflights/ID/manifest.json`: atomic outcome authority, report hashes and
  diagnostic seal. A report's `can_execute` describes data eligibility; the
  manifest determines whether the check's required evidence was saved successfully.

Exit 0 means eligible data with committed evidence; exit 3 means blocked data or
invalid configuration. An input/output persistence failure can return exit 1.
Missing/unreadable original JSON is retained by identity, without copying unsafe
raw bytes. Required report/journal write failures remain failures; a second write
error does not replace an earlier error. A hard kill can leave partial evidence.

```bash
uv run rsys status --storage-root runs/data-checks --id INVOCATION_ID --json
uv run rsys recover --storage-root runs/data-checks --id INVOCATION_ID --dry-run
```

Status verifies the successful preflight's diagnostic seal and report hashes.
Recovery uses the existing confirmed-dead-owner policy; it cannot turn a partial
check into success. Fix the input/problem and start a new check.

## Resource inventory and local benchmark

`verified_object_bytes` measures actual selected valid object files. It excludes
aliases, unrelated cached versions and invalid/unreadable files. The total is
partial if any object failed. Counts cover the full requested budget; no history
is dropped. `prepared_input_json_bytes` measures a specified compact serialization
of the validated resolved input, streamed through the encoder without allocating
another complete serialized buffer. It is not an estimate of all retained output.

Peak memory and future backtest evidence bytes are explicitly unavailable in the
preflight. A strategy can produce much more evidence than its input size. Measure
the declared synthetic workloads in separate fresh processes and new directories:

```bash
uv run python scripts/benchmark_backtest_evidence.py --workload early-close --out runs/benchmark-early
uv run python scripts/benchmark_backtest_evidence.py --workload multi-session --out runs/benchmark-multi
```

The first workload executes 210 minutes, with one 390-minute warmup day (600 input
bars). The second executes two 390-minute sessions, uses lookback 2/search budget 3,
and retains five input objects totaling 1,770 bars. Flat $100/100-volume bars and
account/risk/cost settings from the existing fixture are synthetic software inputs,
not researched strategy defaults. Both retain the complete normal evidence pipeline.

`benchmark.json` records workload, environment, measured elapsed time, process
lifetime peak RSS where supported, exact output byte totals and per-file sizes.
Host physical RAM is labelled separately from a container's allowance. Unsupported
measurements are unavailable, never fabricated zero. Measurements are operational
and do not enter P&L or the deterministic trace hash. A single measurement does not
predict a larger workload's needs. See [BR-04 verification](agent-workflow/BR04_VERIFICATION.md)
and the [evidence compaction follow-up](agent-workflow/evidence-compaction-followup.md).

Shared history compaction is now implemented; see the [measured reduction and
remaining limits](agent-workflow/BR04_F01_VERIFICATION.md). Re-measure your declared
workload before scaling; the benchmark is not a production capacity guarantee.

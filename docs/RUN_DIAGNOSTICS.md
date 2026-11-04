# Progress, status and recovery

These commands inspect software execution. A successful run does not establish
profitability, complete market coverage or readiness for paper trading.

## See progress while running

```bash
uv run rsys run --config fixtures/sessions_trade_fixture.json --out runs/pilot --report --progress on
```

`run`, `backtest` and `download` accept `--progress auto|on|off`. The default `auto`
prints progress only when stderr is an interactive terminal. `on` also works in
redirected logs; `off` suppresses the human progress display. Existing stdout
summary lines and exit codes remain compatible. Evidence is still saved in every
mode. A closed progress output stream disables the display without changing the
simulation result.

Progress shows the invocation ID, resolved run ID when available, stage, current
session, completed/total sessions, processed events, actual entry fills, exits and
elapsed wall time. Downloads count cached sessions, not simulated trades. Unknown
totals remain unknown; there is no estimated completion percentage or ETA. Normal
updates are limited to about one per second, with immediate lifecycle/session
boundaries. Progress is event-driven: a long cache/provider call can remain quiet
until the next observed event. Silence is not proof of a crash.

## Inspect an attempt

Use the `run_id` from the summary, or the invocation ID from progress/failure paths:

```bash
uv run rsys status --storage-root runs/pilot --id RUN_OR_INVOCATION_ID
uv run rsys status --storage-root runs/pilot --id RUN_OR_INVOCATION_ID --json
```

Inspection is read-only, including its SQLite connection. It does not create a
missing database, repair a partial file or append events. It reports:

- The authoritative outcome: SQLite for a resolved run; a final manifest for a
  download or offline data preflight; operational preparation/failure state before resolution.
- Evidence completeness, separately from the outcome. `complete` means verified
  completed software evidence, not full trading-day coverage. `partial` is
  incomplete work. Old evidence can be labelled `legacy_unverified` or `unverified`.
- Whether the recorded local owner PID exists, is confirmed dead, or cannot be
  checked here. A living PID does not establish that useful work is continuing.
- Last observed journal time and checkpoint counts with their observation time.
  Counts can lag a growing trace; `retained_trace_rows` counts complete disk rows,
  including fill rows. A torn final line is disclosed, never repaired.
- Retained error/failure stage and paths to the invocation, journal, trace and
  manifest, including expected paths that could not be written.

Exit zero means the **inspection** worked, even when the inspected run failed.
Missing IDs, malformed state and detected sealed-evidence changes return nonzero.
A success-looking manifest or `artifacts_ready` event alone never proves a run
committed success. A failed terminal commit remains incomplete.

## Recover a stopped process

```bash
uv run rsys recover --storage-root runs/pilot --dry-run
uv run rsys recover --storage-root runs/pilot --id RUN_OR_INVOCATION_ID --apply
```

`--dry-run` changes no files or database rows. `--apply` rechecks state and liveness
under a shared OS lock and reports each applied transition. Both accept `--json`;
omitting `--id` inspects the whole root. A linked invocation/run is counted once.

Recovery only marks a running record crashed after confirming its owner is dead
on this host. Live processes, unknown/remote ownership, unstarted records without
provable ownership and sealed terminal outcomes remain unchanged. Time since the
last event is never enough to declare a crash. Investigate unknown owners on the
machine that ran them. Windows process-query failures are treated conservatively.

An orphaned early invocation gets a separate, hashed `recovery.json` receipt. Its
original invocation, journal and partial bytes stay unchanged. A linked run uses
the existing transactional SQLite transition. The persistent `.recovery.guard`
file holds the stable OS-lock identity; do not remove it while recovery may be
running. Temporary host/PID lock metadata is reclaimed only for a confirmed-dead
local owner. This is a local-machine safeguard, not a distributed recovery service.

Recovery is idempotent. It does not resume a partially executed account, manufacture
missing prices/exits, rewrite frozen inputs or make a failed attempt eligible for
comparison. Start a new run after fixing its input or operational problem.

## Understand zero trades and failures

Reports include a per-session funnel and observed reason counts. The same versioned
funnel is retained in the pinned manifest; the original observations are in the
linked invocation's `diagnostics.jsonl`.

| Field | Counted unit |
|---|---|
| Opportunities | One actual completed-bar strategy call for one symbol |
| Scanner eligible | Such calls whose symbol was in the selected top N |
| Proposals | Non-None strategy results |
| Acceptances | Proposals accepted by the acceptance book |
| Entry fills | Committed entries, excluding cancellations/rejections |
| Exits | Committed exits |
| Reasons | Occurrences at the named stage, with original event/journal references |

Reason stages are separate units: repeated execution-reference checks never add
proposals or trades. Scanner batches with no candidates are counted per decision
time; history reasons are per symbol/feature snapshot; risk halts are counted when
first observed or changed within a session. A reason can coexist with another
reason at a different stage; do not sum all reason counts as a trade count.

Missing history, verified zero historical volume, stale bars, insufficient cash,
risk halts, expiry, rejected trade conditions and missing required exits have
distinct observed codes. `NO_PROPOSAL` only means the strategy returned no proposal;
it does not guess which internal rule failed. Reason tables retain up to three
example references per code/stage/session; the journal retains all observations.

Zero entry fills can be a successful no-trade session. Failed/interrupted sessions
are visibly partial; later unstarted sessions have zero observations. When an
evidence sink fails after an account transition, known fills/account artifacts can
contain more information than the acknowledged observation prefix. Partial counts
must not be treated as complete statistics. Required flatten/max-hold exits without
a usable next reference remain nonzero failures.

Legacy reports without a funnel say it is unavailable. Semantic trading trace bytes,
entry/exit decisions, as-of data rules, fees and account calculations are unchanged.
See [BR-03 verification](agent-workflow/BR03_VERIFICATION.md) for tests and limits.

The offline `check-data` command shares status/recovery but never creates a strategy
run. See [data preflight](DATA_PREFLIGHT.md) for its reports, exits and resource limits.

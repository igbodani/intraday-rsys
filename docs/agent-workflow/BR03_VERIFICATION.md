# BR-03: operator diagnostics and safe recovery

Based on upstream `500d4e08435f265e98f51254d34b7381300e60c8` on
`docs/copilot-backtest-readiness`; the clean starting tree was
`91017b213a7f830f151cad50a4da495973862d74`. This is one bounded implementation of
BR-03. BR-04 data preflight and later research/provider gates remain separate.

## Contract and design

A user can see progress before completion, inspect a successful/failed/interrupted
attempt without changing it, explain observed no-trade reasons, and recover a
confirmed-dead local owner without creating a successful comparison candidate.
The [operator guide](../RUN_DIAGNOSTICS.md) documents commands and counted units.

No research decisions, market assumptions, strategy rules or cost/risk settings
changed. Tests use explicit synthetic inputs, including deliberately inadequate
cash and adverse prices to trigger existing rejection/halt policies. Real provider
calls, order placement, holdout access, resume and merging are outside this ticket.

| Owner | Changes |
|---|---|
| `execution/models.py`, `execution/trace.py` | Immutable observation callback; actual strategy-call and end-of-input observations |
| `execution/bar.py`, `trade.py` | Observe returned proposals and adapter outcomes once; no extra execution call or wall clock |
| `application/diagnostics.py`, `models.py` | Rate-limited journal checkpoints and per-session funnel; plain session model stays in models |
| `application/sessions.py`, `batch.py`, `run.py` | Wire observations, preserve current semantic trace, retain complete/partial reports and known account effects |
| `application/historical.py` | Acquisition session progress around existing cache requests |
| `research/diagnostics.py`, `models.py` | Two operational event codes and isolated presentation observer; inspection/recovery plain records |
| `research/inspection.py` | Read-only authority-aware inspection; conservative preview/apply and orphan recovery receipts |
| `research/storage.py` | Read-only connections, shared OS recovery guard, conservative owner checks, existing transactional outcome enforcement |
| `cli/commands.py`, `progress.py` | Progress stderr flag and public status/recovery interfaces |
| `research/reasons.py`, `reporting/html.py` | Readable observed-code explanations, counted funnel and escaped references |

The new funnel has its own schema version inside the existing pinned manifest.
Operational observations remain outside semantic trace hashes. Existing schema-2/3
readers retain their checks; older reports disclose absent funnel data. A broken
console disables optional progress. Required diagnostic persistence failure still
stops execution; independent writable fills/account files are saved before an
end checkpoint can fail again.

Recovery uses a persistent OS guard before touching legacy host/PID lock metadata.
This closes the stale-lock unlink race between concurrent recovery processes. It
does not infer remote process death. Early recovery records hashes of untouched
invocation/journal bytes in a separate receipt. No automatic resumption occurs.

## Acceptance tests

New [test_diagnostics_cli.py](../../tests/test_diagnostics_cli.py) implements T09–T11:

| Acceptance | Independent assertion |
|---|---|
| Progress before completion | A real CLI subprocess pauses after its early event; stderr is readable while the child remains alive; original stdout lines are unchanged |
| Modes/throttling | Interactive/noninteractive auto, on/off, injected clocks without sleeps, honest unknown totals, closed-console isolation |
| Public status | All four account paths and cached downloads; both invocation/run IDs; complete-tree hashes and mtimes unchanged by inspection/preview; valid JSON |
| Authority/integrity | Successful manifest before failed SQLite commit stays incomplete; manifest/journal/trace/input/state tampering rejected; malformed download shapes produce errors instead of tracebacks |
| Recovery | Real simultaneous dead/live CLI children, early preflight process kill, held guard and guard-owner kill, stale reclaim race, unknown/remote/oversized PIDs, handled failure/interruption, idempotence and no linked double-count |
| Funnel arithmetic | Independently known 20 bar or 19 trade strategy calls per session, two selected opportunities, one proposal/acceptance/entry/exit; original journal-reference checks |
| Actual reasons | Separate missing/zero/stale history, no candidate, no proposal, insufficient cash, daily/account halt, repeated unusable references and expiry controls |
| Missing exit | Public command fails, first session partial, next not started, original inputs/known fills retained; status inspection succeeds while reporting failure |
| Observer isolation | Adapter-call spy verifies observation never calls execution again; existing exact trace/P&L and future-invariance controls remain enabled |
| Failed persistence | Poison required progress append at start and after first fill in all three synthetic modes; preserve original exception and known financial artifacts |
| Report safety | Script payloads in new reason, date, unit and reference fields are escaped; partial metrics remain explicit |

Independent review reproduced and drove regressions for the stale-lock race,
malformed download state and legacy finalization ordering. The latter could have
lost writable account/fill artifacts after the journal failed. The fixes preserve
primary failures and truthful partial outcomes.

## Executed checks

Verified 2026-11-04 UTC in the existing locked Python 3.12 environment:

- Focused starting baseline (`test_diagnostic_lifecycle.py`, `test_run_record.py`,
  `test_report.py`): **96 passed**.
- Existing streaming/journal regressions after integration: **83 passed**.
- Final whole suite: **1,118 passed in 232.23 seconds**, zero failures/skips.
  Command: `PYTHONPATH=/tmp/rsys-offline-guard APCA_API_KEY_ID=TEST_ONLY_OFFLINE_KEY APCA_API_SECRET_KEY=TEST_ONLY_OFFLINE_SECRET .venv/bin/python -m pytest tests/ -q`.
  The temporary, uncommitted guard made `socket.connect`, `connect_ex`,
  `create_connection` and `getaddrinfo` raise before any network access. Its negative
  control explicitly attempted a connection and confirmed the guard blocked it.
- `.venv/bin/ruff check .`: **clean**.
- `.venv/bin/python scripts/check_copilot_config.py`: **valid** metadata and local links.
- `git diff --check`: **clean**.
- Independent reviewer: **no blocking findings in reviewed scope** against the
  complete BR-03 working diff. Final new-file rerun: **54 passed in 42.79 seconds**;
  earlier related review batch: **188 passed**. Reviewer independently reproduced
  the failure paths, checked real strategy-call/funnel counts and reran the repairs.

The full suite retains the unchanged hand-checked equity and trace-byte oracles:
legacy 10000 → 9880; two-session bars 1000 → 1022.34800; two-session trades 1000 →
1034.14420. These are synthetic software fixtures, not market-performance results.
The historical path uses only fake-provider cache fixtures. No real Alpaca calls
or trades were made. Public status/progress/recovery commands run in subprocesses
in the new tests; unsupported final evaluation remains blocked in the existing suite.

## Limits and next dependency

Verification uses Linux, local files and fake/cached provider fixtures. Native
Windows locking/PID behavior and credential-backed Alpaca responses were not run.
The OS guard uses `fcntl` on POSIX and `msvcrt` on Windows; native Windows validation
remains an external platform check. PID liveness is conservative and cannot prove
useful progress or identify remote owners. Recovery is local, not distributed.

Status reads the retained journals/trace; large-workload cost has not been benchmarked.
In-memory execution histories and repeated scanner snapshots remain existing resource
limits. Progress is event-driven and supplies no heartbeat during a blocking call.
No results here establish a profitable strategy or live-market correctness.

Next: **BR-04**, shared offline pinned-data preflight and resource inventory. No owner
credentials are needed for that implementation. Actual Alpaca validation later needs
explicit sample/feed/access inputs and the remaining research decisions.

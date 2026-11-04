# BR-02: streaming trading evidence

Verified 2026-11-03 (America/Denver), based on upstream
`fceb9d9bf27e9019037c92424d25144b3250fcf9` on
`docs/copilot-backtest-readiness`. The clean starting tree matched
`35d1fa241c65ead422b619c70bbbd9e4008d1ea7`. This implements BR-02;
BR-03 and later tickets remain separate work.

## Behavior and ownership

Account execution now saves each trading event during execution. A force-killed
run retains its acknowledged trace prefix, original/resolved inputs and completed
session checkpoints. Previously the application saved the trading trace from its
in-memory list at finalization, so a process kill could lose the entire trace.

This applies to legacy single-session bars, version-2 multi-session bars/trades,
and local-cache historical backtests. Strategies still use completed bars and the
same as-of inputs, phase order, risk policy, costs and execution references.
`rsys replay` remains the separate data-only replay interface.

| Owner | Responsibility |
|---|---|
| `execution/models.py`, `execution/trace.py` | Optional callback type; append original row to runner history and deliver a deep copy to the observer |
| `execution/bar.py`, `trade.py` | Emit at existing event/fill append points, once per row |
| `application/sessions.py`, `batch.py` | Attach session date once, save inputs before execution, checkpoint sessions and wire the sink |
| `application/run.py` | Stream legacy execution; retain known partial fills/account state |
| `research/trace.py` | Exclusive writer, flush/sync boundaries, read-only prefix reader and artifact verification |
| `research/models.py`, `errors.py` | Plain reader-result model and trace-write error |
| `research/storage.py` | Recover only a confirmed-dead owner on the local host |
| `research/comparison.py`, `reporting/html.py` | Verify artifacts, trace and diagnostics against the committed outcome |

## Lifecycle and compatibility

1. BR-01 creates an invocation before preparation. After resolution the existing
   research record enters `running` in SQLite.
2. Create an exclusive `trace.jsonl`; save sanitized original `input.json` and
   `resolved_config.json` before execution. Historical requests remain distinct
   from expanded resolved input, and their access log is saved before execution.
3. Append sorted-key UTF-8 JSON plus LF per semantic row. Flush every row; sync
   fills, session checkpoints and finalization. Operational session events remain
   in the separate diagnostics journal.
4. Save `sessions/<date>.json` at session boundaries before advancing. A sink
   failure does not roll back an already-applied account transition: known fills
   and partial account evidence are retained when storage is writable.
5. Close/sync the trace, hash actual disk bytes, write artifacts/reports/manifest,
   seal diagnostics, then commit the authoritative SQLite outcome. No finalizer
   reconstructs or overwrites the trace from its in-memory copy.

`trace_version: 1` declares new requirements within existing schema-2/3 manifests.
`trace_complete` distinguishes completion from partial evidence; `trace_records`
counts complete disk records. Comparison still requires terminal success, the
manifest pin, trace hash, diagnostic seal and mandatory artifact hashes. Original
schema-2/3 evidence without the streaming declaration keeps its original checks.

A torn last line is disclosed and excluded from the complete prefix. A malformed
complete line is corruption. Neither reader repairs bytes or fabricates missing
fills. A prefix alone does not establish successful completion.

Regenerating a report from new streamed evidence requires retained `runs.db` and
linked invocation evidence as well as the attempt directory. This rejects a
success-looking manifest left before a failed terminal commit. Already-generated
HTML remains portable. Failed/interrupted reports verify diagnostics too.

Recovery remains a local storage service. Missing ownership and remote-host
records stay unresolved; absence of a PID here does not prove a remote process
died. Ownerless legacy records are deliberately no longer automatically marked
crashed. The recovery lock is local, not a distributed coordinator. Automatic
resume of a previous account is not provided.

## Acceptance evidence

The new `tests/test_streaming_run_evidence.py` covers:

| Acceptance | Assertions |
|---|---|
| T05: equivalence/exactly once | Four public paths match unobserved execution; known fixture trace hashes and money are unchanged. Fill rows equal known fills; inputs exist and terminal manifest is absent during emission. |
| T06: process termination | Pause real public commands after a synced entry fill with test-only pipes. Kill one child while another stays alive; verify exact prefix bytes, dead-owner recovery, live-owner protection and idempotence. Incomplete attempts cannot compare/report as successful. |
| T06: checkpoints/ownership | First-session evidence exists before the second starts. Missing/remote ownership never calls local PID liveness. Existing BR-01 preflight-kill checks remain enabled. |
| T07: sealing/corruption | Detect trace/input/resolved-input/journal changes and missing/changed diagnostics on failed reports. Distinguish a parseable but unterminated tail from corrupt middle content; never repair originals. Legacy-format reader tests remain enabled. |
| T07: failure finalization | Inject partial fill writes and write/flush/sync/close failures; retain known account effects and non-success status. Failed final SQLite commits are not reportable successes. |
| T08: observer isolation | Mutate nested callback payloads, advance operational time and slow the observer with deterministic work; account results and semantic traces remain identical. |
| T08: causality | Future session changes and an unavailable history revision leave the full earlier public trace identical. Changing eligible earlier history changes the selected entry to BBB, an effective negative control. |

The `--progress` CLI belongs to BR-03 and is not implemented here. T05/T08 exercise
subscribed/unsubscribed observers directly; they do not claim to test that flag.

| Fixture | Start | End | Unchanged trace SHA-256 |
|---|---:|---:|---|
| Legacy bars | 10000 | 9880 | `d67086832d008a2a69e9a9dafcb50ae0322e699c6526e9f3144adfba85b31cbc` |
| Two-session bars | 1000 | 1022.34800 | `c029c04459d1bf9fbd6219525f482b3b14577d327bf0dfc3a2e121ec09245529` |
| Two-session trades | 1000 | 1034.14420 | `767ca26db5c156389e98c264312eb2c1b6282cde29ebb3d7039b89a5bb117fb8` |
| Fake-cache historical sample | 1000 | 1022.34800 | `13c7e19329ef59cf648fea778cb650b130ab4a915a9b4efea500dc52849d05cf` |

All four public CLI paths generated HTML reports and matched their pre-change
trace bytes exactly (94, 212, 568 and 3012 records respectively). All 17 fixture
cases and the configured data-only replay returned exit 0. Final evaluation
remained blocked with the required exit 5.

Reproduction:

```bash
uv sync --frozen --group dev
uv run pytest tests/test_streaming_run_evidence.py -q
uv run pytest tests/ -q
uv run ruff check .
uv run python scripts/check_copilot_config.py
git diff --check
```

Initial focused baseline: 128 passed. Final streaming file: 42 passed in 49.53
seconds. Broader affected-area run: 258 passed before the last six failed-report
cases were added; the added failure subset then passed 11 tests.

| Final gate | Actual result |
|---|---|
| Full suite in the frozen environment | **1064 passed in 192.29 seconds; zero failures/skips** |
| Ruff | All checks passed |
| Copilot metadata/local-link checker | Valid |
| `git diff --check` | Clean |
| Public smoke and trace parity | All four run paths, 17 fixture cases and data-only replay passed; final evaluation blocked with exit 5 |

The full suite used `.venv/bin/python -m pytest tests/ -q` in the existing locked
environment, with fake `APCA_API_KEY_ID`/`APCA_API_SECRET_KEY` values and an inherited
temporary `sitecustomize.py` that raises on socket connections/address resolution.
The tripwire was checked explicitly. No real provider request or credential was
used. This temporary verification guard is not the future BR-05 provider harness.

Existing test changes are explicit: ownerless recovery expects unresolved status;
local process fixtures declare hostname; report failure injection targets the
internal renderer used before commit; legacy fixtures remove new streaming fields.
Original trading/money assertions were not weakened. Unrelated formatting drift
was left unchanged.

The first full-suite pass identified five old legacy-artifact assertions that
expected only the former trace/manifest/report file set. They now check the exact
expanded file set and every saved artifact hash, including partial-failure cases.

## Independent review and limits

A separate read-only reviewer found one medium issue: failed-report loading
initially omitted diagnostic-seal verification. It was fixed and independently
reproduced, with six missing/tampered-journal regressions. Final verdict:
**no blocking findings in the reviewed BR-02 diff**. The reviewer independently
passed 31 initial streaming tests, 159 related tests, 11 added failure tests and
18 legacy-evidence tests after the artifact-oracle update. It reviewed the handoff
and confirmed that the updated file/hash assertions preserve the independent
money checks. It did not repeat the full suite.

Testing is offline Linux/Python 3.12 with fixtures and fake cached provider data.
Real Alpaca access, Windows, hardware failure and power loss remain untested.
Flush/sync tests establish the process-kill boundary, not universal durability.

Runner histories and scanner snapshots remain in memory; streaming does not solve
their memory growth. No progress/status/recovery CLI, new strategy, provider
entitlement, market assumption, trial budget or promotion rule was added. Final
holdout evaluation remains blocked.

Next: **BR-03—operator-facing progress, status, recovery and failure explanations**.
It can be built offline without credentials. Real-provider checks still require
local credentials and explicit feed, symbols, permitted dates, warmup and
action-review inputs.

# Compact run evidence: implementation and testing work order

Status: **planned, not implemented**. Prepared 2026-11-12.
Branch: `feat/compact-run-evidence`, based on `develop`
`795e33d37d4293f16952405e2aa59ad493b30b04`.

Goal: retain every auditable event while avoiding repeated large decision arrays
in run manifests, session summaries and HTML reports. Preserve trading behavior,
research settings, failure evidence and compatibility with existing attempts.
No Alpaca credentials are required. This work order is based on repository source
and its existing published synthetic benchmarks; it contains no private experiment
records, run identifiers, configuration snapshots or financial results.

## Source-based reason for this branch

[BR-04-F01](BR04_F01_VERIFICATION.md) already compacted scanner history observations
losslessly. Its published synthetic multi-session benchmark still retained
84,086,216 output bytes for 780 execution minutes and 1,770 input bars. This is
historical repository evidence, not a fresh measurement of the current branch.
Re-run the benchmark before implementation. Reuse that version-2 history archive.

Source inspection at the base revision identifies the remaining duplication:

- `application/results.py:summarize_runner` materializes decisions and fills.
- `application/batch.py:BatchSessionRunner.run` retains runners, per-session
  summaries and an aggregated trace; `summary` duplicates decisions again globally.
- `application/batch.py:_run_batch_cli` merges that summary into the manifest,
  writes full session summaries, and renders HTML from accumulated rows.
- `reporting/html.py:_scanner_details` renders every scanner decision in HTML.
  `load_attempt` collects the full trace again when regenerating reports.
- `research/comparison.py:load_run_manifest`, diagnostic status/recovery and
  experiment scorecards consume sealed evidence; they must migrate together.

Profile the existing public synthetic workloads to measure each category rather
than estimate savings from code alone. Compression is useful for transfer, but it
does not remove duplicate allocations when building/loading manifests and reports.

## Scope and representation decision

The first release should introduce **run manifest version 4** and **compact
session-summary version 2**. Those versions are proposed here and do not exist yet.
Keep trace version 1, its canonical JSONL bytes/hash and scanner-history version 2
unchanged in this first release. This creates a strong equality oracle and limits
the number of formats changing together.
These are output artifact versions. Keep the historical request's
`schema_version: 1`; do not change a market-data configuration to version 4.

| Artifact | Planned role |
|---|---|
| `trace.jsonl` | Single canonical chronological event record, including every decision/fill/risk event currently retained |
| `scanner_history.json` | Existing shared observation/snapshot archive; resolve exact selected revisions by ID |
| `manifest.json` v4 | Small status/provenance/summary plus artifact hashes, counts and references; no expanded decision arrays |
| `sessions/DATE.json` v2 | Session status, balances, metrics, risk/failure summary and a bounded trace selector; no duplicated decision bodies |
| `fills.json`, `metrics.json` | Small explicit convenience views, verified against canonical event-derived results |
| `input.json`, `resolved_config.json`, `data_access.json` | Preserve original/resolved inputs and authorization evidence |
| `report.html` | Readable account/session/trade summary with links to the complete evidence; no repeated full scanner JSON for every minute |
| SQLite / study registry / diagnostic journals | Preserve authoritative lifecycle, attempt accounting and diagnostic seals |

A trace selector must include trace identity/hash, session date and exact record
bounds/counts. Never trust a selector that silently omits eligible records or
crosses a session. The reader must support the current public decision projection:
if decision and acceptance rows combine into one logical decision, preserve the
existing ordering, deduplication and field precedence, including session identity.
Add a parity oracle against every old serialized decision before removing copies.
If any old field cannot be reconstructed losslessly, retain it canonically before
removing the old copy; do not guess it from a summary.

Plain record/schema definitions belong in the owning `models.py`. A proposed
`research/evidence.py` service should own supported-version dispatch, safe relative
paths, integrity checks and bounded iteration. Keep rendering in `reporting` and
the runner orchestration in `application`. Do not import CLI code into core layers.

Manifest identity may change with its representation. The original `config_hash`,
data identity, semantic trace hash and financial results must not change merely
because an output record moved. Source hashes will change with this implementation;
do not weaken normal cross-code comparison rules to make a test pass. A test-only
parity harness can compare canonical outputs across the two pinned revisions.

## Ordered tickets and agents

Use one engineer for this bounded branch, followed by one independent review of a
tested revision. Use the architect for S01's format decision if needed. Return to
implementation only for reproducible findings or unmet acceptance conditions.

### S01 — Freeze the baseline and lossless reader contract

1. Record clean base SHA, Python/OS/dependency lock, settings and full file inventory.
   Run the existing early-close and multi-session benchmarks in fresh processes.
2. Add a larger deterministic synthetic workload: 30 full sessions,
   20-session history, ten execution sessions, one symbol and both strategies.
   Give it a few hand-calculated trades and explicit fixture settings. Do not copy
   downloaded AAPL rows or claim synthetic results establish market performance.
3. Inventory every decision field and all manifest/session consumers. Capture an
   independent expected projection from base-revision artifacts, not a second call
   to the new decoder. Include accepted/rejected/unfilled and interrupted sessions.
4. Implement the common reader and plain models for v4/session-v2 while retaining
   supported legacy v2/v3 and history-v1/v2 readers. Reject unknown/incomplete
   versions. Readers must not rewrite or migrate old sealed artifacts in place.
5. Verify required artifacts and internal references, not only outer file hashes.
   Use actual byte hashes across Windows/Linux; forbid traversal and unsupported
   paths. Keep summary reads small and decision iteration streaming.

Completion: legacy fixtures still load; new synthetic compact fixtures reconstruct
every original logical decision exactly; damaged references fail after the test
reseals outer hashes. No new writer is made default before readers are ready.

### S02 — Write small manifests and session summaries

1. Create an output projection rather than blindly merging `batch.summary()` into
   the new manifest. Keep all comparison/provenance fields and failure summaries.
2. Build session selectors/counts as the existing trace sink observes records.
   Preserve its ordering, byte encoding, flush/checkpoint behavior and complete tail.
   Do not add new trading events just to locate session boundaries.
3. Write compact per-session checkpoints. Distinguish running, successful, failed,
   interrupted and not-started sessions; a partial trace cannot imply completion.
4. Keep API compatibility where callers currently need in-memory summaries. Avoid
   retaining a second experiment-wide trace solely to render CLI output. Release
   completed-session presentation state when no live calculation needs it; never
   clear strategy/account/history state that later simulation still depends on.
5. Preserve atomic artifacts → manifest → diagnostic seal → committed run outcome.
   Audit the actual existing ordering before edits. Required-write failures must
   yield failure with retained location and evidence; secondary errors must not
   replace the primary exception. No success before required evidence is durable.

Completion: batch-backed public bar/trade runs, historical backtests and experiment
children write v4 consistently; the unaffected standalone legacy writer may keep
its supported v2 format. Phase-one trace bytes, fills, metrics, ranks, sizing and risk
events match the base oracle. Old attempts remain untouched and readable.

### S03 — Update reports, comparison, status and recovery

1. Make `load_run_manifest`, experiment scorecards, report regeneration and status
   use the shared versioned reader. Preserve successful-versus-inconclusive and
   partial-versus-complete distinctions and all current comparison-axis controls.
2. Render trade/session summaries without materializing the entire trace or
   embedding each scanner snapshot. Link relative evidence files in the same
   copied output folder; show counts and where to inspect complete decisions.
3. Provide a bounded programmatic iterator for a session/decision through the same
   reader. Document its use; do not advertise a nonexistent CLI command. If an
   additional human-view command is necessary, specify/test it as an explicit
   extension rather than quietly changing an existing command's contract.
4. Hash and escape every new report/artifact as required. Reports must remain
   useful when opened locally without credentials, network or a web server.
   Regenerate a legacy report into a separate export destination; do not overwrite
   a report covered by an old attempt's seal with a new rendering.
5. Support readable failed/interrupted evidence without allowing it into a normal
   comparison. Verify required files even if a summary alone could be displayed.
   A missing/corrupt trace may produce an error or explicitly unverified display,
   never a normal verified scorecard.

Completion: old/new reports and comparisons work; all listed commands use the same
integrity rules. Bounded rendering must not become an integrity-check bypass.

### S04 — Prove failure safety, causality and resource improvement

1. Run the acceptance matrix below and all affected existing tests.
2. Benchmark both revisions on identical synthetic workloads and the same machine,
   with report generation enabled. Measure complete output, runtime and peak RSS
   in fresh processes; also measure read/compare/report regeneration separately.
3. Engineering target for the 10-session/two-arm workload: **at least 70% smaller
   uncompressed total output**, new child manifests below 1 MB each and child
   summary reports below 2 MB each. Include SQLite, sessions, traces, diagnostics
   and all supporting files in the total; moving bytes elsewhere is not a saving.
   These are acceptance targets, not already measured results or trading thresholds.
4. Show peak-memory behavior as 10 becomes 20 sessions: presentation/manifest
   finalization must not build duplicate whole-run arrays. Initial prepared input
   and the existing history archive can still grow; report that limitation instead
   of claiming total constant memory. Do not set an unexplained flaky RSS limit.
5. Have an independent adversarial reviewer inspect the final diff, rerun parity
   and damage/failure controls, and publish the actual measured before/after table.
   Fix supported blockers, rerun affected checks, then the required full suite.

Completion: all acceptance checks pass with actual evidence and stated platform
limits; no claim of storage improvement is based solely on ZIP size or fewer trades.
An optional local replay of the owner's cache must be explicitly treated as an
operational reproduction and recorded, not hidden as a new research trial. It is
not required for the credential-free synthetic implementation proof.

## Acceptance matrix

New test names below are proposed until implemented. Extend existing files where
that better fits ownership; preserve the original independent assertions.

| Test group | Required assertions |
|---|---|
| Exact behavior parity | Same frozen inputs yield byte-identical v1 traces and matching all decisions, history IDs, fills, fees, balances, risk events, session totals and metrics across old/new layouts; compare nontrading, losing and successful cases |
| Projection boundaries | Decision/acceptance merging, repeated times across different dates, same-time phases, rejected/expired intents, zero-trade days and every decision field reconstruct exactly |
| Hidden corruption | Remove/change a trace row, snapshot, selector count/boundary or convenience fill; rehash outer files and prove the reader still detects internal inconsistency |
| Failure durability | Inject disk-full/permission errors at trace, session, report, manifest and seal writes; preserve primary exceptions, one meaningful failure outcome, attempt paths and partial evidence |
| Interruption/recovery | Ctrl-C, a torn JSONL tail, a killed owner and report failure after simulation cannot produce a successful comparable run; later sessions remain visibly not-started |
| Reader compatibility | Old v2/v3, history v1/v2 and new v4 are handled intentionally; unknown versions and partial schema markers fail; no old file rewritten |
| Causality | Future revision mutation leaves every earlier output unchanged; a visible earlier mutation changes the expected decision as a negative control |
| Policy parity | Different feed/pins/risk/cost/date/code settings still require their declared comparison axes; undeclared/final dates denied before any data read; sparse unknown gaps stay inconclusive |
| Report/size behavior | Escaped text and hostile filenames; working relative links after moving the whole output folder; accurate counts/full-evidence links; bounded iteration instead of full-trace list loading |
| Public callers | `run` with bars/trades, `backtest`, `experiment`, `compare`, `status`, `recover --dry-run`, and Python report regeneration agree on supported formats, statuses and seals |
| Cross-platform bytes | Windows/Linux newline and path handling; no normalization that changes sealed bytes; avoid long temp-path assumptions and privilege-only symlink setup |

Existing evidence to retain includes
[test_history_evidence.py](../../tests/test_history_evidence.py),
[test_public_sessions.py](../../tests/test_public_sessions.py),
[test_diagnostic_integrity.py](../../tests/test_diagnostic_integrity.py),
[test_diagnostic_journal.py](../../tests/test_diagnostic_journal.py),
[test_diagnostic_lifecycle.py](../../tests/test_diagnostic_lifecycle.py),
[test_report.py](../../tests/test_report.py),
[test_historical_comparison.py](../../tests/test_historical_comparison.py),
[test_experiment_plan.py](../../tests/test_experiment_plan.py),
[test_windows_portability.py](../../tests/test_windows_portability.py) and
[test_sparse_minutes.py](../../tests/test_sparse_minutes.py).

## Commands and handoff

Get the prepared branch in a clean local checkout:

```powershell
git fetch origin
git switch --track origin/feat/compact-run-evidence
uv sync --frozen --group dev
```

If a local branch already exists, switch to it and use `git pull --ff-only` instead
of creating it again. Preserve local edits; do not use reset/clean to force a switch.

Before changing behavior, run the existing benchmarks in this clean base tree and
retain their output outside Git:

```powershell
uv run python scripts/benchmark_backtest_evidence.py --workload early-close --out runs/storage-before-early
uv run python scripts/benchmark_backtest_evidence.py --workload multi-session --out runs/storage-before-multi
```

Run the same workloads to fresh `storage-after-*` roots after implementation.
S01 adds the larger deterministic workload; give its selector a documented name
and validate it before adding its command to the final handoff. No proposed selector
is presented here as already executable. Do not reuse benchmark output directories.

```powershell
uv run pytest tests/test_history_evidence.py tests/test_public_sessions.py tests/test_diagnostic_integrity.py tests/test_diagnostic_journal.py tests/test_diagnostic_lifecycle.py tests/test_report.py tests/test_historical_comparison.py tests/test_experiment_plan.py tests/test_windows_portability.py tests/test_sparse_minutes.py -q
uv run pytest tests/ -q
uv run ruff check .
uv run python scripts/check_copilot_config.py
git diff --check
```

Also run each affected public CLI on synthetic local fixtures, plus the new
compact-evidence tests created in S01–S04. Keep provider tests offline. Full-suite
counts must be fresh for the final code revision; this work order's existence is
not implementation or test evidence.

Copy this task to the `python-engineer` agent:

> Read AGENTS.md, the current architecture and decisions, then
> docs/agent-workflow/compact-run-evidence-plan.md. Work on
> feat/compact-run-evidence. Implement S01–S04 as one bounded evidence-format change
> with the common reader first. Preserve trace-v1 bytes, all decisions/trades,
> integrity/failure semantics and old-format reads. Use synthetic fixtures; keep
> market configuration and strategy behavior unchanged. Measure complete output
> before/after and run the listed checks. Request independent adversarial review
> of a concrete tested revision. Report unsupported cases and actual results;
> do not claim the storage targets passed without measuring them.

Reviewer handoff: provide base/head, clean status, changed files, exact comparison
fixtures, parity results, corrupt/resealed and sole-write-failure reproductions,
full-suite/lint evidence and benchmark machine details. Distinguish a reproduced
correctness defect from an optional improvement. Do not merge into develop until
the owner requests the merge.

## Explicit later work

- Optional streaming compression of canonical trace bytes can be a follow-up
  after this release. It needs separate stored-byte and logical-content hashes,
  deterministic codec settings, bounded readers and interrupted-stream tests.
  Do not quietly gzip a file while legacy readers expect plain JSONL.
- Cross-arm input deduplication, a columnar data cache, SQLite migrations and
  changing source fingerprint newline policy are separate proposals. They are
  not needed to remove the repeated summaries and reports identified in source.
- Corporate-action support, calendar expansion, strategy cutoffs, exits, scanner
  filters, costs and holdout/promotion policy belong to separate research/code
  changes. Storage repair must not erase unfavorable trials or change profits.

## Preparation checks for this planning commit

Executed on 2026-11-12; no runtime code or existing experiment configuration was
changed. The local starting tree was checked against develop's exact Git tree
`b1b21b2d6266ec19351f3354c6d57b7bcbd22737` before these document edits.

- Existing ORB/VWAP configuration defaults were checked through the real builder.
- Existing ORB, VWAP and Copilot-config tests: **33 passed**.
- Copilot metadata/configuration checks, local documentation links, locked Ruff
  0.17.3 and `git diff --check`: passed.
- The complete runtime suite and before/after compaction benchmarks were not run
  for this documentation-only commit. They are required for S01–S04 implementation;
  no storage-reduction result or real-data rerun is claimed here.

# Backtest readiness: testing and acceptance plan

This is the test specification for [the tickets](backtest-readiness-tickets.md), not a claim that the proposed tests already pass. Existing regression files below are the starting safety net. New tests must use independent values, public paths and negative controls rather than recomputing the implementation as their oracle.

## Baseline and routine completion commands

Run from the repository root. These are **existing commands**:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
uv sync --frozen --group dev
uv run python scripts/check_copilot_config.py
uv run pytest tests/ -q
uv run ruff check .
git diff --check
```

The plan's base had 937 passing tests. Record the actual count and revision for your checkout. A baseline failure must be reproduced and classified; do not skip it, loosen assertions, change the fixture oracle or copy the old count. The default suite must make no provider requests even if credentials exist in the environment.

Existing public smoke paths:

```bash
uv run rsys fixture --case all --out runs/readiness/fixture
uv run rsys run --config fixtures/standalone_run_fixture.json --out runs/readiness/legacy --report
uv run rsys run --config fixtures/sessions_bar_fixture.json --out runs/readiness/bar --report
uv run rsys run --config fixtures/sessions_trade_fixture.json --out runs/readiness/trade --report
uv run rsys replay --config configs/replay_bar_fixture.yaml --out runs/readiness/replay
uv run rsys final-evaluate
```

The last command **must exit 5 and remain blocked**. Assert that separately; do not mistake it for a failed software gate or combine it into an all-zero-exit script. Repeated output roots must retain distinct attempts rather than overwrite them. Offline historical backtest/compare subprocess coverage is supplied by fake cached datasets in the existing test suite, so it needs no credentials.

For each material implementation ticket: run the focused new/existing tests, the affected public command, independent review, then full tests and Ruff on the final code. Re-run affected checks after a supported fix; broaden testing again only for new changes or unresolved failures. Documentation-only changes need the Copilot checker/local-link checks and affected configuration tests; they do not prove runtime behavior was implemented.

## Existing regression map

| Area | Existing files | What they protect |
|---|---|---|
| Account and sizing | `tests/test_portfolio.py`, `tests/test_acceptance.py`, `tests/test_sizing.py`, `tests/test_m2_account_repairs.py` | Cash/reservation ownership, Decimal arithmetic, atomic rejection and fee/risk checks |
| Public execution and policies | `tests/test_public_sessions.py`, `tests/test_trade_input_run.py`, `tests/test_trade_execution.py`, `tests/test_trade_condition_execution.py`, `tests/test_standalone_run.py` | Two-session balances, halts, costs, timers, eligible fill references, condition rules and retained failures |
| Causality and history | `tests/test_causality.py`, `tests/test_r3_clock_fixes.py`, `tests/test_m2_run_repairs.py`, `tests/test_minute_history.py`, `tests/test_minute_history_repairs.py`, `tests/test_public_history_scanner.py` | Bounded observations, availability/revisions, missing versus zero, complete earlier-trace invariance |
| Provider/cache/partitions | `tests/test_alpaca_pipeline.py`, `tests/test_holdout_boundary.py` | Fake provider failures, cache integrity, normalization, offline reuse and denied reads |
| Evidence and operators | `tests/test_run_record.py`, `tests/test_run_cli.py`, `tests/test_compare_cli.py`, `tests/test_historical_comparison.py`, `tests/test_report.py`, `tests/test_multi_session_evaluation.py` | Frozen records, recovery, artifact verification, comparisons, escaping and metric conventions |
| Agent/package boundaries | `tests/test_copilot_config.py`, `tests/test_package_structure.py` | Discoverable metadata, links, model ownership and import direction |

## Acceptance matrix

### T00 — Checkout and independent account baseline (BR-00)

- Confirm repair behavior: declared strategy/feed-delay comparisons work; source/policy/two-axis changes fail; volume-only prices cannot cause fills or false account peaks; malformed nanosecond timestamps fail.
- Preserve the two-session fixtures' exact money expectations: initial `1000`, bar ending `1022.34800`, trade ending `1034.14420`; two completed trades/four fills, no open positions or reserved cash. Check costs are applied once.
- Record the current semantic output and source revision for manual before/after comparison. Tests also retain independent account values and invariants; do not merely bless a newly generated snapshot.

### T01 — Early visibility and correlation (BR-01)

- Pause a child at a controlled preparation/cache-read barrier. While it is still running, read the invocation ID, sanitized request identity and at least one complete diagnostic line from disk.
- Kill a separate child during preparation: the invocation remains inspectable even though a resolved run may not exist. It must not appear as successful or vanish from status.
- After normal resolution, invocation/run/artifact links agree. A study counts the invocation and linked run once. Concurrent invocations have distinct directories and independent sequences.

### T02 — Secret handling (BR-01)

- Inject fake credentials in nested configuration, provider exception text and arbitrary error messages. Include punctuation/newlines to avoid a trivial regex-only test.
- Search every generated text artifact and captured stdout/stderr for the exact injected values; none may appear. The failure type/stable error code must remain useful.
- With unknown redaction context, free-text details are withheld. No authorization header, environment dump or malformed raw input is copied into a report.

### T03 — Append, flush and encoding boundaries (BR-01)

- Validate version, IDs, increasing per-invocation sequence and explicit clock fields. Each completed record is one UTF-8 line; embedded newlines/HTML remain encoded data.
- Observe flushes before a reader acknowledges visibility and sync calls at the promised boundaries. Inject short writes, flush errors and a torn trailing record.
- Original bytes remain intact. A reader reports a valid prefix plus incompleteness; it never ignores a corrupt middle record or treats a partial journal as final.

### T04 — Error on error (BR-01)

- Inject permission/disk-full failures at creation, append, flush, final manifest and report writing, plus a normal preparation error followed by cleanup failure.
- No case prints successful completion or leaves a comparable successful record. Preserve the primary failure and identify the secondary write problem when possible.
- When all writes fail, require nonzero output explicitly saying evidence could not be saved; do not assert that an unwritable disk somehow retained a file.

### T05 — Streaming equivalence and exactly-once order (BR-02)

- Exercise legacy version-1 bars, version-2 bars/trades and fake-cache historical backtests through public entry points.
- Compare quiet/progress-on executions: ordered decisions, acceptances/rejections, reservations, fills, account/risk states, scanner references and semantic trace hashes are identical. Attempt IDs/wall time/operational hashes may differ.
- Read a prefix before completion. Count fill rows and session transitions; batching plus callbacks must not duplicate them. Preserve canonical newline/Windows behavior.

### T06 — Real child-process termination (BR-02)

- Use barriers/pipes and bounded watchdogs, not arbitrary sleeps. Force termination after an acknowledged flushed trace record and separately during preflight. Do not add production pause-for-test CLI flags.
- The flushed prefix survives, the command never committed success, recovery identifies only a confirmed-dead local owner, and comparison refuses incomplete evidence.
- Keep a second child alive during a recovery sweep: it stays running. Repeat recovery to prove idempotence; finalized runs remain unchanged. Unknown-host ownership is not automatically called dead.
- Use the platform's force-termination mechanism and document the tested OS. Do not claim OS/power-loss guarantees from an in-process mock alone.

### T07 — Sealing, tampering and compatibility (BR-02)

- Successful new attempts verify journal/trace/artifact hashes from disk. Modifying a sealed diagnostic file, a trace row or input after sealing must be detected.
- Existing schema-2/3 manifests remain readable/comparable under their original requirements. New diagnostics-version evidence cannot silently omit a required artifact.
- A hard-killed run with absent terminal manifest or a torn tail stays incomplete. A report/status command must not silently repair it or fabricate complete P&L.

### T08 — Causality and observer isolation (BR-02)

- Mutate only observations unavailable at a chosen cutoff. Compare the entire earlier public behavior, including fills and account/risk states; ignore only attempt/provenance metadata when doing the semantic comparison.
- Change an eligible earlier observation and prove the relevant earlier outcome changes. This negative control is mandatory.
- Slow the diagnostic sink or advance its wall clock: simulated readiness/exit times and results stay unchanged. A malicious sink mutating its received mapping cannot mutate strategy/account state. A sink error causes explicit non-success.

### T09 — Public progress and status (BR-03)

- Read stderr progress before process completion with `--progress on`; existing stdout summaries remain parseable. Test interactive/noninteractive auto behavior and explicit off.
- Fake wall-clock advances to test throttling without sleeping; unknown totals never become fake percentages. No quiet/progress choice changes the semantic result.
- `status --json` produces valid machine-readable output, names the actual run outcome/completeness and is read-only. Missing IDs and malformed state return useful errors; reading a failed run is not itself proof the simulation succeeded.

### T10 — Honest reason counts and reports (BR-03)

- Use separate fixtures for no scanner candidate, missing/stale history, genuine zero denominator, no strategy proposal, insufficient cash, risk halt, expiry, unusable fill condition and missing required exit reference.
- Each diagnostic describes the actual observed stage/reason. It does not relabel zero trades as a bug or guess which strategy rule failed when only `None` was returned.
- Independently count opportunities/proposals/entries/exits from tiny timelines. Repeated pending-order checks do not inflate proposal totals. Failed/partial session metrics are visibly partial; injected script/HTML in messages is escaped.

### T11 — Recovery command and failure usability (BR-03)

- `recover --dry-run` changes no bytes/DB rows. `--apply` respects locks, liveness and terminal sealing, and reports exactly what changed.
- Test preflight-only invocation, resolved running record, handled failure, interrupted run, dead owner, live owner and unknown host. Never count the early and resolved records twice.
- In a public run, remove all eligible references after a required flatten request. Expect a nonzero incomplete-data failure, first session failed, later session not started, and retained partial inputs/fills. This is a detected input limitation, not a successful flat account.

### T12 — Preflight agreement and no simulation (BR-04)

- Compare `check-data` eligibility to the existing loader on the same pinned fixtures. A normal session expects 390 regular minutes; the declared early-close fixture expects 210.
- Spy on strategy/execution methods: none may be invoked. Spy on HTTP/credential access: none may occur. Repeat checking without modifying the cache.
- Missing execution minutes fail. Warmup gaps remain explicitly unavailable/warned under current semantics, with no older-session substitution. A present zero-volume observation remains different from an absent one.

### T13 — Before-read policy and identity failures (BR-04)

- Place a denied date in execution and separately inside the complete warmup search budget. Reader/alias/stat/network spies must remain untouched before the denial is recorded.
- Reject altered cache bytes, request/feed/mapping mismatch, symlink escape, unsupported schema/calendar/year, malformed/non-minute timestamps, invalid OHLC/volume and conflicting duplicate bars.
- Equivalent numeric duplicates remain accepted once; inventory and cache round-trip match. These checks cannot be bypassed by asking for a preflight report instead of a backtest.

### T14 — Resource measurements without false guarantees (BR-04)

- A small deterministic workload has independently known bar/session counts and output byte totals. Operational timings do not affect semantic hashes.
- The journal writer does not buffer the entire stream again; check its buffering contract rather than asserting an arbitrary machine-dependent runtime.
- Benchmark the app separately on a 210-minute fixture and a declared larger local fixture, recording lookback, hardware/OS and memory/bytes. Report scanner snapshot growth honestly; unsupported memory measurement is unavailable, not zero. No credential-backed data needed.

### T15 — Offline by default (BR-05)

- Run default tests with fake credential environment variables present and network access made fatal: no provider calls happen.
- Explicit integration selection without enablement/local request must not call Alpaca. If real access is absent, document the unrun external check without marking the provider gate complete.

### T16 — Fake provider boundaries (BR-05)

- Cover multiple/empty pages, repeated tokens, exact/numeric/conflicting duplicates, 401/403, bounded retryable failures, malformed data and interruption.
- Assert exact fake request/retry counts and retained per-date states; use injected sleep instead of real retry delays. Earlier completed objects survive a later failed download and are reused on retry. Secrets never appear in diagnostics.

### T17 — Real provider sample (BR-05, separately enabled)

- Require an owner-supplied request and explicit invocation. Confirm feed entitlement, raw/mapping semantics, 2024 calendar scope, chosen development dates and all required warmup dates.
- Inspect actual timestamp precision, numeric fields, first/last bars, coverage and known action evidence. Record object hashes and any discrepancy. Download success alone is not a complete-coverage or action-audit pass.
- Repeat without refresh to reuse the pins; backtest afterward with HTTP blocked. Record what was verified versus still unknown about final historical revisions and modeled timing.

### T18 — Experiment plan validation before any arm runs (BR-06)

- Reject unknown schema/keys, duplicate variant IDs, unsupported patch paths, invalid study kinds, missing budget/failure policy, final dates and unapproved combined changes before opening data or starting a child run.
- Retain the failed parent invocation and exact attempted plan. No valid first arm may run before a later invalid arm is discovered during plan validation.

### T19 — Complete trials, budgets and restart behavior (BR-06)

- Use a declared three-attempt fixture budget with success, failure and retry cases; failures/retries count under the recorded convention. A restart in the same study registry cannot reset the count.
- Reserve a trial transactionally; two invocations competing for the final available slot cannot both execute. No parallel simulation engine is needed to test this storage race.
- Every planned arm has a state and, when launched, its attempt links. Stop/continue failure policies and interruption preserve unstarted variants. A blocked-budget arm does not get fake metrics.

### T20 — Comparison isolation and stale evidence (BR-06)

- A single permitted strategy or feed-delay variation compares; adding cost, source, partition, action-review, code or another undeclared change fails.
- Validate entry-versus-exit parameter categories, not just the broad `strategies` field. Combined ORB/VWAP baselines are labelled complete-system comparisons.
- Failed, incomplete or tampered runs remain listed as unavailable; they cannot disappear from the scorecard. Re-running both arms on the same new code preserves the provenance guard.

### T21 — Independent scorecard values and units (BR-06)

- Construct a tiny fixture with known fees, one losing arm, one zero-trade arm and one failure. Check net cash change, trade count and unavailable metric states independently.
- For an equity sequence `1000 → 1200 → 900`, session-end peak-to-trough decline is `300`, fraction `0.25`, displayed percentage `25%`. Preserve existing stored metric conventions: the current field named `max_drawdown_pct` stores a fraction. Do not silently change it or display `0.25%`.
- Mark drawdown as session-end diagnostics; do not claim worst intraday drawdown. No loss/no trades, unscored promotion and incomplete evidence have explicit states, never fabricated infinity, zero loss or pass.

### T22 — Real-trade reconciliation worksheet (BR-07)

For each chosen opportunity retain: dataset IDs, session/symbol, source interval, modeled availability, decision/readiness time, scanner/strategy evidence, entry/exit reference, quantity/rounding, stop/target, fees/adverse costs, cash/reservation transitions, independent expected values, observed values and discrepancy disposition. Include at least a rejected/no-fill opportunity as well as fills when available. If the sample contains no fills, say so and leave fill reconciliation unverified; do not fabricate a passing example.

Verify both an identical offline rerun and a controlled one-setting change. A reproducible result remains a single historical sample, and a loss is a valid study outcome. Leave final data unopened.

### T23 — Additional strategy contract (BR-08)

- Test hand-specified trigger/non-trigger examples, exact equality/crossing, warmup, stale/missing inputs, deadline, expiry/rearm, session reset, gap fills, stops/targets and timed exits.
- Verify rejected proposals leave cash/reservations consistent and scanner removal does not disable position protection.
- Exercise the actual public builder/runner/report path and full future-prefix invariance with an effective negative control. A strategy cannot import the data client, receive future arrays or mutate the account.

## Suggested focused commands after each file exists

These reference **proposed test files**. Do not run them at the planning-only base and report their absence as an application failure.

```bash
uv run pytest tests/test_diagnostic_journal.py tests/test_run_record.py -q
uv run pytest tests/test_streaming_run_evidence.py tests/test_public_sessions.py tests/test_trade_condition_execution.py -q
uv run pytest tests/test_diagnostics_cli.py tests/test_report.py -q
uv run pytest tests/test_data_preflight.py tests/test_alpaca_pipeline.py tests/test_holdout_boundary.py -q
uv run pytest tests/test_experiment_plan.py tests/test_historical_comparison.py tests/test_multi_session_evaluation.py -q
```

## Operator sequence after implementation

1. Check the status of the relevant ticket and use the implemented CLI help. New names in this pack are specifications until shipped.
2. Copy the existing Alpaca templates into an ignored local location such as `runs/pilot/inputs/`. Fill explicit choices and resolve cache paths relative to those files. Set credentials locally; never paste them into a ticket or commit them.
3. Authorize a small development acquisition including its history window, run the existing `rsys download`, then the proposed `rsys check-data`. Investigate coverage/action warnings before interpreting a result.
4. Run the existing `rsys backtest --config ... --out ... --report`. Use proposed `status` and the HTML report for interpretation; keep trace/manifest/run IDs for investigation. Unexpected failure requires diagnosis, not deleting/retrying until a green outcome appears.
5. Complete the reconciliation worksheet before using proposed `rsys experiment` with a preregistered finite plan. Leave final evaluation blocked.

## Required reviewer handoff

Use [the repository handoff](handoff.md). Include ticket ID, exact diff, acceptance IDs satisfied, commands and exit results, actual pass/skip counts, artifact references, platform tested, injected failure results, independent expected values, compatibility effects, review findings resolved and remaining unrun external checks. The reviewer must distinguish a code defect from incomplete data, an expected strategy loss, a proposed research choice and an operational limitation.

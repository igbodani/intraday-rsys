# Backtest readiness: development tickets

Read [the plan](backtest-readiness-plan.md) first. Everything below is proposed work, except references explicitly labelled existing. Test IDs refer to [the testing plan](backtest-readiness-tests.md). Paths beginning with a package name below are relative to `src/rsys/`. New file/test names are suggested ownership locations, not claims that those files already exist.

## BR-00 — Establish the checkout and freeze the technical contract

**Outcome:** the engineer knows the starting behavior and has an implementable logging/lifecycle design that respects existing frozen evidence.

**Owners:** architect designs; engineer runs checks and records the decision; reviewer challenges the contract. No new research choice is needed.

**Steps**

1. Inspect branch, HEAD, working edits and the latest source diff. Confirm the pre-data repairs are present rather than relying on a branch name. Run the baseline commands in test T00. Preserve unrelated edits.
2. Trace `cli/commands.py` → `application/run.py` or `historical.py` → `batch.py` → runners → storage/reporting. Identify every trace append, preparation step, artifact write and failure handler. Include the legacy version-1 run path.
3. Record a short design in the ticket handoff: invocation/run ID correlation; canonical trace encoding/order; journal durability boundary; terminal-state authority; redaction; sink failure behavior; schema compatibility; and import direction.
4. Use this bounded design: an early invocation directory under `out/invocations/<id>/` retains sanitized request identity, owner metadata and operational events before preparation. Once resolution succeeds, link that invocation to the existing research run and attempt directory. If convenient, allocate the existing run-ID format once and let `start_run()` accept it as an optional validated ID. An invocation and its linked run are one attempt, never two counted trials. Download attempts keep their existing public manifest location and link it to the invocation.
5. Do not overwrite `RunRecord.resolved_config` or its immutable hashes after creation. Early preparation failures can have an invocation without a resolved run; status/recovery must expose that explicitly. If a different design is necessary, document how it preserves these same observable guarantees before implementation.
6. Keep the SQLite run record authoritative once present. Operational stage names such as `preparing`/`finalizing` are not new terminal research outcomes. For downloads without a run record, use an atomic final manifest. Define what happens if the process dies between artifact creation and terminal commit.

**Acceptance:** T00 and a reviewed design addressing every item above. Record actual baseline failures separately; do not repair unrelated behavior under a documentation ticket. The existing two-session bar fixture ends at `1022.34800`, and the trade fixture at `1034.14420`, starting from `1000`; these are software fixture expectations only.

**Next:** BR-01. Do not wait for Alpaca credentials to continue offline work.

## BR-01 — Append-only operational journal and early invocation evidence

**Outcome:** an operator can see that a command started and what stage it reached, even when preparation fails before simulation.

**Likely files:** new `research/diagnostics.py` writer; plain journal records/enums in `research/models.py`; orchestration in `application/historical.py`, `application/batch.py`, `application/run.py`; existing redaction helpers in `research/experiments.py`. A data client may accept an optional progress callback, but must not import research/application code.

**Steps**

1. Create the invocation ID and persist `invocation.json`, sanitized request metadata and `diagnostics.jsonl` before any cache/network/expansion work. For malformed input retain the path and readable-byte hash, not arbitrary raw bytes or secrets. Do not log credentials, authorization headers, full environment dumps or raw provider exceptions.
2. Define a versioned JSONL record with `schema_version`, `invocation_id`, optional `run_id`, monotonic sequence, UTC wall time, level, stable event code, stage, and an allowlisted details object. Include simulation time only as an explicitly named separate field when relevant. Wall time never drives a strategy or enters the semantic trace hash.
3. Support named events for invocation start, configuration/preflight, permitted/denied data access, cache hit/read, acquisition page/retry counts, dataset completion, session start/end, failure and artifact readiness. Record bounded IDs/counts and sanitized error codes; avoid dumping all bars/history into each diagnostic line.
4. Append one complete UTF-8 JSON record per line. Flush every completed record; sync lifecycle, failure and declared checkpoint boundaries. Inject writer/clock hooks for tests. Document that process-kill preservation covers the flushed prefix, not an unacknowledged write or every hardware/power failure.
5. Use atomic replacement for small state summaries. Never rewrite the append-only journal or truncate an older attempt. Concurrent commands have distinct invocation directories and IDs.
6. Redact before serialization and console output, including nested configured secrets and exception strings. Provider credential values remain in memory only. If secret knowledge is unavailable, omit free-text details rather than guessing a regex caught everything.
7. Treat required evidence-write failure as a failed command. Preserve the primary exception plus a sanitized secondary write failure when possible. If disk/permissions prevent all persistence, print a clear nonzero failure and the best available invocation/path; do not promise an artifact was saved.
8. Link the invocation to its resolved run/download record. Seal journal hashes at finalization without appending afterward. Avoid a false committed-success event: journal `ARTIFACTS_READY` before the authoritative terminal commit; status readers consult the committed outcome. A later console delivery problem does not rewrite a sealed financial result.

**Tests:** T01–T04, existing run-record/redaction tests. Proposed file: `tests/test_diagnostic_journal.py`.

**Done means:** a deliberately blocked preparation leaves an identifiable journal before the blocker is released; malformed input, denied warmup and provider failure leave distinct readable failure evidence; no injected secret appears in captured output/artifacts; a writer failure never produces successful completion.

**Not included:** trading trace streaming, automatic process restart, remote log services or notifications.

## BR-02 — Stream the existing semantic trace through the public runners

**Outcome:** already-emitted trading events survive an abrupt process termination, with the same deterministic results and ordering as before.

**Likely files:** `execution/bar.py`, `execution/trade.py`, narrow sink type in the owning model/interface module, `application/batch.py`, `application/run.py`, `research/diagnostics.py`, storage/comparison/report readers where versioned evidence fields are required.

**Steps**

1. Add an optional event sink to both account runners. Emit the same immutable/copy-isolated row at the existing trace append point, including every fill row exactly once. Default direct callers keep their current list/return behavior. Strategies and the ledger must not receive the writer or gain filesystem access.
2. Application orchestration attaches the session date and writes `trace.jsonl` incrementally in the same canonical order/encoding used today. Do not write it again from `batch.trace` at completion, double-write through both paths, or mix diagnostics into it. Streaming is durability work; retaining existing in-memory lists initially is acceptable and must be disclosed.
3. Persist complete original/resolved inputs once available and before executing a session. Update per-session output at session boundaries. Stream historical, version-2 synthetic bar/trade and legacy version-1 account paths. `rsys replay` remains a separate data-only interface; do not claim it gained account execution or crash streaming under this ticket.
4. Flush each semantic record. Sync at documented fill/session/failure boundaries. A sink exception stops the attempt without undoing already committed in-memory account effects or labelling the partial run successful. Retain known partial fills/positions when normal failure handling can still write them.
5. On normal success or handled failure, close writers, compute hashes over the actual on-disk bytes, write reports/manifest, then commit the authoritative terminal record. Keep the existing schema-2/3 reader path; add an explicit diagnostics version for new mandatory files. Never weaken existing trace/artifact verification.
6. For an abrupt death, keep the last valid prefix and mark its completeness unknown until recovery. A torn trailing line is disclosed; corruption in the middle is an integrity failure. Do not erase, silently repair or reseal the original evidence. No automatic continuation of the old simulation/account is permitted; retry as a new linked attempt.
7. Verify quiet/verbose modes, writer timing and progress frequency cannot affect proposals, reservations, fills, costs, equity, selected history or semantic hashes.

**Tests:** T05–T08. Proposed file: `tests/test_streaming_run_evidence.py`. Reuse existing public sessions, legacy run, causality, trade-condition, report and comparison tests.

**Done means:** a parent process can read a complete trace prefix while the child is still running; force-killing after a barrier leaves that prefix; a live owner is never recovered as dead; successful streaming matches the pre-change semantic baseline and independent account values.

**Not included:** reconstructing a complete account from a torn log, promising every unflushed event survived, or solving scanner snapshot memory growth.

## BR-03 — Operator progress, status, recovery and explanations

**Outcome:** a nontechnical operator can tell running, failed, incomplete and successful attempts apart and find the relevant evidence without opening SQLite manually.

**Proposed interfaces**

```text
rsys run|backtest|download ... --progress auto|on|off
rsys status --storage-root ROOT --id INVOCATION_OR_RUN_ID [--json]
rsys recover --storage-root ROOT [--id ID] --dry-run
rsys recover --storage-root ROOT [--id ID] --apply
```

These do not exist at this plan's base. Preserve existing invocations and stdout summary lines. `auto` may enable human progress for interactive stderr; explicit `on` is useful for redirected logs. Progress is rate-limited and goes to stderr, separate from machine-readable stdout.

**Likely files:** `cli/commands.py`, plain CLI/application result models, `application/diagnostics.py`, `research/storage.py`, `reporting/html.py`, runner/adapter observation points.

**Steps**

1. Show invocation/run ID, stage, current session, completed/total sessions when known, processed-event/fill counts and elapsed runtime. Report unknown totals honestly; no fabricated completion percentage or time estimate. Use an injected wall clock for tests.
2. `status` is read-only. Read the authoritative run when present and the early invocation otherwise. Report last observed progress time, owner state, outcome, completeness, error and artifact paths. Distinguish missing data, rejected input, an interrupted process and a strategy producing zero trades. Inspection exit zero means the status was read, not that the inspected run succeeded.
3. `recover` has separate dry-run/apply modes and reuses the existing liveness/transaction safeguards. Do not mark live local owners crashed, change sealed terminal outcomes, infer death from elapsed time alone, or automatically declare a different host dead. Report unknown ownership as requiring manual investigation. Handle orphaned preflight invocations as well as created research records, idempotently.
4. Capture existing exclusion/rejection reasons when they occur: no/missing/stale scanner history, rejected acceptance, risk halt, expired intent, unusable fill reference, and unavailable required exit data. Observe the actual returned adapter result rather than calling execution logic again. If a strategy simply returns no proposal, say `NO_PROPOSAL`; do not invent which internal rule failed.
5. Add a per-session funnel to reports: observed decision opportunities, scanner-eligible opportunities, proposals, acceptances, entry fills, exits and reason counts. Define the counted unit per field; repeated pending-order checks must not inflate proposal/trade totals. Provide stable reason codes plus readable text and links/references to original events.
6. Reports for zero-trade/failed/partial runs remain useful. Mark partial metrics clearly, escape all displayed input/error text, and never hide losing runs. Nonzero failure behavior remains compatible with current commands unless an explicit documented migration is necessary.

**Tests:** T09–T11. Proposed file: `tests/test_diagnostics_cli.py`. Include public subprocess tests, HTML escaping, no-trade controls and a real two-process recovery check.

**Done means:** a user sees progress before completion, receives an actionable failure summary, and can inspect/recover a dead attempt without converting it into a successful comparison candidate.

## BR-04 — Offline data preflight and resource inventory

**Outcome:** before running a strategy, a command explains whether the pinned local data satisfy the requested execution/history requirements and what will be read.

**Proposed interface:** `rsys check-data --config BACKTEST_JSON --out ROOT`. It is local-only, not a downloader or simulation.

**Likely files:** factor shared planning/validation from `application/historical.py`; reuse `application/dataset_policy.py`, `data/bar_cache.py`, calendar and history selection; new plain request/report records in `application/models.py`; CLI/reporting adapters.

**Steps**

1. Validate the same config, account/strategy settings, source identity, chronological sessions, action-review acknowledgement and full declared lookback search window as `backtest`. Extract shared behavior rather than maintaining a second, drifting validation implementation.
2. Authorize every execution and warmup date before opening or inspecting any dataset/alias. Retain allowed/denied accesses in the early journal. Unknown/final dates fail before cache reads. There is no HTTP client or credentials lookup on this path.
3. Check exact date/object pins, request/feed/mapping/raw-unit/calendar identity, object hash, schema and recomputed normalization/coverage. Missing objects fail clearly. Never repair data, silently refresh a cache, insert zero-volume bars or substitute older sessions.
4. Produce JSON plus a readable summary per date: role (execution/warmup/both), expected minutes, valid observed minutes, missing intervals, duplicate count, chosen object, source/timing basis and coverage status. Distinguish empty observations from verified zero.
5. Execution-day gaps and integrity/config errors are fatal. Warmup gaps retain the existing backtest semantics: explicit warnings/history unavailability, not a fabricated usable RVOL value. Report `can_execute` separately from history completeness and research validity; a permitted zero-trade run need not mean its data were useful for the study.
6. Inventory actual cache bytes, sessions and bars; give measured or clearly labelled estimated evidence/resource sizes with their basis. Do not claim that cache size predicts peak memory. Add an offline benchmark script using declared fixture workloads and record elapsed time, peak memory when available and output bytes. Unsupported measurement platforms report unavailable metrics.
7. Keep resource measurements operational, outside trading P&L and deterministic hashes. Do not hide a memory problem by dropping revisions, truncating evidence or reading fewer lookback dates. If the bounded pilot is too large, report it and create a separate compaction ticket.

**Tests:** T12–T14. Proposed file: `tests/test_data_preflight.py`; benchmark script may be `scripts/benchmark_backtest_evidence.py`.

**Done means:** check-data's eligibility matches the existing loader; a final-warmup sentinel is never opened; sparse execution data fail; history gaps are visible; no strategy is evaluated and no network request occurs.

## BR-05 — Explicitly enabled Alpaca validation harness

**Outcome:** the owner can validate one bounded provider sample without exposing credentials, opening holdout data or launching strategy selection.

**Owners:** engineer builds the offline harness/CLI evidence; researcher documents the provider contract and action/feed requirements; owner supplies access and approves the actual request scope.

**Steps**

1. Reuse existing `rsys download` and the cache client; do not create another downloader. Add/read the new progress/journal fields and supply an operator checklist with expected manifest paths and failure interpretation.
2. Require explicit symbol, entitled feed, raw units, symbol-mapping policy, supported 2024 dates, partition permissions, lookback budget and action review for subsequent backtesting. Include all warmup dates, never broaden the date range to satisfy missing data automatically. No paid feed fallback.
3. Document environment-only credentials. Any new integration test must require both an explicit integration selection and explicit enablement, plus a local request file. Having credentials in the environment alone is insufficient. Default `pytest tests/ -q` and CI remain offline even when credentials happen to exist.
4. Exercise the same harness with fake transport for pagination, empty pages, numeric duplicates, bad OHLC/timestamps, 401/403, bounded 429/5xx retries, interruption and partial-download reuse. Log sanitized status/retry counts and preserved dataset identities. Do not log request headers or authentication values.
5. At the owner's explicit live invocation, acquire only the declared development sample, run BR-04, and retain a provider-validation record: request/feed identity, dates, object hashes, coverage, retrieved source-contract date, observed timestamps/numeric fields and discrepancy notes. Each checklist item is `verified`, `failed` or `not_run` with evidence. Treat an action attestation as an attestation, not an automated audit.
6. Repeat acquisition without refresh to prove cache reuse; simulate offline with HTTP denied to prove no requests occur during backtest. Refresh is a separate explicit acquisition preserving previous pins. No live strategy optimization or paper orders.

**Tests:** T15–T17; extend `tests/test_alpaca_pipeline.py`. Any proposed `tests/integration/test_alpaca_sample.py` must be registered/excluded from default offline execution and fail safely when requested without valid enablement/configuration.

**Done means:** software checks pass without credentials. The real-provider gate is complete only after the owner-enabled sample and evidence review pass. If no access exists, finish the software and explicitly leave live verification outstanding.

## BR-06 — Finite experiment plans and honest comparison reports

**Outcome:** one declared plan runs a baseline plus a finite list of variations, retains every attempt and produces a scorecard without selecting a winner or changing research assumptions silently.

**Proposed interface:** `rsys experiment --plan PLAN_JSON --out ROOT`. Reuse `backtest`, run storage and `compare_run_pair`; do not add a second simulation engine.

**Owners/files:** architect defines the versioned plan; application orchestration and plain schema in `application/models.py`; trial links/records in research ownership; reporting composes existing metrics. Quant researcher verifies study isolation and decision dependencies.

**Plan contract**

- Required: schema version, stable study ID, hypothesis, study kind, base backtest config reference, exact finite named variants, permitted comparison axis, explicit trial limit/counting convention, and failure policy (`stop` or `continue`). No default search budget or Cartesian parameter expansion.
- Resolve paths relative to the plan. Represent changes as a validated sequence of known key paths and literal values; no arbitrary Python, expressions or unrestricted patching of partitions/source identities.
- Study kinds distinguish scanner, entry, exit, costs, account and complete-system baseline comparisons. Map eligible settings to their actual declared axis. `strategies` alone is too broad to prove entry-versus-exit isolation; validate which parameters changed and label complete-system comparisons accordingly.
- Dates, source objects, source/feed/timing basis, account assumptions and code must remain equal except for the declared permitted axis. Changes to the history search window that alter required objects remain a data-identity mismatch; never weaken comparison to make them pass.

**Steps**

1. Record the original plan/hypothesis and all planned variants before executing any arm. Resolve/validate the entire plan and allowed date scopes before cache reads. Reject unknown keys, duplicate names, invalid variants, undeclared combined changes and insufficient explicit budget without running an earlier valid arm first.
2. Freeze base config/data/code identities; instantiate each variant deterministically and validate it through existing loaders. Capture fully resolved settings and plan/parent links at child-record creation, respecting immutable fields. Both arms must run on the same source revision.
3. Execute serially for the initial slice. The supported counting convention counts baseline/variant execution attempts, including failed preparation and retries; the parent coordinator and standalone status/data checks are not extra strategy trials. All invocations still remain retained. Link each child's early invocation and resolved run as one trial. Enforce the supplied budget across reruns within the same study registry, reserving a slot before child preparation. Do not reset it on restart or silently exclude failed attempts. A new study ID or another storage root is not security-proof global trial accounting; disclose that limitation and retain references to externally run attempts.
4. Keep explicit planned/running/success/failed/interrupted/not-started/blocked-budget trial states. Failures are visible under either failure policy. Interrupted plans leave remaining variants unstarted. Never present a partial experiment as a completed success or resume an old partial simulation as if nothing happened.
5. Compare only complete sealed compatible runs through the existing comparison service. A failed or tampered arm appears as unavailable with its reason; do not omit it or assign zero return. Preserve old manifests and feed-delay/scanner-profile behavior.
6. Report existing measures: net change after modeled costs, completed trades, win rate, expectancy, profit factor, exposure and the currently implemented **session-end** drawdown diagnostics. Name their units and limitations. Do not relabel session-end drawdown as the worst intraday drawdown; MFE/MAE, confidence intervals and common-entry exit attribution are separate future features.
7. Keep promotion status `unscored`. ORB versus VWAP full-account runs compare complete systems, not a causal proof that only entry improved. An exit-setting experiment reruns the full portfolio because holding times affect later affordability. A true common-entry diagnostic requires separately specified observation windows and implementation.

**Tests:** T18–T21. Proposed file: `tests/test_experiment_plan.py`. Use tiny offline cached fixtures with a losing arm, zero-trade arm, failure and a changed-cost negative control. A user-supplied trial limit of three in a fixture is a test value, not an approved research budget.

**Done means:** a known finite plan retains all arms and failures, enforces its declared study budget, rejects incompatible comparisons, reproduces exact fixture outcomes and never reads final data or calls the network.

## BR-07 — Reconcile a real pilot before broad strategy testing

**Outcome:** documented evidence that the downloaded sample and a few simulated trades match the declared model, followed by a bounded development study. This is an operator/research ticket, not a claim that software tests prove profitability.

**Steps**

1. Complete the owner-input checklist in the plan before looking at comparative outcomes. Preserve explicit development/validation/final partitions, hypothesis, trial list, controls and intended evaluation method. Keep final access blocked.
2. Use BR-05's verified single-stock 2024 sample and BR-04's report. Inspect first/last bars, early-close handling when present, feed/mapping identity, coverage and corporate-action evidence. Explain any history exclusion before interpreting zero trades.
3. Run the unchanged ORB baseline first, then VWAP as a separately labelled whole-system baseline. Existing strategy IDs are `orb_close_cross_v1` and `vwap_touch_hold_v1`; completed bars remain their decision input.
4. For selected filled and rejected opportunities, manually reconcile source interval/availability, scanner inputs, signal time, order readiness, eligible fill reference, quantity, stop/target, each fee/adverse adjustment, cash/reservation effects and exit reason. Use Decimal and documented rounding; do not accept a numerical tolerance invented after a mismatch.
5. Repeat an identical run with HTTP disabled. Expect equal decisions/fills/equity/semantic hashes, while attempt IDs, operational times and diagnostic hashes may differ. A successful repeat is reproducibility evidence, not an independent market sample.
6. Execute the preregistered finite development variants. Vary one declared aspect at a time and retain weak, losing and failed outcomes. Do not tune on validation/final outcomes or retrospectively change success criteria. Do not claim promotion until D17's required protocol is frozen and implemented.
7. Measure run memory/output size before adding dates/lookback depth. Record discrepancies and open bugs as separate reproductions; stop dependent research if accounting/data fidelity is unresolved.

**Tests/evidence:** T22 and a retained reconciliation worksheet with dataset/run IDs, source references, independently calculated values, observed values and a pass/fail/unresolved result per check. Do not commit downloaded bars or credentials.

**Done means:** no unresolved pilot accounting/data mismatch, a reproducible bounded experiment, all failures visible and honest unscored conclusions. No required profitable result.

## BR-08 — Add one specified momentum strategy, then repeat validation

**Outcome:** one additional strategy with an explicit definition and independent fixtures, integrated through the existing pipeline. No strategy is chosen by this planning pack.

**Researcher must supply:** strategy ID/version; eligible universe/data session; warmup and required features; exact completed-bar entry predicate; equality/crossing rules; deadline; initial stop and target construction; hold/flatten rules; expiry/rearm and daily fill limits; missing/stale-data behavior; cost assumptions; and a hypothesis/control. State explicitly disabled behavior. Missing definitions block only this strategy ticket.

**Engineer steps:** add its plain config/state records to `strategies/models.py` and behavior beside ORB/VWAP; implement the existing `StrategyLike` interface; extend shared builder/validation/registration paths; reuse scanner, acceptance, execution, risk and evidence unchanged. Supply hand-calculated trigger/non-trigger, equality boundary, invalidation, gap, timer, rejection and session-reset examples. No broker access, direct account mutation, future arrays or new execution engine.

**Tests:** T23 plus existing account/causality tests. Include a full earlier-trace invariance test with an effective past-input negative control, both fill/rejection outcomes through the public path and a finite experiment plan. Rerun baseline and candidate on the same updated code before comparing.

**Done means:** the rule is specified, software assertions pass, integration/reporting are reproducible and independent review finds no blocker. Historical performance remains a separate unscored research result until the study protocol supports more.

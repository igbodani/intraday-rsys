# R3 integration repairs — coding-agent handoff

> Historical planning or implementation record. Starting-state descriptions and paths reflect its original revision. Use [ARCHITECTURE.md](ARCHITECTURE.md) for current packages and [CURRENT_SYSTEM_AND_NEXT_STEPS.md](CURRENT_SYSTEM_AND_NEXT_STEPS.md) for current status. Behavioral requirements still apply unless superseded by the decision register.

> Historical handoff/completion record. The later review reproduced failures despite the tests recorded here. `R3_REPAIR_RESULTS.md` and the R3SAFE requirements now supersede runtime claims, completion counts and migration instructions in this document. Preserve this file for traceability; its checked boxes are not independent proof of the newer guarantees.

Prepared: 2026-10-28, America/Denver. Reviewed source: `intraday-rsys(2).zip`, commit `7084d4a`.

Suggested repository location: `docs/R3_INTEGRATION_REPAIR_TASKS.md`.

## 1. Assignment and completion boundary

Repair the concrete failures below and finish the R3 acceptance gate before beginning M2. Preserve the useful corrections already implemented. This document refines the remaining work in `docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md`; it takes precedence over claims that R3 is already complete. Existing product goals, strategy definitions and research constraints remain in force.

The deliverable is a trustworthy offline synthetic replay with immutable strategy observations, correct bar histories, enforced settings, retained run evidence and independently meaningful acceptance tests. No ledger, fills, scanner, ORB/VWAP implementation, P&L, optimizer, broker connection or market-data acquisition belongs in this repair pass.

**Forming-bar decisions remain a required capability.** Preserve the existing closed-bar ORB/VWAP definitions as baselines, while proving that a separately identified synthetic intrabar observer can decide before a minute closes. Do not make the whole system closed-only to remove a difficult integration problem.

Before editing, read `AGENTS.md`, this document, the previous R3 handoff, and the relevant contracts, decisions, requirements, data rules and ticket documents. Inspect the current branch and preserve user changes. If the checkout is newer than `7084d4a`, verify the regression rather than undoing an existing correct fix.

Work through the tickets below in order, with focused tests and reviewable changes. Continue ordinary implementation work without repeated confirmation requests. Ask only for an unresolved research choice that actually blocks a required feature; continue independent tasks. Do not invent market assumptions to resolve a blocker. Do not commit or push unless separately requested.

## 2. What was independently verified

- All 348 existing tests passed in the available Python 3.12 environment, using pytest 9.2.0 and Pydantic 2.14.1.
- Both replay CLI examples succeeded: four events in the provider-bar fixture and eight in the trade fixture.
- Repeated baseline runs produced equal semantic hashes.
- A fresh frozen dependency installation and Ruff were not independently reproduced in the review environment. The coding agent must run and report these in its own environment.
- Separate probes demonstrated the failures in section 3. They require no market data or private credentials.

Retain the improvements already made: root-scoped source exclusions, lifecycle checks inside SQLite transactions, stronger bar/trade validation, safer reloaded-record exception handling, corrected equal-time latest-event selection, and separate field eligibility. The new work is primarily integration and remaining contract enforcement.

## 3. Failure inventory and expected regressions

Use these descriptions as executable acceptance specifications. Names are suggested test names, not existing tests. Tests should use valid event constructors and public runtime/CLI boundaries wherever possible.

| ID | Reproduced behavior at `7084d4a` | Required result | Suggested regression |
|---|---|---|---|
| R3I-01 | A context created after a 100-price trade returns 120 after a later trade; after closure, the old forming query becomes `None`. | The old context keeps the original snapshot and availability forever. | `test_saved_context_survives_later_trade_correction_and_close` |
| R3I-02 | A trade-built bar closes, but `context.closed_bars()` returns an empty list. | The authoritative builder's completed history is available through the common strategy view. | `test_trade_authority_exposes_completed_bars_to_closed_context` |
| R3I-03 | Adding 100 to every provider OHLC price leaves trace bytes and semantic hash unchanged. | Trace content identifies the actual normalized observations; an observable price change changes the semantic trace hash. | `test_provider_price_change_changes_trace_and_semantic_hash` |
| R3I-04 | Q1's trace row contains Q2's source ID after replay finishes. | Every row records only the quote version available at that row's key. | `test_quote_trace_prefix_is_unchanged_by_future_quotes` |
| R3I-05 | Replay creates no SQLite attempt/run ID; `--out auto` reuses a fixed directory. | Fresh retained attempt and artifact location for every execution, including failures. | `test_replay_cli_retains_distinct_attempts_and_failures` |
| R3I-06 | A trade at second 5 and unrelated heartbeat timer at second 6 produce `complete=true`. | Completion requires declared coverage and the correct boundaries; a timer alone is insufficient. | `test_early_unrelated_timer_cannot_complete_truncated_replay` |
| R3I-07 | A split between prices 100 and 50 is logged but yields a normal mixed-unit bar, high 100/low 50. | Apply the supported unit contract or explicitly block/invalidate the affected interval. | `test_split_cannot_silently_create_a_mixed_unit_bar` |
| R3I-08 | Unknown interval/rules and incompatible profile/authority settings pass replay validation. | Unsupported or incompatible settings fail before execution. Accepted settings control actual behavior. | `test_replay_settings_are_resolved_and_enforced` |
| R3I-09 | `synthetic_test_net_return:v1`, registered as `fixture_only`, satisfies a research gate field. | Fixture-only specifications remain D17 blockers for research. | `test_fixture_method_cannot_satisfy_research_gate` |
| R3I-10 | A correction with unknown conditions raises after changing the live price to 999. | Rejected events leave state, indexes, history and counters unchanged. | `test_rejected_correction_has_no_side_effects` |
| R3I-11 | Duplicate cancel creates revisions 1 and 2; reusing a bare trade ID across buckets redirects correction lookup. | Idempotent delivery and explicit scoped identity/conflict handling. | `test_duplicate_correction_and_identity_conflict_are_safe` |
| R3I-12 | The first late trade into an elapsed bucket is labeled `forming`; an earlier timer did not create a builder watermark. | The elapsed interval is not presented as currently forming. | `test_first_late_trade_respects_elapsed_boundary` |
| R3I-13 | Quotes are exposed through `latest_quote` even with quote support disabled; an invalid-quality provider bar reaches `closed_bars`. | Capability and quality policies are enforced at the public view. | `test_disabled_or_invalid_observations_are_not_usable` |
| R3I-14 | Comparator returns `match=true` for count 1 versus 999 without VWAP precision; incompatible adjustment modes can also match. | Counts are independent of VWAP options; incompatible units/as-of semantics are not comparable. | `test_parity_checks_count_and_comparison_prerequisites` |

Additional integration gaps established by inspection: observation cadence is not dispatched by the actual runner/CLI; phase-37/40 behavior is largely descriptive; closures depend on input timers; several configured policies are unused; source provenance is incomplete in replay manifests. Include these in the tickets below rather than treating the existence of classes as completion.

## 4. Ticket IR-01 — Make event application atomic and identities explicit

**Files:** `src/rsys/data/bar_builder.py`, `events.py`, `bar_rules.py`; builder/revision tests.

### Implementation

1. Validate the entire candidate event before mutation: series identity, quality, original reference, replacement values, condition classification, unit compatibility and retention availability. Compute candidate contributions first, then commit a single accepted state change. An exception from classification must not leave price/size partially changed.
2. Define an explicit trade identity scope supported by the adapter. The fixture scope must include enough source/instrument/session/venue information to avoid treating unrelated trades as one. If the same full identity is redelivered with a different bucket/time/payload, reject it as a conflict; do not overwrite the reference index. If venue-scoped IDs can collide, correction references must carry that scope.
3. Track accepted event identities and relevant payload digests for trades, corrections and cancels. Identical redelivery is an idempotent no-op: no new volume, revision, publication or observer invocation caused solely by the duplicate. A same-ID/different-payload event is a named conflict.
4. Preserve original and replacement identities. Distinguish omitted replacement conditions from explicitly empty conditions; `if event.conditions` cannot express both meanings. Reclassify corrected contributions when conditions/tape change. Unsupported correction shapes block explicitly.
5. Preserve deterministic aggregation ordering and the distinction between event time and availability. A bare string trade ID is not evidence of provider order; keep a declared fixture ordering rule and preserve source metadata for a future verified adapter.
6. Unknown/evicted references remain explicit. Retention must not fabricate empty snapshots. Document which contributor/history/index structures are bounded and which are retained as evidence; do not claim bounded memory merely because contributors are evicted.

### Acceptance

- Apply T1 = 100 × 10. Attempt a replacement at price 999 with an unknown condition. Compare complete state and publication history before/after rejection: identical.
- Apply the same cancel twice after closure: one accepted cancellation and one revision, with unchanged history on redelivery.
- Reuse an identity in another bucket: explicit conflict, or distinct identities if the declared scope legitimately differs. A correction cannot silently target the wrong bucket.
- Correct first/last/extreme contributors, conditions and sizes; verify exact OHLC/count/volume/VWAP accumulators.
- Keep existing worked-example arithmetic intact.

## 5. Ticket IR-02 — Make strategy contexts immutable and source-aware

**Files:** `views.py`, `replay_runtime.py`, `data/bar_version_store.py`, normalized observation types; causality and replay-input tests.

### Implementation

1. Introduce or consistently use an immutable full replay/publication key: availability, phase and declared sequence/tie metadata. A timestamp-only cutoff cannot represent earlier versus later phases at one instant.
2. Publish immutable normalized observation versions. Use a complete series identity, including instrument, provider, feed, interval and unit/adjustment contract; retain source authority and rule version/hash as provenance.
3. Build contexts from versions visible at their frozen key. They must not call live `builder.forming_bar()` or hold a mutable router as the source of query answers. Capturing immutable selected versions or querying an append-only store with a fixed key is acceptable. Ensure later arrivals cannot insert backdated versions that alter old answers.
4. Keep mutable aggregation state internal. Remove the ability to stamp current state with an arbitrary earlier `as_of_ns`; replace that API or reject historical misuse. A docstring warning that the caller must not advance replay is insufficient.
5. Unify completed-bar access for provider and builder authority. `closed_bars()` must return the selected versions of completed intervals from the authoritative series. It must include trade-built closures and honor `latest_version_asof`; do not hard-code `bar_revision == 1` while advertising that policy.
6. Preserve old decisions and first-close snapshots even when later revisions become visible to later contexts. A revised old bucket is not the current forming bucket.
7. Enforce authority, quality and capability at the view boundary. A comparison stream cannot leak into authoritative history. An invalid latest observation must not be silently treated as valid; expose an unavailable/exclusion reason according to the explicit policy. Disabled quote capability must not return raw quotes from the event list.

### Exact saved-context regression

Use a June 10, 2024 regular-session bucket starting at `13:30:00Z` (09:30 New York). T1 at +5 seconds is 100 × 10; T2 at +20 is 120 × 10.

1. Process T1 and save context K5. Assert forming close 100, volume 10, numerator/denominator 1000/10.
2. Process T2. Re-query the same K5: all values and version IDs remain unchanged. K20 returns close 120, volume 20, numerator/denominator 2200/20.
3. Process correction/cancel, closure and a later minute. Re-query K5 and K20 again; both retain their original values and forming status as known at their own times.
4. A context before T1 is unavailable. A closed-only context after closure contains the builder's completed bar. A later revision is visible only under the declared later cutoff.
5. Repeat at equal timestamps with different phases/sequences, and across two instruments/feeds. No query may cross the frozen key or series identity.

Do not satisfy this regression by retaining only a previously returned `BarSnapshot`; the saved **context itself** must remain safe on subsequent queries.

## 6. Ticket IR-03 — Complete scheduled replay, observers and coverage

**Files:** `replay.py`, `replay_runtime.py`, event/manifest models, calendar integration; event-order and integrated replay tests.

### Implementation

- Add explicit replay-window and input-coverage declarations to the resolved fixture configuration/manifest: relevant instruments/series, session/calendar version, interval range, source coverage and incomplete/known-no-trade states. A generated timer proves a scheduled boundary, not delivery completeness.
- Schedule required closures before dispatch, independently of another trade. Retain a monotonic clock/watermark even before a per-instrument builder exists. On creating a builder, initialize it consistently with that clock. First late arrivals cannot reopen elapsed intervals as currently forming.
- Preserve the existing phase contract. Pre-scheduled close publication is phase 20; actions 25; other timers 30; market observations 35; derived publications 37; observers 40. Do not generate a phase-20 event from a phase-30 callback after its phase has passed. Keep inactive account/execution phases inactive.
- For the supported availability-batch cadence, apply all same-time inputs, publish derived versions, then invoke eligible observers once on a frozen phase-40 context. For completed-bar cadence, invoke the close-only observer at the declared publication boundaries. Record a real observer result and its input-version references.
- Keep the existing same-phase derived-insertion guard. Original queued events can share a phase; newly derived events must target an allowed future phase/key. Use the same key semantics throughout stores, contexts and traces.
- Corporate actions must invoke supported action/invalidation logic. For a split/unit transition that R3 cannot safely support, explicitly block or invalidate the affected interval/run with a structured reason. Logging the action and continuing a valid-looking mixed-unit bar is unacceptable. Later-known essential actions cannot rewrite past decisions.
- Determine completion from the requested replay window, validated coverage and required boundary processing. Do not use `any(TimerEvent)` or an unrelated later timer as a success predicate. Early EOF, missing required input or unresolved unit dependencies produce a retained non-successful attempt with a specific reason.

### Acceptance

- T1 at second 5 plus a heartbeat at second 6 does not complete a requested minute.
- Complete coverage of a quiet final minute allows closure without a later tick; missing delivery remains unavailable/incomplete.
- A first old-bucket trade arriving at second 65 is not currently forming, even if no builder existed at second 60. Preserve the earlier absence/coverage observation and publish the later knowledge at its actual availability.
- Same-boundary old-bucket late arrival updates after the scheduled closure, while a trade with market time exactly at the boundary belongs to the next bucket.
- A split between 100 and 50 cannot produce an ordinary valid mixed-unit bar. Exercise both known-at-boundary and later-known actions through the runner.
- A fixture-only threshold observer crosses 101 before minute close using the current forming version. It is called by the runner/CLI, not by a separate test-only loop over the router.
- A close-only observer never receives forming observations. With multiple symbols, phase-40 evaluation sees the complete same-time batch.

## 7. Ticket IR-04 — Enforce configuration and research boundaries

**Files:** `config.py`, `registry.py`, `replay_io.py`, event normalization and config tests.

### Implementation

1. Validate cross-field combinations, not only each field's allowed strings: input mode/profile, authority/available input, interval/calendar/session, observation mode/cadence, revision policy, quote capability and unit policy.
2. Resolve rule IDs, policy IDs and methods to actual implementations/specifications of the correct purpose and evidence class. Empty or unknown identifiers fail. A parsed name must not advertise unsupported behavior.
3. A research gate requires a research-approved specification. The current `_is_method_reference` returns true for `fixture_only`; correct this and test through `ResearchConfig` plus `validate_config`. Keep unknown research methods blocked rather than registering invented methods. Synthetic registry fixtures may be accepted only through an explicitly synthetic validation context.
4. Record actual specification content/version hashes, not just a hash of the method name. Validate purpose compatibility where fields require different kinds of method.
5. Every accepted configuration field must be consumed by runtime behavior or explicitly rejected as unsupported. In particular wire cadence, availability model, quote quality/freshness, authority, revision policy and unit/session policy.
6. Require honest timing evidence: a recorded-arrival claim needs recorded receipt information. Otherwise declare modeled availability explicitly. Do not accept the `recorded` label alone as proof. Synthetic fixtures may model recorded receipts, but their synthetic evidence class remains visible.
7. Validate regular-session fixtures against the calendar. The current fixtures use `09:30Z` while declaring regular NYSE data; June 09:30 New York is `13:30Z`. Correct fixture times and expectations rather than teaching the calendar the wrong session.
8. Resolve input paths under a documented portable rule. Ensure a config can be invoked outside the repository's working directory. Safely handle malformed JSON, invalid UTF-8 and unsupported event kinds without leaking submitted secrets or uncontrolled tracebacks.

### Acceptance

- Unknown interval, unknown rule set and unsupported input/profile/authority combinations fail with named configuration errors before execution.
- For combinations that are intentionally supported, test actual runtime behavior; changing only metadata is insufficient.
- A `fixture_only` method does not remove its research D17 blocker. Replace the existing test expectation that a synthetic method makes a research gate valid; retain a separate legitimate synthetic-registry test.
- Quote policy names resolve or block. A disabled-quote run has no usable quote observations. Timing labels match evidence, and incompatible event declarations fail.
- Changing a supported observation cadence changes callback scheduling as specified.
- Replay from outside the repository resolves its input correctly; decoding/validation failures are controlled.

## 8. Ticket IR-05 — Connect actual replay attempts to evidence and safe output

**Files:** `cli.py`, `replay_io.py`, `experiments.py`, `storage.py`; CLI/evidence tests.

### Attempt lifecycle

Validate basic configuration before starting. When an execution attempt begins, create a fresh run record with resolved settings, code/lock/rule/config hashes, input content identity or an explicit unavailable-input status, timing evidence and declared observer/cadence. A path string alone is not a dataset hash. Do not bypass `start_run`/`run_context` or duplicate the storage lifecycle in the replay CLI.

Retain execution, input, coverage and persistence failures under the existing lifecycle. Map incomplete replay to an explicit non-success terminal outcome/reason without inventing a successful run. Preserve original errors and safe redaction. Handle interruption honestly.

Use a unique attempt directory containing the fresh run ID. Treat `--out` as an artifact root, or explicitly reject overwriting an existing attempt directory. Define and test the behavior for both explicit paths and `--out auto`. Do not rewrite older attempts or delete failed ones. Link the database record to artifacts and their hashes; finalize success only after required evidence is durably written. If writing artifacts fails, preserve a truthful failure where storage remains available.

### Trace content

Emit immutable trace records as events/publications/observer outputs occur, or construct them exclusively from captured immutable versions. Never query a final live quote/builder when reconstructing earlier rows.

Include the information needed to reconstruct what the observer knew:

- Stable event/publication keys and identities, source/series identity and normalized payload or an immutable resolvable payload reference/hash.
- Provider-bar OHLCV, count/VWAP where present, interval bounds, revision, units and quality.
- Trade-built version identity, lifecycle state, aggregates and contributing/update-event references.
- Quote version, bid/ask and sizes/units, quality/freshness policy result at the observation key.
- Action/invalidation and coverage/exclusion outcomes.
- Observer identity, capability, cadence, decision key, selected input versions and result.

Separate deterministic semantic trace content from fresh attempt IDs and wall-clock execution metadata. Hash the complete canonical semantic representation, and separately hash config/data/code provenance. Provider-price changes must not disappear from evidence; quote-prefix records must not change when future quotes change. Use canonical Decimal and nanosecond serialization.

### Acceptance

1. Run each fixture twice through the actual CLI. Two distinct persisted run IDs and attempt directories exist; both attempts remain readable; identical semantic inputs produce equal trace hashes.
2. Add 100 to every valid provider OHLC value, preserving IDs/times. The normalized input identity and semantic trace hash change. Verify the trace contains the changed values or verifiable references.
3. Replay Q1 then Q2. Q1's trace references Q1 only. Perturb or remove Q2 and compare the entire earlier trace prefix: unchanged.
4. Inject an input/handler/artifact-write failure and an interruption. Check persisted status, history, retained diagnostic evidence and output collision safety. Do not assert only that files exist.
5. Invoke `--out auto` twice; prove that the first run's bytes and database record remain unchanged.

## 9. Ticket IR-06 — Finish quote and parity contracts

**Files:** `data/quotes.py`, `views.py`, `parity.py`, event models and their tests.

### Quotes

Retain versioned quote state at full replay keys and return the selected as-of version with its explicit condition/freshness result. Enforce configured capability and policy. Preserve source/feed/instrument/venue semantics and size units; do not assume a generic size is shares when its source declares something else.

Reject or flag malformed/invalid observations before they become usable. Keep locked/crossed/missing-side/stale states visible according to an explicit fixture policy. Do not silently carry a future/current quote into an earlier context. Quote updates must never alter stock trade OHLC, volume or VWAP. Disabled support must not leak raw quote data through a separate event-list accessor.

### Parity

Compare independent count fields whenever present, regardless of whether VWAP precision was requested. Missing required information produces an explicit not-comparable/partial result; it must not silently certify fields that were skipped.

Require compatible provider/feed/instrument/session/interval/unit/adjustment/quality/rule basis and as-of semantics. Carry missing provenance in normalized versions so these checks are possible. A revised version cannot be matched to an earlier first publication just because OHLCV happens to agree. Revision numbers alone are insufficient evidence that two sources represent the same knowledge cutoff.

Use exact Decimal comparisons where definitions match; apply the declared VWAP precision/rounding policy only to VWAP. Do not widen tolerances to hide disagreement. Preserve the distinction between a synthetic comparator test and verified provider parity.

### Acceptance

- Count 1 versus 999 produces a mismatch with default comparator arguments and with VWAP checking enabled.
- Equal OHLCV with different adjustment/unit basis is not comparable.
- Earlier versus later knowledge versions are not comparable unless aligned by the declared policy.
- Missing required fields/metadata and invalid-quality input do not return an unconditional match.
- Quote freshness, disabled access, same-time ties and old-context queries work through the actual runner/view, not only `QuoteState` directly.

## 10. Ticket IR-07 — Prove the integrated contract and reconcile status

### Tests that must run through the delivered path

Extend the existing suites or create a focused `tests/test_r3_acceptance.py`. Use real config loading, runner, version store, public contexts, trace writer and evidence lifecycle. Avoid a second test-only runner that silently supplies the missing behavior.

Required integrated checks:

- Saved-context stability after trades, corrections, close, eviction and future actions.
- Future perturbation of trades, quotes, corrections targeting earlier trades and actions. Compare the full earlier semantic trace and observer outputs, including source/version references. Retain a deliberately leaky negative control that fails the same comparison.
- Equal timestamp/different phase visibility and complete cross-symbol batching.
- Shuffled input order with unchanged identities/order metadata: identical semantic outputs. Changing actual recorded order is a different declared input, not the same experiment.
- One authoritative source when provider and builder streams coexist; no additive volume or comparison-stream leakage.
- Multi-symbol, multi-minute data including quiet intervals, late first arrivals, corrections, missing coverage and an unsupported unit transition.
- A runner/CLI-driven synthetic intrabar decision before close and a separate closed-only observation path consuming trade-built completed bars.
- Two retained successful attempts and a retained failed/incomplete attempt, with independently verified payload/config/provenance hashes.

Use a bounded synthetic stream to record event count, elapsed time and approximate memory footprint after correctness passes. Do not claim unlimited day/week scale from the current tiny fixtures or introduce a large streaming framework merely to satisfy a performance slogan.

### Commands

Start with the existing lockfile. Use `uv sync --frozen` when available; do not regenerate the lock solely to bypass an environment failure. Run focused tests while implementing, then the full integration checks:

```bash
uv run pytest tests/test_config.py tests/test_run_record.py
uv run pytest tests/test_market_events.py tests/test_bar_builder.py tests/test_bar_revisions.py
uv run pytest tests/test_event_order.py tests/test_causality.py tests/test_replay_inputs.py
uv run pytest tests/test_quote_state.py tests/test_provider_bar_parity.py tests/test_replay_cli.py
uv run rsys validate --config configs/fixture.yaml
uv run rsys validate --config configs/baseline_pending.yaml
uv run rsys replay --config configs/replay_bar_fixture.yaml --out auto
uv run rsys replay --config configs/replay_trade_fixture.yaml --out auto
uv run pytest tests/
uv run ruff check .
```

Add newly created acceptance test files to targeted commands and the full suite. Repeat both replay commands for the distinct-attempt check. Research validation is expected to exit 2 with the exact justified blocker set; do not target a blocker count or suppress failures. Report unavailable tooling honestly. Do not claim missing/skipped acceptance cases as passes.

### Documentation and final gate

Update README, tickets, requirements and relevant contracts/decisions so they describe delivered behavior without contradictory old paragraphs. Map R3I-01…14 to tests using stable existing/new requirement IDs; these handoff IDs need not replace the project's IDs.

The following checklist governs completion:

Checked 2026-10-29 against this repository (381 tests passing, `uv run ruff check .` clean, no commits made this pass):

- [x] All R3I-01…14 regressions pass through the relevant public boundary. (See `docs/requirements.md`'s updated R3I-to-test mapping; each has a dedicated regression demonstrating the pre-fix failure.)
- [x] Accepted configuration policies control execution; unsupported capabilities block clearly. (`bars.authority`, `quotes.enabled`/freshness, `revision.retention_limit`, `coverage.*` are all consumed by `run_replay_from_config`; `replay.input_mode`↔`bars.authority` cross-field compatibility enforced. `observation.mode`/`cadence` are validated and consumed by `ReplayRunner.run_with_observers`, but the CLI does not yet auto-select or invoke a specific observer from config alone -- see the honest gap noted below.)
- [x] Saved contexts remain immutable; both bar authorities supply correct completed history. (IR-02: `forming_bar`/`closed_bars` sourced from the immutable `BarVersionStore`; `tests/test_r3_acceptance.py::test_saved_context_stability_integrated`.)
- [x] Closure/coverage/action handling is enforced by the runner. (IR-03: declared `CoverageConfig` + strengthened `assert_stream_covers_boundary`; `SeriesRouter.apply_action`/`TradeBarBuilder.invalidate_if_straddled`.)
- [~] Actual runner/CLI observers prove forming-bar and closed-bar paths. `ReplayRunner.run_with_observers` is a real, tested, runner-driven mechanism (`tests/test_r3_acceptance.py::test_runner_driven_intrabar_and_closed_only_observers_together`), but `rsys replay` (the CLI) does not itself register or invoke any observer -- no strategy/observer selection concept exists in `ReplayConfig` (that selection is M2's concern). Honest partial: the mechanism exists and is proven through the runner directly, not yet through the CLI end to end.
- [x] Replay attempts, failures, payloads and provenance are retained without overwrites. (IR-05: `start_run`/`run_context` wired into `run_replay_from_config`; `tests/test_r3_acceptance.py::test_two_successful_and_one_failed_attempt_all_independently_retained`.)
- [x] Full integrated causality/determinism tests and required lint checks pass. (`uv run pytest tests/` -> 381 passed; `uv run ruff check .` -> clean.)
- [x] Provider verification, market assumptions and optional unsupported features remain honestly labeled. (Docstrings throughout `quotes.py`/`parity.py`/`bar_rules.py` state fixture-only/non-provider-verified status; D22 decisions.md note preserved from the prior pass.)
- [x] Existing source/lifecycle/validation fixes remain covered; no unrelated user changes were overwritten. (Test count only grew, 348 -> 381; no existing test was deleted or weakened to pass.)

If a required item fails, report R3 as incomplete and identify its next concrete repair. Do not start M2 to compensate for the missing gate. Once the gate passes, the next ticket is M2-01: ledger and conservative execution against F01–F17. Position-management-after-scanner-removal remains a future implementation requirement until that layer exists.

Final report: changed files grouped by IR ticket; R3I-to-test mapping; before/after evidence; exact executed commands/results; distinct run IDs and artifact paths; semantic/data/config/code hashes; compatibility/migration notes; unresolved dependencies; and whether the R3 gate actually passed. Avoid using test count alone as proof of correctness.

## 11. Copy-ready coding-agent prompt

> Implement `docs/R3_INTEGRATION_REPAIR_TASKS.md` against the current checkout. Read the referenced project rules and contracts first. The reviewed baseline is `7084d4a` with 348 passing tests; preserve correct newer work and existing user edits. Execute IR-01 through IR-07 one focused ticket at a time. Add regressions for R3I-01…14, fix the actual public runner/view/CLI paths, and run the integrated acceptance checks. A new helper, comment, or manually driven test loop does not satisfy a missing runtime behavior. Keep this work offline and synthetic, preserve forming-bar decision capability and the closed-bar ORB/VWAP baselines, and do not invent market decisions. Retain attempts and failures. Continue routine implementation without repeated approval requests. Stop after the R3 gate; do not implement M2 in this pass. Finish with the defect-to-test mapping, exact commands/results, retained run artifacts/hashes, remaining blockers and a truthful completion status. Do not commit or push unless separately requested.

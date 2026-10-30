# Intraday research system: corrections and R3 implementation handoff

> Historical handoff/completion record. The later review reproduced failures despite the tests recorded here. `R3_REPAIR_RESULTS.md` and the R3SAFE requirements now supersede runtime claims, completion counts and migration instructions in this document. Preserve this file for traceability; its checked boxes are not independent proof of the newer guarantees.

Prepared: 2026-10-29 UTC. Review baseline: uploaded `intraday-rsys(1).zip`, Git HEAD `3bb1dad`.

Suggested repository destination: `docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md`.

## 1. Objective, authority and stopping point

Correct the verified integrity defects, finish the replay contracts, and deliver an inspectable offline synthetic replay that supports both completed bars and forming bars built from available trades.

This is an implementation specification. The accompanying Copilot prompt authorizes the coding agent to implement it. This handoff does not claim the corrections are already implemented. It supersedes the immediate ordering and completion claims in the older implementation summary, while retaining the product, strategy and research requirements in the repository.

**Implement sections 4–9 in this pass. Stop after the R3 acceptance gate.** Section 10 describes subsequent M2 work and its implementation approach; it is not authorization to build all of M2 in the same pass. Complete one focused ticket at a time, continuing through the authorized sequence without requesting approval for ordinary implementation choices.

Read `AGENTS.md`, `docs/decisions.md`, `docs/contracts.md`, `docs/data_rules.md`, `docs/requirements.md`, `docs/experiments.md`, `docs/tickets.md`, and `docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md` before dependent changes. Recheck the current branch and code: if a finding is already fixed, verify the acceptance test instead of undoing newer work.

Preserve these boundaries:

- Offline synthetic development; no broker access, market-data acquisition, purchases or trading in this pass.
- No invented market account/risk/cost values, historical membership, date splits, statistical methods or gate thresholds. D09–D13 and D16–D17 remain explicit dependencies. Separate the requested forming-bar capability from unresolved D22 provider details.
- Keep the declared 2024 calendar unless a separate calendar-extension ticket is authorized.
- Decimal money/price/size; integer nanosecond timestamps; deterministic sorting; explicit quality and unavailable states.
- Preserve failed attempts and existing evidence. No database reset or history rewriting to make tests pass.
- Preserve `orb_close_cross_v1` and the documented VWAP baseline definitions. They remain closed-bar strategies. **The replay architecture must also support intrabar decisions.** Actual intrabar strategy variants require distinct names, rules and evaluation cadence; do not silently convert either baseline.
- Do not implement a general optimizer, detailed exchange simulator, distributed infrastructure or optional model integration.

## 2. Reviewed state and evidence limits

The independent review reproduced 251 passing tests, successful validation of `configs/fixture.yaml`, and exit 2 with 44 blockers for `configs/baseline_pending.yaml`. Tests ran under the available Python 3.12 environment with pytest 9.2.0 and Pydantic 2.14.1. A fresh locked `uv` installation and Ruff were not independently reproduced. Run those in the implementation environment.

Existing code includes configuration, run records, data/calendar validation, typed event envelopes, deterministic ordering, basic views and a synthetic trade bar builder. `ReplayView.forming_bar()` is still unimplemented. Ledger, fills, scanner, actual strategies, P&L and end-to-end research commands are later work.

The 251 tests are a baseline, not a target count. Existing tests miss the counterexamples below. Do not delete them or weaken their assertions merely to accommodate the fixes.

## 3. Corrections inventory

“Reproduced” means a separate executable probe demonstrated the result on the reviewed snapshot. “Inspection” means the implementation visibly lacks the contract; verify it with a focused test before changing it.

| ID | Evidence | Finding | Main location |
|---|---|---|---|
| C01 | Reproduced | Modifying `src/rsys/data/*.py` need not change the source fingerprint because every directory named `data` is excluded. | `experiments.py`: `_is_excluded`, `compute_source_fingerprint` |
| C02 | Reproduced | Two connections can commit `running → success → failed`; transition validation happens outside the transaction. | `storage.py`: `write_record` |
| C03 | Reproduced | An immutable-input violation during context exit is swallowed and leaves the stored run `running`, although the context returns normally. | `experiments.py`: `_finalize`, `run_context` |
| C04 | Reproduced | A pending record reloaded before failure loses its redaction values; a fake credential reaches saved error text. | `experiments.py`, `test_run_record.py` |
| C05 | Reproduced | A completed provider bar can be accepted and visible at its start time; a negative-size trade for BBB can enter an AAA builder. | `data/events.py`, `data/bar_builder.py`, `views.py` |
| C06 | Reproduced | After T1 at second 5 and T2 at second 20, querying a builder snapshot as of second 5 returns T2's price stamped with the earlier time. | `data/bar_builder.py`: `snapshot`, `_snapshot` |
| C07 | Reproduced | Equal-availability trades dispatch in sequence order, but `latest_trade` returns the first. Same-phase derived insertion also contradicts the documented prohibition. | `views.py`, `replay.py` |
| C08 | Reproduced | `nonexistent_method:v999` passes method-reference validation; a naming pattern does not resolve a declared method. | `registry.py`: `_is_method_reference` |
| C09 | Inspection | Trade identity scope, correction idempotency, empty/evicted queries, first late arrivals, quality filtering and coverage-boundary checks need stronger contracts. | builder, events, replay, manifest |
| C10 | Inspection + provider docs | A single OHLC eligibility flag and a conditions-only adapter cannot express all intended provider rules. Tape and richer correction payloads are missing. | `data/bar_rules.py`, `data/events.py` |
| R3 | Explicitly unfinished | Replay settings, publication/view wiring, quote state, parity comparison and integrated causality evidence. | config, replay, views, new integration tests |

## 4. Repair run evidence and configuration

### C01 — Hash the code that actually determines the result

**Implementation**

1. Separate root output-directory exclusions (`/data`, `/runs`, build outputs) from true anywhere exclusions such as `.git`, `.venv`, `__pycache__` and tool caches. Never exclude a source package solely because one path component is `data`.
2. Hash canonical relative paths and bytes for relevant implementation files, versioned rule/specification files, fixture definitions and configuration inputs. Record the sorted manifest and a fingerprint-algorithm version. Keep dependency-lock and dataset identities explicit.
3. Include relevant new/untracked source. Preserve Git revision as supplementary metadata. Exclude runtime credentials and generated output.
4. Do not retroactively recompute old run hashes. Record the algorithm change for new runs; document the limitation of older fingerprints.

**Acceptance**

- Changing `src/rsys/data/bar_builder.py` changes the fingerprint.
- Adding, modifying and deleting a relevant source/rule file changes it.
- Changing root `/data`, `/runs`, caches or credentials does not.
- Identical bytes and paths produce the same fingerprint regardless of traversal order.
- The manifest includes every current `src/rsys/data/*.py` source file selected by the policy.

### C02 — Make validation and state transition one transaction

**Implementation**

Use `BEGIN IMMEDIATE`, then load the current row, validate immutable fields/ownership/lifecycle, update the row, and append the transition before commit. An explicit revision compare-and-swap is also valid if it supplies the same guarantees; do not introduce both mechanisms without need.

Freeze terminal outcomes. A repeated identical terminal write may be an idempotent no-op; a different status, result or error must not overwrite it. If later annotations are needed, use separate append-only records. Keep initial creation and its first transition atomic.

**Acceptance**

- Use two independent SQLite connections and controlled interleaving. Exactly one competing terminal outcome wins; the other gets a named conflict or verified idempotent result.
- History cannot contain `success → failed`, `failed → success` or terminal → running.
- Inject a failure between row update and transition insertion; reopening shows either the whole old state or the whole new state, never a partial commit.
- Exercise a real multiprocess case without timing-sensitive sleeps as the only synchronization.

### C03 — Do not silently lose finalization failures

**Implementation**

Remove the blanket suppression of `FrozenFieldError` and `IllegalTransitionError`. Reload to distinguish a legitimately finalized concurrent outcome from corrupt/mutated input or another persistence error.

Treat immutable run inputs as an immutable snapshot. Give finalization a narrow update surface containing only permitted status/result fields; avoid writing an arbitrarily mutated full record. If invalid mutation is attempted, reject it explicitly. If an error arises inside an active context, retain the failure using the original persisted inputs where possible, then surface the error. If persistence itself fails, report that failure rather than claim success. Preserve the original application exception through chaining or an appropriate exception group.

**Acceptance**

- Mutating a nested resolved setting cannot produce normal exit with a persisted `running` record.
- A genuine immutable-field error remains visible.
- A concurrently committed terminal result is preserved and handled explicitly.
- `KeyboardInterrupt` still records `interrupted` when storage permits and is re-raised.
- Force-kill recovery remains truthful. Keep existing live-owner and dead-child tests; test stale recovery locks and document local-host ownership limitations. Do not infer a remote owner's death from a local PID lookup.

### C04 — Keep credential handling independent of serialized records

**Implementation**

Run records must not be the runtime secret store. Introduce a small in-memory redactor/runtime-secret interface, injectable into execution and finalization even when the record was loaded from disk. Do not persist secret values or reversible substitutes to make redaction work after reload.

Prefer structured persisted failures: error category, safe message, file/function/line locations, and explicitly approved context. Arbitrary exception text and traceback source lines can contain credentials; do not persist them verbatim when a reliable sanitizer is unavailable. Keep any allowed diagnostic text behind the same redactor. Avoid claiming that a field-name regex can sanitize every arbitrary string.

Retain the compatibility redaction of credential-shaped config fields, while rejecting credential-bearing resolved research config at the eventual runner boundary. Cover provider-style names, including `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, their header forms, camelCase and authorization tokens. Preserve legitimate fields such as `ranking_sort_key`.

**Acceptance**

- Create a pending run with a fake secret, reload it, enter its context, and raise an exception containing that secret. Persisted config, error, history and exported diagnostics contain no canary.
- Repeat with an explicitly supplied runtime redactor and with the safe fallback when no redactor is supplied.
- Include fake credentials embedded in a traceback source line and nested exception cause.
- Do not weaken this test to checking only that loaded config is redacted; that misses the original defect.

### C08 — Resolve method references

Implement a minimal registry of declared method specifications. Each resolvable reference must identify its version, specification/content hash, supported purpose and evidence class. Syntax validation is only the first step.

An unknown reference remains a named D17 blocker. Register fixture-only methods solely for synthetic tests; they must not satisfy research-profile gates. Do not invent statistical methods or approve a market gate merely to fill the registry. Test malformed, well-formed-but-unknown, wrong-purpose and declared synthetic references. Update blocker assertions to the exact justified set; do not aim for a fixed count.

## 5. Repair event and bar contracts

### C05 — Validate the objects the replay actually consumes

**Implementation**

- Share normalized validation rules between `NormalizedBar` and `ProviderBarEvent`, or require a validated conversion boundary. A parallel permissive event model must not bypass data-quality checks.
- A completed bar requires `available_at_ns >= bar_end_ns`, valid interval duration/alignment, finite valid prices, OHLC consistency and valid nonnegative counts/volume. Preserve documented partial/no-price representations separately. Do not encode an in-progress provider update as a completed bar with premature availability.
- Stock trades require finite positive price and size; correction replacements require valid replacement values. Cancellations are distinct events, not negative trades. Reject booleans for numeric timestamps, sequences, counts and quantities before coercion. Validate identifiers and revision domains.
- Recorded receipt requires actual receipt evidence and `available_at_ns >= received_at_ns`. Preserve the distinction between source time and arrival time. Clock-skew or out-of-order source-time anomalies must be explicit quality failures or declared adapter behavior, never silently rewritten timestamps.
- Corporate-action availability must respect both `effective_at_ns` and `known_at_ns` under the existing dispatch contract; validate the split ratio and reuse the action validation logic. Arrival later than either bound delays availability further.
- Route by complete series identity: instrument, provider, feed, interval, adjustment/unit contract and rule version as relevant. The builder independently rejects mismatched events before mutation.
- Preserve bad raw observations in the evidence/quarantine stream. Invalid/unavailable data must not be returned as valid strategy observations; suspect data needs an explicit fixture policy and a research blocker until its policy is declared.

**Acceptance**

- A one-minute completed bar available at its start is rejected and never reaches `closed_bars`.
- A BBB trade cannot alter AAA state; neither can a wrong-feed, invalid-quality, negative, zero, NaN or infinite trade.
- A recorded event cannot become visible before receipt.
- An action cannot influence a view before its declared availability.
- Validation failures leave prior builder/view state unchanged and retain a named reason.

### C06 — Store knowledge versions; do not backdate current state

**Implementation**

Introduce an immutable publication record with at least: series/bucket identity, full publication key, lifecycle state, quality/coverage state, revision identity, source event IDs and Decimal aggregates. Keep mutable contributor state internal to the builder.

Every accepted change publishes a new version. An as-of lookup selects the last version whose publication key is at or before the cutoff. It must never recompute current contributors and label the result with the requested historical time.

Preferred boundary: the builder mutates current state and returns new snapshots; a version store owns historical queries. Remove or redirect the misleading builder historical API, and update its callers/tests. If no version exists by the cutoff, return a typed unavailable result. If history was evicted and cannot be reconstructed, raise/return an explicit history-unavailable state. Never manufacture an empty bar as a substitute.

Keep first closing publication and later revisions distinguishable. Declare the view revision policy. A practical R3 fixture policy is latest version known at the decision key for a completed interval, with each decision recording the selected version. Preserve an explicit first-publication policy where an existing baseline requires it. Neither policy rewrites a previously emitted decision.

**Acceptance**

Within one bucket, T1 at second 5 is `100 × 10`; T2 at second 20 is `120 × 10`. After processing both, as-of second 5 returns close 100, volume 10 and numerator/denominator 1000/10. As-of second 20 returns close 120, volume 20 and 2200/20. Repeat after corrections, cancellations, closure and retention eviction.

Previously returned objects and serialized decision traces must remain unchanged. A cutoff before T1 is unavailable. A cutoff at an earlier replay phase cannot see a later-phase publication with the same timestamp.

### C09 — Close the remaining builder edge cases

Add focused tests before changing these inspection findings:

1. **Identity and duplicate delivery:** use the adapter's declared trade identity scope, not a bare ID assumed global. Retain provider/feed/instrument/venue/session scope where required. Verify the full relevant payload, including time and conditions. Identical delivery is idempotent; a changed payload is an explicit conflict. Reusing an identity in another bucket must not overwrite the correction index.
2. **Correction identity:** duplicate correction/cancel delivery must not apply twice or invent a new revision. Preserve original and replacement references. Recompute eligibility when replacement conditions change. Unsupported correction forms are named blockers; do not silently keep stale conditions.
3. **Ordering:** use a declared total key for open/close contributors, including tie metadata. Equal market timestamps must not depend on accidental list/dictionary order. Preserve arrival ordering for availability and provider-specified ordering for aggregation as distinct concepts.
4. **Clock:** retain a monotonic processed watermark. A first trade arriving after its bucket's scheduled end is a late publication/revision of that elapsed interval, never a fresh currently forming bucket. A late event's market time may be old; its availability must not move replay backward.
5. **Empty and missing:** separate verified no-trade coverage, missing delivery, no eligible price and evicted history. Zero observed volume alone proves none of these. Never forward-fill OHLC. A timer can close an observation interval without inventing a priced bar.
6. **Retention:** bound contributor detail and account for identity tombstones/indexes. Unknown or evicted correction dependencies stay explicit. A simple bounded synthetic policy is sufficient; do not claim bounded memory if indexes grow without limit.
7. **Actions:** preserve price-unit provenance. Unsupported mixed-unit intervals and late-discovered essential splits invalidate affected evidence at the appropriate availability; do not revise the past decision transcript.

Keep the recomputation implementation as a correctness reference. Measure before optimizing it; do not replace it with complex incremental extrema maintenance in this pass unless the bounded fixture workload demonstrably requires it.

### C10 — Prepare the rule interface for actual provider semantics

Official documentation checked on 2026-10-29 distinguishes open/close, high/low and volume eligibility, with rules dependent on tape, conditions and bar type. Thus a single OHLC `price` flag is insufficient. The FAQ also documents conditional updates and restrictive combination behavior. [S1]

**Implement the interface and synthetic coverage now:**

- Pass normalized trade context and interval/rule context to classification, not just the conditions tuple.
- Represent open/close, high/low, volume, count and VWAP eligibility separately where needed. Permit conditional policies; an unconditional Boolean alone cannot describe every rule. Do not infer VWAP or count eligibility from volume without a declared rule.
- Preserve tape and the raw condition codes. A space can be a meaningful provider code; do not strip it as if it were a blank identifier.
- Preserve correction identity and condition changes. Alpaca's stream documentation includes original/corrected IDs, prices, sizes and conditions. [S2]
- Expand synthetic rules to cover separate price-field contributions, combined conditions and correction-driven reclassification. Name them as fixtures, never as verified Alpaca behavior.
- Correct the existing docstring claiming there is no authoritative eligibility table. The remaining work is mapping and verifying the chosen feed, payloads, timing, coverage and supported rules.

**Provider audit remains separate:** record source URLs, retrieval date, exact supported feed/tapes/intervals, table/specification hash, timestamp mapping, ordering policy and unsupported cases before enabling a real adapter. Unknown rules block affected evidence. Do not claim provider parity from a fixture adapter. No data purchase or capture is authorized here.

## 6. Repair the replay clock and access boundary

### C07 — One order and one visibility boundary

Use an explicit immutable `ReplayKey` containing availability, phase, declared source ordering and stable tie information. A strategy context is frozen at a processed key; it cannot choose a later cutoff or access the full future input array. Public historical audit queries may select earlier keys from retained versions.

Reuse this ordering for replay, latest-trade/quote selection, publications and decision traces. At equal availability, `latest_*` selects the latest eligible processed event under the declared key, not the first `max` tie. Source sequences must retain their source scope; sequences from independent feeds are not evidence of a shared arrival order. Pin any fallback as a modeled deterministic ordering policy.

**Derived insertion contract for this pass:** queued original events may share a phase. Once a phase has begun dispatch, newly derived events may target only a strictly later `(availability, phase)` pair. Thus a phase-35 handler may enqueue phase 37 at the same timestamp, but not another phase-35 event. Reject any earlier time or phase. This conservative rule matches the current public prohibition and avoids ambiguous same-phase insertion; record it explicitly and test it.

### Scheduled closures and same-time batches

For `trade_replay_v1`, retain the declared phase profile:

| Phase | Data-only R3 behavior |
|---|---|
| 20 | Publish scheduled closures and first provider-bar publications that are actually available. |
| 25 | Apply available corporate-action/unit changes. |
| 30 | Process remaining declared timers/session events. |
| 35 | Apply the complete same-availability batch of trades, corrections and enabled quotes in deterministic order. |
| 37 | Publish derived forming/revised snapshots from that batch. |
| 40 | Freeze the observation context and invoke eligible synthetic observers once for the declared cadence. |
| 80 | Record the deterministic trace and evidence. |

Phases 10, 26, 50, 60 and 70 retain their existing meanings but have no account/execution work in this slice. Preserve the separate `bar_v1` behavior.

Pre-schedule phase-20 closure events before dispatch; do not wait for a phase-30 timer and then insert a closure backward into phase 20. A closure at boundary B uses information processed before B. An old-bucket trade becoming available exactly at B is applied at 35 and produces a later version at 37. A trade whose market time equals B belongs to the next half-open bucket. Phase-40 observers see the completed batch according to their declared revision policy.

Timers establish scheduled boundaries, not proof of feed completeness. Match the required boundary's identity, session/calendar and coverage interval. An unrelated later timer must not satisfy the check. Generate required timers independently of the next tick, while using explicit input coverage/end metadata to distinguish a quiet complete session from a truncated stream. Missing coverage produces an incomplete replay result.

**Acceptance**

- Reverse input file/page order with event identities and source metadata unchanged; trace content remains identical.
- Two same-time sequential trades return the later one; repeat for quotes.
- Earlier-phase contexts cannot see later-phase same-time data.
- Same-time cross-symbol observation waits for the full declared batch without waiting for symbols that have no event.
- Same-phase/backward derived insertion fails; valid 35 → 37 insertion succeeds.
- Quiet intervals close on schedule, first late arrivals are handled, and incomplete EOF/unrelated timers cannot pass completion.

## 7. R3 implementation: settings, publication, views, quotes and parity

### 7.1 Validated settings

Add typed settings corresponding to `docs/decisions.md`. Preserve existing fixture validation; a legacy fixture need not become a replay fixture. The replay command requires the replay-specific fields and rejects unsupported combinations.

| Setting | Required validation |
|---|---|
| `replay.input_mode` | Implemented completed-bar, historical-trade or recorded-event mode; unsupported modes fail explicitly. |
| `replay.ordering_profile` | Compatible `bar_v1` or `trade_replay_v1`, with versioned tie behavior. |
| `replay.availability_model` | Recorded receipt or explicit modeled availability; never label reconstructed timing as captured-live. |
| `bars.authority` | Exactly one authoritative source per series: provider publications or trade builder. |
| `bars.rule_set_id` / hash | Resolvable compatible rules; fixture-only rules cannot certify market data. |
| Interval/session/unit policy | Compatible with the implemented calendar, aggregation and adjustment contract. |
| Revision/retention policy | Explicit version selection and unavailable-history behavior. |
| Observation mode/cadence | Implemented capability and cadence; unsupported per-trade/quote cadence is rejected, not silently downgraded. |
| Quote settings | Enabled flag; explicit freshness/quality policy when enabled. Disabled is a supported complete configuration. |
| Execution data basis | Record as inactive in R3; later distinguish bar and subsequent-trade execution. |

Use declared synthetic values in dedicated fixture configs. Leave unresolved research values as named blockers. Do not preselect a market freshness limit or provider delay.

### 7.2 Minimal integration structure

Reuse existing modules and introduce only narrow components as needed:

- `ReplayRunner`: validates a resolved run, schedules events, advances the clock and dispatches phases.
- A router keyed by full series identity, owning builder/provider/optional quote state.
- An immutable version store or equivalent publication history.
- Context factories that expose only the granted observations at the current processed key.
- A trace writer connected to the corrected run-evidence store.

These are responsibility suggestions, not mandatory class names or a reason to create a framework. Prefer small composable functions/classes. Keep provider normalization outside the strategies and account mutation outside the contexts.

Select one authoritative bar source for each series. When provider bars and raw trades both exist, the non-authoritative source is a comparison stream; never add their volumes or silently switch authority. Record authority and rule version in every relevant publication and run manifest.

### 7.3 Forming-bar decisions

Implement `forming_bar`, `closed_bars`, `latest_trade` and enabled `latest_quote` over published/processed versions. A forming query selects the requested current interval; an old interval revised now remains a revised completed interval, not the current forming bar.

`ClosedOnlyContext` retains its limited capability surface. `IntrabarContext` exposes forming snapshots plus the allowed trade/quote state. Do not grant raw replay arrays or a mutable builder reference.

For the first R3 proof, support a declared **availability-batch cadence** for a synthetic intrabar observer: one evaluation at phase 40 after all same-time inputs and derived publications. It may evaluate many times during a minute. Separately support a synthetic close-only observer at declared completed-bar boundaries. Do not describe batch cadence as per-trade evaluation when several events share a timestamp.

Use a fixture-only threshold observation to prove a decision can occur before minute close and references only the current forming version. This is a software test, not a newly implemented ORB/VWAP strategy or performance claim. Record observation mode, cadence, decision key and input version IDs.

### 7.4 Optional quote state

When enabled, maintain frozen quote versions with bid/ask prices and sizes, source identities, source/arrival times, explicit size units and quality flags. Represent locked, crossed, missing-side and stale states. Compute age against the decision clock and the declared freshness rule.

Quotes update quote state only; they do not contribute to stock trade OHLC, volume or VWAP. A displayed quote size is not a guaranteed fill. The stock stream documents separate bid/ask exchanges and round-lot size fields; normalization must preserve or explicitly convert those units. [S2]

If optional quotes are deferred, reject `quotes.enabled=true` with a named unsupported capability, keep disabled replay fully operational, and mark quote acceptance incomplete. Do not claim the quote subfeature passed because its tests were skipped. Prefer completing the small synthetic quote-state path during R3; no live feed is required.

### 7.5 Offline parity comparison

Implement a comparator, not a provider downloader. Compare only matching instrument/feed/session/interval/unit/rule/revision/as-of semantics. Return a structured mismatch or not-comparable result when those prerequisites differ.

Use exact Decimal comparison for fields whose definitions and precision match. For VWAP, compare the exact accumulator-derived value to the provider representation using a declared precision rule; do not choose a wide tolerance that conceals a defect. Do not compare a historical final bar with an earlier forming snapshot.

Use independently specified synthetic expected bars in local fixtures. A comparator passing synthetic fixtures proves comparator behavior, not Alpaca parity. Stream bars can be updated after the first publication, so first/final/current-as-known versions must remain separate comparison targets. [S2]

### 7.6 Runnable synthetic evidence

Add a small `rsys replay --config PATH --out auto` command for this data-only deliverable, following the existing CLI/error conventions. Do not make `rsys run`, `compare` or `final-evaluate` pretend to be complete. The replay command validates before starting and uses the corrected run lifecycle for an actual attempt.

Create two explicit configs, for example `configs/replay_bar_fixture.yaml` and `configs/replay_trade_fixture.yaml`, plus local input fixtures. Output a run manifest and a JSONL observation/decision trace containing event/publication keys, source IDs, bar identity/status/version, available aggregates, quality/coverage, observer mode and snapshot references. Include quote fields only when enabled. Record incomplete input as an explicit non-successful result.

Separate volatile attempt metadata such as run ID and wall-clock start from deterministic semantic trace content. Repeated runs have fresh IDs but identical semantic trace hashes for identical inputs/config/code. Decimal and timestamp serialization must be canonical.

## 8. Required integration fixtures and tests

Suggested new files: `tests/test_replay_inputs.py`, `tests/test_quote_state.py`, `tests/test_provider_bar_parity.py`, and a focused runner/CLI test file if existing tests are not a natural home. Extend the existing config, storage, event-order, causality and bar-builder suites.

### Worked trade fixture: retain exact arithmetic

Use the existing replay plan's one-minute synthetic example. Times below are exchange-local display labels; store integer UTC nanoseconds using the declared calendar. `ALL` contributes to all fixture fields; `VOLUME_ONLY` contributes only to fixture volume/count.

| Arrival | Observation | O / H / L / C | Volume / count | VWAP numerator / denominator |
|---|---|---|---|---|
| 09:30:05 | T1: 100 × 10, ALL | 100 / 100 / 100 / 100 | 10 / 1 | 1000 / 10 |
| 09:30:20 | T2: 102 × 20, ALL | 100 / 102 / 100 / 102 | 30 / 2 | 3040 / 30 |
| 09:30:30 | T3: 150 × 5, VOLUME_ONLY | 100 / 102 / 100 / 102 | 35 / 3 | 3040 / 30 |
| 09:30:50 | T4: 101 × 10, ALL | 100 / 102 / 100 / 101 | 45 / 4 | 4050 / 40 |
| 09:31:00 | Scheduled close | 100 / 102 / 100 / 101 | 45 / 4 | 4050 / 40 |
| 09:31:05 | T5: market time 09:30:55, 103 × 5 | 100 / 103 / 100 / 103 | 50 / 5 | 4565 / 45 |
| 09:31:10 | Cancel T2 | 100 / 103 / 100 / 103 | 30 / 4 | 2525 / 25 |
| 09:31:15 | Cancel T5 | 100 / 101 / 100 / 101 | 25 / 3 | 2010 / 20 |

Run this through the real runner, version store and contexts, not only direct builder calls. A trade timestamped exactly 09:31:00 belongs to the next bucket. A fixture intrabar observer with a declared close threshold of 101 can first observe a crossing at 09:30:20; the close-only observer receives no forming observation. These threshold/cadence values are synthetic test inputs only.

### Causality and determinism acceptance matrix

| Case | Required assertion |
|---|---|
| Future trades/quotes changed | Trace prefix through cutoff K is unchanged. |
| Later correction targets an earlier trade | Queries and decisions at keys before the correction retain their original values. |
| Later-known corporate action | Earlier output remains immutable; later evidence is updated/invalidated explicitly. |
| Same timestamp, later phase | Earlier-phase context cannot observe it. |
| Shuffled files/pages and cross-symbol order | Identified events yield identical semantic trace hashes. |
| Duplicate trade/cancel/correction | No double counting, extra revision or extra decision caused solely by duplicate delivery. |
| Quiet complete final minute | Scheduled closure occurs without a subsequent trade. |
| Truncated delivery or unrelated later timer | Run is incomplete; fabricated timers do not prove coverage. |
| Provider and builder streams together | Exactly one source contributes to the authoritative series. |
| Wrong symbol/feed/unit/quality | State is unchanged; affected observation is rejected or unavailable. |
| Snapshot queried after later updates/eviction | Correct historical version or explicit unavailable result. |
| Quote update only | Trade-bar aggregates stay unchanged. |
| Unknown rules or unregistered methods | Named unresolved/unsupported result; no default success. |

Run future-perturbation checks against the actual integrated runner and context APIs. Retain a deliberately leaky observer as a negative control that makes the same prefix comparator fail. Testing only a list filter over three completed bars is insufficient for this gate.

## 9. Work order, commands and completion report

Use this order; each row should be a reviewable change with its own targeted tests.

| Ticket | Scope | Completion evidence |
|---|---|---|
| FIX-01 | C01 fingerprint and C08 method resolution | Source mutation and unknown-reference regressions |
| FIX-02 | C02–C04 lifecycle, finalization and credential handling | Transaction/race, interrupted/recovery and reload-canary tests |
| FIX-03 | C05 event validation and routing | Invalid observations rejected before state mutation |
| FIX-04 | C06/C09 snapshot history and builder edge cases | Historical-query, duplicate/revision/retention/late-first tests |
| FIX-05 | C07 ordering, phase boundary and timers | Equal-time, queue, closure and coverage tests |
| R3-A | C10 adapter shape and validated replay settings | Synthetic field rules and config dependency tests |
| R3-B | Runner, versioned views, optional quote state and comparator | Actual integrated fixture and causality tests |
| R3-C | Replay CLI, reproducible trace, documentation | Two repeatable synthetic modes and full checks |

At startup record branch, revision, existing worktree changes and baseline failures. Preserve user edits. If the lock is valid, use `uv sync --frozen`; do not regenerate the lock merely to work around a local install failure. Report dependency/environment problems accurately and continue independent useful work.

Representative targeted commands, after the named files exist:

```bash
uv run pytest tests/test_config.py tests/test_run_record.py
uv run pytest tests/test_market_events.py tests/test_data_contract.py tests/test_universe_manifest.py
uv run pytest tests/test_bar_builder.py tests/test_bar_rules.py tests/test_bar_revisions.py
uv run pytest tests/test_event_order.py tests/test_causality.py
uv run pytest tests/test_replay_inputs.py tests/test_quote_state.py tests/test_provider_bar_parity.py
uv run rsys validate --config configs/fixture.yaml
uv run rsys validate --config configs/baseline_pending.yaml
uv run rsys replay --config configs/replay_bar_fixture.yaml --out auto
uv run rsys replay --config configs/replay_trade_fixture.yaml --out auto
uv run pytest tests/
uv run ruff check .
```

The pending research config is expected to exit 2; verify its blocker identities and reasons. Fixture commands must succeed. Run each replay twice and compare canonical semantic output, excluding declared volatile attempt metadata. Add any new CLI tests to the full suite. Do not claim success for missing targets, skipped required assertions or unavailable lint tooling.

Update `docs/requirements.md` with stable new or extended requirement IDs and exact test mappings. C01–C10 here are handoff references, not a demand to rename existing IDs. Reconcile `docs/tickets.md`, `docs/decisions.md`, `docs/contracts.md`, `docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md` and README statuses. Distinguish implemented, independently tested, synthetic-only, provider-unverified and blocked work. Keep D22's capability request separate from unresolved provider assumptions.

**R3 acceptance gate**

Checked 2026-10-29 against the implementation in this repository (348 tests passing, `uv run ruff check .` clean, no commits made this pass):

- [x] Every reproduced defect has a regression and a correction or an explicit verified already-fixed finding. (C01-C08, FIX-01/02/03/05; `docs/requirements.md` CORR-001..CORR-013.)
- [x] Relevant inspection cases are tested and resolved; any deferred capability is explicitly blocked. (C09/C10, FIX-04/R3-A; a real `providers/alpaca_rules.py` remains explicitly blocked on D12, not fabricated.)
- [x] Both completed-bar and trade-built-bar synthetic replay paths produce inspectable traces. (`rsys replay --config configs/replay_bar_fixture.yaml`, `--config configs/replay_trade_fixture.yaml`.)
- [x] A declared synthetic intrabar observer makes an observation/decision before the bar closes, using the correct forming version. (`tests/test_replay_inputs.py::test_fixture_threshold_observer_first_crosses_at_t2_using_forming_state_only`.)
- [x] Existing close-only semantics remain intact. (`ClosedOnlyContext` tests unchanged and passing.)
- [x] Full integrated future-perturbation, same-time visibility and deterministic replay checks pass. (`tests/test_causality.py`, plus CLI-level `test_reversed_input_order_yields_the_same_semantic_trace_hash` and `test_repeated_replay_runs_produce_identical_semantic_trace_hashes`.)
- [x] Provenance, lifecycle and credential regressions pass. (FIX-02, `tests/test_run_record.py`.)
- [x] Optional quote scope and provider-parity limitations are reported honestly. (`src/rsys/data/quotes.py`/`src/rsys/parity.py` docstrings and `docs/decisions.md`'s D22 note state these are fixture-only, never verified Alpaca behavior.)
- [x] Full suite and lint results are reported, with exact commands and any environmental limitations. (`uv run pytest tests/ -q` -> 348 passed; `uv run ruff check .` -> clean; no environmental limitations encountered on this Windows/`uv`/Python 3.12 checkout.)
- [x] Requirements/status docs describe the delivered behavior rather than planned interfaces. (`docs/requirements.md`, `docs/tickets.md`, `docs/decisions.md`, `docs/contracts.md`, `docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md`, `README.md` updated in this pass.)

Final coding-agent report: changed files grouped by ticket; each reproduced defect and its regression; commands/results; trace artifact paths and semantic hashes; compatibility/migration notes; unresolved market/provider decisions; and the next M2 dependency. Do not report that strategies, execution, P&L or real-provider parity exist unless they actually do.

## 10. Next implementation after R3 is accepted

### M2-01 — Ledger and conservative bar execution

Implement the account ledger, reservation/sizing logic, positions and conservative bar execution against the existing F01–F17 expected values. Use explicit instrument ticks/quantity steps and Decimal arithmetic. Keep proposals, acceptance and fills as separate operations; a proposal cannot mutate the ledger.

Recheck available cash and risk at acceptance/fill, release reservations consistently, enforce one active position per instrument in v1, and preserve position management after a symbol leaves the scanner. Implement existing stop/target/ambiguity/flatten contracts; do not invent new market defaults. Preserve the declared split/unit behavior and rejected-entry-at-invalid-open case.

Deliver `tests/test_fixture_cases.py` and a functioning `rsys fixture --case all --out auto` that executes real account code and matches every checkpoint. Stop on failed accounting invariants before adding strategies.

### M2-01T — Minimal trade-event execution adapter

Reuse the ledger and order lifecycle. An order produced by event E or batch B cannot fill on that same event or any observation already used in B's decision snapshot. Require a subsequent eligible event after the decision key and after declared latency. A same-time later phase alone does not make the triggering trade a new fill opportunity.

Handle corrections, ineligible/late old prints, expiry, exits before entries, cash/risk rechecks and source/unit compatibility explicitly. Late historical corrections are not fresh executable liquidity. Test these with tiny synthetic streams. Keep results labeled as the declared execution approximation; do not imply order-book realism or guaranteed quote liquidity.

### M2-02 / M2-03 — Features, scanner and strategies

Implement causal shared features and scanner ranking from bounded views, then ORB and VWAP independently. Use existing H01–H03, DQ08, ORB-01…07, VWAP-01…08 and strategy fixtures. Retain stable ties and ranking evidence. Scanner membership does not control position management.

Keep the current closed-bar baselines. For any later intrabar variant, first specify a distinct strategy ID, observation mode, cadence, signal/re-entry/cooldown rules and feature definitions. Evaluate it as a separate experiment using the appropriate execution basis. Forming-bar volume and VWAP are current knowledge, never the eventual completed values.

### M2-04 — First complete synthetic research path

Wire configuration → universe → replay → features/positions → scanner → strategy proposals → acceptance → execution → reporting. Implement `run` and `compare` with traceable decisions, trades and net-of-cost results. Reject incompatible comparisons. Protect the final holdout through the dedicated final-evaluation boundary and log accesses before market evaluation.

Demonstrate one reproducible synthetic end-to-end run and a fair comparison. Separate software fixture outputs from historical or paper evidence. Real market experiments wait for the relevant D09–D13/D16–D17 choices and provider audit. M3 comparisons precede optimization.

## 11. Provider references and interpretation limits

These sources support the limited provider facts cited above; they do not establish the user's entitlement, historical coverage or exact reconstruction of live arrivals. Recheck them before implementing a market adapter.

- **[S1] Alpaca Market Data FAQ — stock bar aggregation**, accessed 2026-10-29: https://docs.alpaca.markets/us/docs/market-data-faq#how-are-bars-aggregated
- **[S2] Alpaca Real-time Stock Data — trades, quotes, bars, updates and corrections**, accessed 2026-10-29: https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data

The interface and test prescriptions in this document are engineering recommendations derived from the project's causality requirements and the observed code defects. They are not assertions of a verified real-data adapter.

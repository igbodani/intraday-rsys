# Public dated/as-of history scanner

This feature connects declared synthetic minute history to `rsys run` for both bar and trade input, including multiple sessions. Rankings are calculated at the replay decision boundary. Supplied synthetic scores remain supported. This is software evidence, not an Alpaca loader or a market-performance result.

## Try the complete examples

The generator writes a complete version-2 config, including both sessions, both symbols, explicit risk/cost settings and every declared historical minute. It makes no network calls.

```bash
uv run python scripts/make_history_scanner_fixture.py --mode bar --out /tmp/history-bar.json
uv run rsys run --config /tmp/history-bar.json --out runs/history-bar --report
uv run python scripts/make_history_scanner_fixture.py --mode trade --out /tmp/history-trade.json
uv run rsys run --config /tmp/history-trade.json --out runs/history-trade --report
```

The examples use 2024-11-29 and 2024-12-02, reset daily strategy state and carry account equity. At the first decision each stock has 100 shares of current volume. AAA's comparable prior minute has 50 shares, giving RVOL 2; BBB's has 200, giving RVOL 0.5. With top-N set to 1, AAA gets the entry opportunity. Each example completes two AAA trades; the modeled final balances are 1022.34800 for bars and 1034.14420 for trades, from 1000. These synthetic prices, histories and freshness/liquidity thresholds are test values, not approved research settings.

## Public configuration

Without `scanner`, or with `scanner: {"mode": "supplied"}`, each session still supplies `scanner_inputs`. In history mode, omit every session's `scanner_inputs`; mixing the modes is an error.

The following top-level `scanner` fields are all required for history mode:

| Field | Meaning |
|---|---|
| `mode: minute_history` | Select the new synthetic scanner |
| `feed: fixture` | Must match the public synthetic replay feed; SIP/IEX/provider labels are rejected |
| `unit_basis: raw` | Current and prior observations share raw units |
| `corporate_actions: none` | Explicit declaration that this fixture has no action boundaries; split conversion is not added |
| `lookback_sessions` | Positive integer number of comparable prior sessions; no research default |
| `max_lookback_calendar_sessions` | Positive calendar search budget, at least the lookback |
| `min_dollar_volume` | Explicit positive Decimal threshold for prior full-session volume times prior close |
| `max_current_bar_age_ms` | Explicit nonnegative integer maximum age from a completed bar's **market end** to decision availability; equality is allowed |
| `history` | Object mapping symbols to lists of declared minute observations; an empty/missing symbol history is unavailable |

Each observation requires `session_date`, zero-based `minute_index`, timezone-aware `available_at`, nonnegative integer `revision`, nonblank `source`, `status`, `close` and `volume`. `status` is `TRADED` (positive close, nonnegative volume) or `NO_TRADE` (null close, zero volume). Dates/minutes must fall within the declared 2024 calendar and availability cannot precede minute completion. Monetary/volume values accept the existing Decimal input convention; the examples use strings. Availability uses millisecond precision and is normalized to UTC. Existing current-session market timestamps remain dated exchange-local timestamps without offsets.

The complete public input is structurally and numerically validated before execution, including future rows. A malformed future row therefore fails the attempt. For a valid input, historical observations contribute only once their own declared availability is reached. The lower-level provider separately validates economic payloads after visibility/revision selection; its tolerance of an unavailable malformed payload is not a promise of the public JSON schema.

## Decision behavior

1. The public batch parses history into `data.models.MinuteObservation` records and constructs one scanner per session. The histories remain explicitly supplied input; a short replay session is never promoted into verified full-day historical coverage.
2. Both runners invoke `strategies.minute_scanner.MinuteHistoryScanner` with their immutable `ClosedOnlyContext` at phase 40. No forming bar, raw future market array, broker API or account mutation is exposed to this scanner.
3. For each session-universe symbol, use the latest visible completed one-minute bar. Retain its market end, publication time and age. Reject absent bars, bars older than the explicit age limit, missing opening/intermediate minutes, incompatible feed/units or unusable prices/volumes. A late correction cannot make an old market interval fresh by changing its publication time.
4. Sum the latest selected version of each completed interval once. Elapsed minutes come from the interval end relative to the calendar open, not from delivery time. Create a fresh `MinuteHistoryProvider` for this elapsed minute and decision cutoff. Its highest visible historical revisions supply comparable prior cumulative volumes, previous full-session close and prior full-session volume. Historical `available_at` already declares knowability; the current-input feed-delay setting is not added to it again.
5. Reuse the existing RVOL/gain/dollar-volume math and RVOL → gain → symbol ranking. Keep missing-selected-history and zero-denominator statuses distinct. Calendar selection precedes coverage checks; missing selected data is not replaced with older complete data. Early-close sessions too short for the requested minute are skipped for RVOL, but the immediately prior full session still supplies eligibility and previous close.
6. Strategies continue receiving their existing new-completed-publication callbacks. Ranking an older but explicitly age-eligible observation does not introduce new strategy callbacks or execution opportunities. The existing acceptance/risk/fill clocks and scanner-independent protective exits are unchanged.

For example, a bar ending at 09:31 and published at 09:32:01 still requests one elapsed minute of history. Its age is 61 seconds, so it is eligible only if the fixture explicitly allows that age. Trade-built bars use their actual bounded versions at scheduled replay decisions; delayed delivery can change which intervals are available and does not manufacture a completed bar at a different clock time.

## Objects and evidence

| File/object | Role |
|---|---|
| `application/history_config.py` | Strict input validation, parsing and per-session scanner construction |
| `strategies/minute_scanner.py` | Bounded current-volume construction, fresh historical cutoff, feature composition, ranking and evidence |
| `strategies/models.py: HistoryScan` | Plain result record containing `ScanResult` and its explanation |
| `execution/models.py: DecisionRecord.scanner_evidence` | Additive per-decision explanation; empty for supplied mode |
| `application/batch.py`, `application/sessions.py` | Connect the service to both public runners and preserve multi-session/partial evidence |
| `research/comparison.py`, `reporting/html.py` | Require sealed history artifacts for history mode and display escaped per-decision explanations |

Decisions retain all eligible ranks, selected symbols, exclusions, feature statuses/values, selected calendar dates, current input-version references and the existing canonical `ranking-json-v1` payload/digest. Each candidate has `SELECTED`, `OUTSIDE_TOP_N` or an exclusion reason; underlying current-coverage/history statuses explain unavailable scores.

`scanner_history.json` stores content-addressed snapshots of exact selected historical observations, including source, feed, availability, revision and units. Decision records refer to snapshot IDs, avoiding a full previous-day minute dump at every decision. This registry, complete input, resolved configuration, trace, session summaries, fills, metrics and optional HTML report are hashed and sealed in the version-3 manifest. Comparison requires the registry, verifies every retained artifact and includes the scanner policy/history in `scanner_hash`. The basis is `synthetic_minute_history_v1`; it is not interchangeable with supplied scores.

Runtime failures preserve earlier successful sessions, current partial fills/decisions/snapshots and later not-started sessions. Config-construction failures retain their input/error with `scanner_basis: unresolved_invalid_config`, not a misleading successful scanner-mode label. Failed attempts cannot be compared as successes. The HTML report has expandable scanner explanations and identifies the registry containing exact revisions.

## Validation and review

| Tests | Independent assertions |
|---|---|
| `tests/test_public_history_scanner.py` | Both modes through subprocess CLI; exact RVOL/gain/previous volume and account balances; two-day state; reconstructed ranking/snapshot hashes; changing eligible prior volume changes the purchased stock; mutations of future history/current/session input leave the complete earlier trace unchanged; delayed history/current data; current corrections replace volume once; missing vs verified zero; stale/gapped symbols excluded; continuing exits after rank removal; unsupported configs; retained runtime/config failures; report escaping; sealed artifact and comparison checks |
| `tests/test_minute_scanner.py` | Real bounded replay contexts at zero minutes, early-close and calendar boundaries; selected missing history cannot be backfilled |
| Existing `test_minute_history*.py`, `test_public_sessions.py`, `test_package_structure.py` | Provider cutoffs/revisions, previous public risk/cost/CLI behavior and dependency/model boundaries |

```bash
uv run pytest tests/test_public_history_scanner.py tests/test_minute_scanner.py -q
uv run pytest tests/ -q
uv run ruff check .
```

Verified on the feature diff: `uv run pytest tests/ -q` **851 passed**; `uv run ruff check .` **clean**. Tests used the frozen dependency environment with `PYTHONPATH=src`; the public examples were invoked in subprocesses. No provider/network check was needed or run.

Independent review reproduced and fixed unlimited stale ranking and mislabeled failed configurations. The revised stale-symbol fixtures verify that BBB can buy and exit after AAA stops updating, in both execution modes. The new tests use complete traces with real fills plus a past-change negative control, not an invariant check against an inactive strategy.

Publication scope: build on history-branch commit `4695e96` and push to `feat/dated-asof-scanner-history`. No merge into develop is included in this task.

## Remaining dependency

Implement audited Alpaca historical ingestion with source/feed/corporate-action and coverage policies, partition checks before every loader read, and declared historical universe membership. D12/D22 remain Open, D13/D14/D20/D21 remain Proposed, and the approved dollar-volume threshold is still missing. This change chooses no production freshness threshold, research trial budget, final split or promotion rule. The 2024 fixture calendar, unsupported equal-market-time trade inputs and existing account limitations remain. `final_evaluate()` stays blocked.

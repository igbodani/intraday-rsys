# Alpaca acquisition and local backtests

Implemented scope: one explicitly chosen stock per request, SIP or IEX one-minute raw bars, the declared NYSE **2024 calendar**, versioned local JSON storage, and offline ORB/VWAP bar execution. This adds no broker orders, live feed, trade-level Alpaca replay, optimizer or final evaluation. Credentials are needed only for acquisition. No credential-backed request has been tested in this implementation.

## Workflow

1. Copy `configs/alpaca_download.example.json` and `configs/alpaca_backtest.example.json`. They deliberately contain blocked settings rather than silently approve feed, date partitions, liquidity/freshness thresholds or action coverage.
2. Choose your entitled `sip` or `iex` feed. Keep it identical for current and historical bars. The example `asof: "-"` explicitly disables symbol remapping; a canonical date opts into Alpaca entity mapping. This is not an arrival-time or corporate-action cutoff.
3. Declare non-overlapping date partitions using `{"name":"development"|"validation"|"final", "start":"YYYY-MM-DD", "end":"YYYY-MM-DD"}`. No split percentages or dates are automatically assigned. Development can read only development dates; validation can read development and validation dates; final and undeclared dates are always denied. Both acquisition and replay preflight all requested trading dates before any data/cache read. These are user-declared policy boundaries, not protection against an operator deliberately rewriting the policy.
4. Set `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` in your local environment. Do not place them in JSON, Git, command-line arguments or messages. The HTTP endpoint is fixed to Alpaca data; redirects are rejected and transport failures are sanitized.
5. Download the execution dates **and all scanner lookback dates**. The current loader conservatively requires the entire declared calendar search window, not just whichever dates happen to have data. The download manifest lists each date, immutable object ID and coverage gaps. A completed download can still have missing coverage; this is not silently called complete market data.
6. Copy those date/object-ID pairs into backtest `objects`. Pin exactly the required execution/warmup dates. Choose explicit scanner settings, account/execution policies and a flatten time compatible with every selected session. The example 12:55 works within regular and early-close sessions but is an editable example, not a researched exit policy.
7. Record an action review covering every execution and warmup date: `status: verified_none`, a nonempty `source`, `start` and `end`. This records your attestation; the app does not independently audit it. If splits/dividends/other actions occur, use another reviewed window or wait for action handling. The pending template cannot run.
8. Run the backtest. This command never creates an HTTP client or reads credentials. It validates pinned cache content and reuses the existing scanner, strategies, risk, execution and report pipeline.

```bash
uv run rsys download --config configs/my_download.json --out runs/acquisition
# Repeating skips already cached requests; refresh preserves the previous objects:
uv run rsys download --config configs/my_download.json --out runs/acquisition --refresh
uv run rsys backtest --config configs/my_backtest.json --out runs/historical --report
uv run rsys compare --storage-root runs/historical --runs ID_A ID_B --out runs/comparison
```

Cache paths resolve relative to the JSON config. `/data/`, `/runs/` and `.env` are ignored by Git. Keep dataset snapshots outside the source repository and back them up. JSON is the initial transparent storage format; a columnar adapter can be added behind the cache contract later.

## Data and execution contracts

- Downloads paginate until `next_page_token` is absent, including pages with no requested-symbol rows. A repeated token fails. Explicit bounded retries apply to 429, selected server errors and transport outages; 401/403 fail immediately. No alternate feed is silently substituted.
- Every request pins symbol, date, feed, raw adjustment, mapping policy, interval and ordering. Objects are stored under a request-specific directory and addressed by a SHA-256 of their exact canonical bytes. The current-request index changes on refresh; previously pinned objects remain. Files publish by atomic rename, and failed pagination does not publish a partial day.
- Stored objects contain source pages, retrieval time, normalized bars, missing-minute inventory and calendar/timing versions. Reads validate hashes, schema/provider/timing identity and recomputed OHLC/coverage. Symlink aliases and cross-request lookups are refused. This establishes integrity relative to collected input, not cryptographic proof of the vendor's response.
- Negative/nonfinite values, bad OHLC, non-minute/naive timestamps, wrong symbols, out-of-request bars and conflicting duplicate bars fail. Exact duplicate bars count once and remain disclosed. Missing minutes remain missing; a missing Alpaca bar is never synthesized as a verified no-trade minute.
- Execution dates currently require every scheduled regular-session minute. Sparse IEX data may fail this strict requirement. Warmup gaps make the scanner's history unavailable and can yield a valid zero-trade result; calendar selection never fills a missing selected day with an older available one.
- Historical results are labeled `historical_bar_estimate`, `historical_final_bar_features_v1`, and unscored. Alpaca final historical bars do **not** reconstruct original publication/revision history. Current bars use modeled availability at interval end plus the explicit feed delay. Previous-day bars use the same timing model. Internal replay remains bounded, but future vendor corrections may already be embedded in a downloaded historical snapshot; this limitation cannot be eliminated by the simulator.
- Execution remains modeled bar-open/OHLC execution with explicit costs and the synthetic immediate-settlement account. It is not a real fill record or precise intrabar touch simulation. Raw-unit runs require the declared action-free window; no split-adjusted data is silently mixed into execution.

## Retained evidence

Each download invocation retains `downloads/<id>/manifest.json`: redacted request, allowed/denied date log, successful session objects, coverage and any failure. Retrying after a failed later session reuses the earlier completed downloads. An interrupted download retains its status and does not overwrite pinned objects.

Backtests use the existing SQLite run lifecycle and sealed version-3 artifacts: complete expanded bars/history, original backtest request, pinned dataset hashes, source/feed/timing and action review, `data_access.json`, scanner revisions, decisions, fills, metrics, session files and optional HTML report. Configuration/loading errors, including unreadable or malformed JSON, retain failed attempts. Unparseable raw bytes are not copied into reports; their hash is retained when readable. Comparison includes historical source/policy identity and refuses altered evidence. `final-evaluate` remains blocked.

## Implementation map and checks

| File | Responsibility |
|---|---|
| `data/alpaca.py` | Fixed-endpoint environment-authenticated HTTP transport, pagination and bounded retries |
| `data/bar_cache.py` | Request-scoped immutable snapshots, validation, deduplication, coverage and integrity |
| `research/models.py: DatePartition` | Plain date-partition record |
| `application/dataset_policy.py` | Purpose/date authorization and logs before source reads |
| `application/historical.py` | Download lifecycle, guarded pinned-data loading and historical run composition |
| Existing batch/bar/scanner/report/comparison modules | Explicit historical source identities and labels while preserving synthetic paths |
| `tests/test_alpaca_pipeline.py` | Provider fakes and public offline CLI tests |

Tests verify hand-calculated two-session balances, ranks/fills and source references; cache reuse without network access; refresh/old snapshot preservation; pagination failures and resume; missing versus complete coverage; duplicate/conflicting bars; invalid payloads; bounded HTTP retries and secret sanitization; denied warmup reads; cache tampering/aliasing/version rejection; action-review gates; failed public input retention; and repeated sealed comparisons. Existing replay/future-invariance tests continue to apply to the shared engine. Synthetic provider responses prove software behavior, not actual entitlement or market-data fidelity.

```bash
uv run pytest tests/test_alpaca_pipeline.py -q
uv run pytest tests/ -q
uv run ruff check .
```

Verified on the final implementation diff: **881 tests passed**, including 30 new acquisition/cache/offline-backtest cases; **Ruff clean**. Independent review reran 12 failure/security-boundary regressions successfully. The pinned-future-data test compares the entire earlier trace and proves a past zero-volume change removes the earlier fill.

Independent review found credential-error leakage and pre-lifecycle input failures; both received fixes and regression tests. Cache schema/provider/timing validation was also added following review.

## Provider reference and remaining checks

API contract checked against [Alpaca historical stock bars](https://docs.alpaca.markets/us/reference/stockbars) on 2026-10-30: endpoint, pagination, explicit feed/raw adjustment, inclusive time range, mapping `asof`, and authentication/error statuses. No downloaded sample or credentials were used.

Still needed before claiming a validated market backtest: test your credentials/feed entitlement, inspect a small real response and missing-minute patterns, approve partitions and scanner settings, verify corporate-action coverage, and manually reconcile selected real-data trades against source bars. Beyond this first bounded path: calendar years beyond 2024, audited universe/action adapters, recorded arrival/correction histories, sparse-data coverage evidence, columnar/performance work and the frozen promotion protocol. D12/D22 remain open and no D13/D14/D16/D17/D20 research choice is silently resolved.

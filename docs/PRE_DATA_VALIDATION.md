# Pre-data system audit and repairs

Scope: latest `develop` at `f0eece4744442b088fa7442112118bacb3d69d62`, plus the containing repair commit. That merge has the same source tree as the prior feature branch `a48f6f2`. No user edits existed at the start. All data in this audit are synthetic fixtures or fake provider responses; no Alpaca download or credential-backed request was made.

## Defects repaired

| Defect | Resulting behavior | Regression evidence |
|---|---|---|
| Historical comparisons count a strategy/account/scanner change twice through the saved original request | Supported settings are compared once under their declared axis. Full requests remain retained; source identity, partitions, action review and unknown metadata still gate comparison. | `tests/test_historical_comparison.py`: actual retained backtests and public `compare`; undeclared and simultaneous changes rejected; source/policy/unknown-field negative controls |
| Feed delay changes derived history and falsely appears to change scanner controls | New historical manifests separately fingerprint scanner settings; retain the full history fingerprint. Legacy evidence stays conservative; unknown versions fail. | Actual 250 ms versus 500 ms runs compare under `cost_profile`; adding a scanner change still fails |
| Equal numeric duplicates conflict when JSON spellings differ | Equal OHLC/volume values count once regardless of numeric spelling; different values still fail. | `tests/test_alpaca_pipeline.py`: decimal/scientific spellings, missing-minute count, cache round-trip and conflicting-value control |
| Datetime parsing truncates sub-microsecond time or checks alignment before UTC conversion | Parse exact UTC nanoseconds and require minute alignment before converting to display timestamps. | Offset-seconds, nonzero nanoseconds and excessive precision fail; exact UTC encodings pass |
| A volume-only trade can create a false account mark, fill and risk halt | Marks follow declared close eligibility. Fresh fills require ordinary fixture prints; volume-only/late prints cannot fill entries, stops, targets or timed exits. | `tests/test_trade_condition_execution.py`: direct adapter gates, ordinary-print controls, two-session account/risk invariance, public retained inputs and unknown-code failure |

One concrete account reproduction changed a volume-only print to `10000`. Previously this fabricated a peak near `90080`, triggered an incorrect target exit and suppressed later trading. The repaired two-session fixture finishes at exactly `1034.14420`, with both trades completed, no remaining position or reserved cash, and identical risk history to a neutral volume-only price. An ordinary eligible print at that price still changes the result, proving the exclusion test is meaningful.

The fixed condition policy is deliberately fixture-only. A `LATE_REPORT` print retains its declared close-mark eligibility, while providing no fresh fill. Nothing here supplies Alpaca trade-condition rules or changes the historical bar-fill model.

## Verification

The unchanged latest-develop baseline passed **881 tests**. New regression cases were executed before repairs and reproduced the comparison, data-normalization and condition defects. An additional feed-delay regression reproduced the second-axis comparison failure before its repair.

Validation commands use the frozen dependency lock and local data:

```bash
uv sync --frozen
uv run --frozen pytest tests/ -q
uv run --frozen ruff check .
```

For this audit, dependencies were restored entirely from an offline cache into an isolated environment. Focused checks cover the public offline backtest/compare/run commands as well as cache, adapter and risk boundaries. The full suite also covers F01–F17, deterministic replay, bounded revisions and future-prefix invariance with negative controls, reservation/cash conservation, failed-attempt retention, artifact tampering, HTML escaping, holdout reads denied before cache/network access, package boundaries, and the deliberately blocked final evaluation.

Final complete-diff result: **937 passed in 120.65 seconds; Ruff clean; `git diff --check` clean**. This adds 56 regression cases to the 881-test baseline: 6 acquisition/normalization, 25 historical comparison and 25 trade-condition cases. No tests were skipped or weakened to accommodate the repairs. The earlier complete run also passed all 937 cases; the final rerun followed import-order cleanup.

Independent review reported no blocking findings in the repaired scope. One reviewer reran 35 comparison/cache cases; the other reran 87 execution/public-session cases and separately exercised 160 known-condition combinations plus 9 unknown-code combinations. Both used local inputs and made no live API requests. No financial/research decision status changed.

Changes are prepared on `fix/pre-alpaca-validation`, based on the latest `develop`, for review against that branch. No merge is part of this repair task.

## Remaining boundaries before strategy research

- Validate credentials and chosen feed with a bounded acquisition sample; then inspect timestamps, coverage and a few source bars. These tests cannot verify the account's entitlement or an actual vendor response.
- Explicitly choose development/validation/final dates, scanner settings and an action-free window. The loader enforces declared boundaries but cannot verify the operator's corporate-action attestation. D12/D22 and the remaining research decisions are not approved by these repairs.
- Current historical scope remains one stock per request, raw regular-session minute bars and the declared 2024 calendar. Sparse execution-day coverage fails rather than silently becoming zero volume. Historical final bars use modeled availability and may already include vendor revisions.
- Start with small samples and measure resources. Independent review measured about 7.45 MB of scanner evidence for one 210-minute fixture with one lookback session. Prefix snapshots repeat minute evidence; large date ranges/lookbacks are not yet a validated performance workload.
- Rerun both comparison arms with the same repaired code, pinned data and declared policies. Profitability, uncertainty, trial-budget enforcement, entry/exit attribution studies and promotion to paper trading are not established by passing software tests. Final evaluation remains blocked.

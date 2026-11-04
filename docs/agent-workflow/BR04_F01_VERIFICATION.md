# BR-04-F01: lossless history evidence compaction

Based on upstream `6d692a2d52845ca92df76c2f3fbdae662237434f` (BR-04), with a clean
starting tree. Implements the [bounded follow-up](evidence-compaction-followup.md).
The [format guide](../HISTORY_EVIDENCE.md) documents archive version 2, legacy reads,
logical snapshot identity and the unchanged trading/evidence contracts.

## Implementation and acceptance

- `data/history_evidence.py`: shared observations and persistent sequence prefixes,
  exact original logical hashes, isolated snapshot reads and structural/hash checks.
- `strategies/minute_scanner.py`: intern retained evidence after selection, without
  changing the feature provider, cutoff, ranking, entries, exits or account policy.
- `application/batch.py`: merge compact tables, mark the archive version and stream
  file hashing. Every input, selected revision, decision and failed attempt remains.
- `research/trace.py`, `comparison.py`: shared verification of compact/legacy
  artifacts and all decision references; invalid metadata produces domain errors.
  Existing report/status readers use that same verification boundary.
- `research/diagnostics.py`: chunked atomic JSON writes, preserving prior scalar/key
  conversion and error behavior without whole-tree serialization/reparse copies.

`tests/test_history_evidence.py` proves exact reconstruction and IDs, linear prefix
sharing, observed-zero/missing/revision distinctions, numeric representation
preservation, old-format compatibility, old/new execution equality, corruption and
missing-reference rejection after outer rehashing, malformed metadata, bounded
write sizes and preservation of the old file after a later write failure.
The existing public history test changes only its archive read adapter; its original
hash, selected-revision, fill and equity assertions remain intact.

## Executed checks

Checks ran 2026-11-04 UTC on Linux/Python 3.12 with the locked environment.

- Isolated unchanged BR-04 checkout: **79 focused tests passed**. An earlier baseline
  launched in the editing checkout overlapped changed subprocess imports; it was
  discarded and replaced by this isolated baseline.
- Changed history/scanner/stream integration: **79 passed**.
- New evidence tests plus diagnostic journal/lifecycle after the writer compatibility
  repair: **82 passed**. The preliminary full run found that same private writer-spy
  compatibility issue; it was fixed without weakening its assertion.
- Final full suite: **1,182 passed in 182.45 seconds**, zero failures/skips, using
  `uv run --offline --frozen pytest tests/ -q` with inherited socket/DNS tripwires
  and dummy Alpaca credential variables. No real provider call occurred.
- Ruff, `git diff --check` and Copilot configuration/link checks: **clean**.
- Independent reviewer: **no blocking findings**. Passed 19 new tests and five public
  CLI/causality/failure checks; additionally compared 250 varied logical snapshots
  and 500 serialized objects with the prior representation. The review's malformed
  metadata hardening received four regression cases and a clean re-review.
- Reviewed Python fingerprint:
  `b205a12d1ff3be2eacd26eb7736eb920dfae1b74b9da66efc0fc7425e378c037`.

Exact same-pin comparisons against saved pre-change benchmark runs verified **all
trace bytes, fills, metrics and all 211 / 1,383 reconstructed snapshots** on the
early-close / multi-session workloads. Existing future-prefix tests retain effective
negative controls: unavailable corrections do not change earlier outputs, while a
visible earlier history change changes the selected filled symbol.

## Resource measurements

Fresh-process synthetic measurements on the same Linux x86_64/Python 3.12.14 host
(9 reported logical CPUs, 10,451,427,328 bytes reported physical host RAM):

| Workload | Metric | Before | After |
|---|---|---:|---:|
| 210 execution minutes; 600 input bars | Run output bytes | 23,253,892 | 13,843,472 |
| Same early-close workload | Peak RSS bytes | 155,623,424 | 106,291,200 |
| Same early-close workload | Backtest seconds | 3.82 | 1.97 |
| 780 execution minutes; 1,770 input bars | Run output bytes | 191,415,081 | 84,086,216 |
| Same multi-session workload | Peak RSS bytes | 1,343,410,176 | 150,876,160 |
| Same multi-session workload | Backtest seconds | 22.15 | 14.28 |

The larger workload used approximately **89% less peak memory and 56% fewer output
bytes**. These are measured software workloads, not market performance. Peak RSS
covers process lifetime including setup; host RAM is not a container allowance.
Elapsed times vary with machine load. No data, revisions, history dates or audit
records were dropped to obtain the reduction. Output retains the same scope as the
BR-04 benchmark (run database, reports, manifests, sessions, traces and diagnostics).

The larger workload still writes about 84 MB because complete decision summaries,
trace and HTML details are deliberately preserved. This does not establish capacity
for large universes or long studies. Native Windows and real-provider checks remain
unrun. The next bounded ticket is BR-05's explicitly enabled provider harness;
no financial/research decisions or final-holdout authorization changed here.

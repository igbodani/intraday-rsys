# BR-04-F01 — Reduce repeated scanner evidence before larger experiments

**Status: implemented.** See [verification and measured limits](BR04_F01_VERIFICATION.md). This is separate from provider verification
BR-05 and experiment registry BR-06. No credentials or new research assumptions
are needed to build it. Bound real-data pilots until this resource cost is addressed.

## Problem and evidence

The BR-04 offline benchmark keeps the normal evidence path and all lookback dates.
An initial 210-minute execution with 600 input bars produced approximately 23 MB of
evidence and 156 MB process peak RSS; two 390-minute executions with 1,770 input bars
produced approximately 191 MB and 1.34 GB. These are Linux synthetic measurements,
not market backtests or portable capacity guarantees. The final measured totals
and machine details are in [BR04_VERIFICATION.md](BR04_VERIFICATION.md).

The per-file inventory shows repeated scanner history in `scanner_history.json`,
manifest/session structures and reports. A small cache does not imply a small run.

## Agent implementation plan

1. Profile these exact workloads and identify the allocations and repeated bytes.
   Preserve baseline config/object pins, ordered traces, fills and equity outcomes.
2. Propose a versioned representation for shared immutable scanner observations
   and bounded decision references. Keep raw revisions, availability times, selected
   sessions, exclusions and per-decision auditability. Do not truncate the search
   budget, silently sample events or retain only successful trades.
3. Centralize serialization/hashing and reader resolution. Write each large artifact
   incrementally; avoid collecting it again during manifest/report finalization.
   Define compatibility for old manifests and explicit rejection of unknown versions.
4. Update report/comparison/status readers together. Retain full attempt provenance,
   immutable inputs, primary-failure preservation, atomic outcome commit and seals.
5. Use a reviewer independent of the implementation. Stop if reduced evidence cannot
   reproduce a previously explainable decision; that is a contract change, not an
   acceptable storage optimization.

## Acceptance and testing

- Original hand-calculated P&L/fill oracles and exact semantic traces remain equal.
  Compare old and new representations on both benchmarks and missing/revised history.
- Change a future revision and prove earlier decisions unchanged; change a visible
  observation as a negative control and prove the relevant decision changes.
- Corrupt/remove a referenced evidence chunk, interrupt each write boundary and
  simulate a full disk. Readers reject broken references; failed runs never appear
  successful or comparable. Reports escape untrusted text and do not load all rows
  merely to display a summary.
- Measure before/after elapsed time, output bytes, peak RSS and major allocations
  on the same workload/machine. Demonstrate a material reduction in repeated bytes
  and bounded streaming behavior without promising a machine-independent runtime.
- Run the affected public CLI, existing integrity/causality tests, full offline
  suite, Ruff and independent review. Publish measured evidence, compatibility limits
  and an explicit resource envelope before approving bulk downloads/experiments.

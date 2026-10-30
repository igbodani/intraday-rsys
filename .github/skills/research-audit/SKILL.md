---
name: research-audit
description: Audit intraday-rsys research decisions, historical/provider data plans, cost and risk assumptions, holdout boundaries and experiment claims. Use before market-data integration, strategy comparison or research-policy changes.
---

Read [decisions](../../../docs/decisions.md), [data rules](../../../docs/data_rules.md), [experiment protocol](../../../docs/experiments.md) and [current capability limits](../../../docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md). These sources own research status; do not copy numeric defaults into this skill.

1. Classify the task's evidence: hand-calculated synthetic fixture, verified software output, historical observation or paper result. State which claims the evidence supports and which it cannot establish.
2. List each dependent decision with current status, provenance and unresolved fields. Distinguish provisional calibration from approved policy and implementation convention. Preserve the warning about the imported owner-round date. Do not silently approve Proposed/Open entries.
3. For provider/history work, require source/feed identity, timestamp meaning, available-at/correction history, condition eligibility, units/corporate actions, calendar/coverage, entitlement and historical universe scope. Record official source URLs and retrieval date where used. If tools cannot retrieve a source, mark the assertion unverified.
4. Define a minimal allowed slice with explicit inputs and fail-closed unsupported cases. A fake provider can validate the interface before real feed semantics are resolved; it cannot validate entitlement or market fidelity. Do not label fixture rules as Alpaca rules.
5. Trace every loader read, lookback, warmup and cached alias to its partition permission before I/O. Specify denied-read spies and access logs. Keep `final_evaluate()` blocked until its frozen protocol and authorization are present; no development run may open final outcomes.
6. Check trial accounting includes failures, manual changes and seeds. Change one supported comparison axis at a time with equal account/cost/date/source assumptions. Specify uncertainty, controls and promotion rules before viewing comparative market outcomes; unresolved rules are blockers, not values to optimize.
7. Propose independent checks: tiny hand-calculated RVOL/fees/risk, unavailable/missing/zero history, late/revised observations, forbidden partitions before reader invocation, mismatched comparison assumptions and retained failed attempts. Link to current tests and name new tests as proposed until implemented.

Return a compact table: claim; source/decision/status; supported scope; gap; required test or owner decision. Finish with the next implementable ticket. Never claim profitability, purchase data, trade, open final data or alter research decisions through this workflow.

---
name: adversarial-code-review
description: Independently review an intraday-rsys code diff for causal, account, evidence and regression defects. Use for pre-PR review, reviewing agent-generated code or verifying fixes to reported findings.
---

Read [AGENTS.md](../../../AGENTS.md) and [the review handoff](../../../docs/agent-workflow/handoff.md). Review a named base/head or an explicit working diff; if no target is given, inspect status and use the current diff, stating that scope. Do not guess a remote branch or silently include unrelated work.

1. Inspect the diff and surrounding code before relying on the author's summary. Read the relevant contracts/tests and trace one real public caller through the changed path. Identify the strongest falsifiable correctness claim.
2. Search for a concrete counterexample: future observation leakage, impossible fill, same-time ambiguity, duplicated event/cost, insufficient reservation, partial state after failure, daily-state leakage, dropped failed attempt, tampered artifact, unsafe HTML, incompatible comparison or holdout access before permission checks.
3. Use [replay causality](../replay-causality/SKILL.md) or [research audit](../research-audit/SKILL.md) where relevant. Compare tests against independent expectations. Passing tests and high test counts do not prove the oracle is correct.
4. Run targeted offline tests/reproductions in temporary output locations if execution tools are available. Do not edit tracked files or install dependencies in review mode. If execution is unavailable, label the review static and list unverified checks.
5. Report only actionable findings as defects: severity, file/line or symbol, triggering input, actual/required behavior, impact and reproduction. Keep hypotheses, coverage gaps and research decisions separate. Do not manufacture a finding to satisfy the adversarial role.
6. On a revised patch, reproduce the original issue and inspect the fix for neighboring regressions. Record the exact new revision/diff; the old verdict does not automatically apply.

Return `changes requested` when supported defects block acceptance; otherwise return `no blocking findings in reviewed scope`, with limitations. These are review outcomes, not merge authority or research approval. Hand fixes back to the engineer, then re-review.

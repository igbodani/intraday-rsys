---
name: bounded-implementation
description: Implement one intraday-rsys feature, bug fix or refactor through a bounded diff and independently justified tests. Use when changing Python behavior, public CLI integration, package ownership or run artifacts.
---

Read [AGENTS.md](../../../AGENTS.md), [architecture](../../../docs/ARCHITECTURE.md), the relevant decisions and the task. Run commands from the repository root.

1. Inspect `git status --short`, `git branch --show-current`, `git rev-parse HEAD` and the relevant diff. Preserve pre-existing edits. Locate the public caller, owning service/models and tests; do not rely on historical paths.
2. State the observable acceptance condition and scope. Use [the task brief](../../../docs/agent-workflow/task-brief.md) for nontrivial work. If research semantics are unresolved, identify the exact dependency and continue independent interface/fixture work with explicit synthetic settings.
3. Establish a focused baseline. For a bug, reproduce the defect and add the smallest regression whose expected outcome comes from a contract or independent calculation. For a refactor, retain existing assertions and compare behavior rather than rewriting the oracle.
4. Implement the smallest complete vertical slice. Keep model ownership/import direction, CLI exits and evidence contracts intact. Use shared transitions/builders before duplicating logic.
5. Test meaningful failure paths as well as success. Use [causality checks](../replay-causality/SKILL.md) for time/availability changes and [research audit](../research-audit/SKILL.md) for policy/data/experiment changes.
6. Run focused tests, the affected public command, `uv run pytest tests/ -q` and `uv run ruff check .`. For Copilot-file changes also run `uv run python scripts/check_copilot_config.py`. Report unavailable checks; do not substitute an older test count.
7. Inspect the final diff for unintended scope, weakened assertions, generated artifacts and credentials. Complete [the handoff](../../../docs/agent-workflow/handoff.md). Request independent review for material behavior changes. Re-run the affected check after any fix; prior evidence applies only to its tested revision/diff.

Stop dependent work for an unresolved research choice, missing access or unreproduced assumption; keep unrelated authorized work moving. Follow the user's commit/push/merge scope. Completion means implemented behavior plus evidence, not a plan or a green test that ignores the failure condition.

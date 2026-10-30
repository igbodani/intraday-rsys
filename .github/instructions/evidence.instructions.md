---
applyTo: "src/rsys/application/**/*.py,src/rsys/research/**/*.py,src/rsys/reporting/**/*.py,src/rsys/cli/**/*.py,configs/**/*,docs/decisions.md,docs/experiments.md"
---

Read [public session contracts](../../docs/PUBLIC_SESSION_RUNS.md) and [research audit](../skills/research-audit/SKILL.md). Preserve complete attempted inputs, resolved settings, failures, partial output, source/data/config hashes and sealed artifacts. Label synthetic, historical and paper evidence separately. Compare only supported compatible runs; keep account/cost/capital/source/date assumptions explicit. Test CLI failure exits, artifact tampering and report escaping where affected. A report is not a promotion gate. Guard partition reads before I/O including warmup/caches, and leave final evaluation blocked until the frozen protocol and authorization exist. Never resolve a Proposed/Open research decision by choosing an implementation default.

---
description: Implement one specified backtest-readiness ticket with offline acceptance checks and an evidence handoff.
agent: python-engineer
---

Read [the readiness plan](../../docs/agent-workflow/backtest-readiness-plan.md), [development tickets](../../docs/agent-workflow/backtest-readiness-tickets.md), [testing plan](../../docs/agent-workflow/backtest-readiness-tests.md) and [shared rules](../../AGENTS.md).

Implement the BR-XX ticket supplied in the chat. If no ticket is supplied, start BR-00; verify the actual checkout and baseline rather than assuming the plan's historical test count is current. Check dependencies and use [bounded implementation](../skills/bounded-implementation/SKILL.md), plus [replay causality](../skills/replay-causality/SKILL.md) and [research audit](../skills/research-audit/SKILL.md) where applicable.

Complete one bounded, testable slice at a time. For a multi-ticket request, preserve the order and verify each slice before continuing. Use existing services and explicit settings. Place plain models in their owning models.py, keep operational time outside the deterministic trading trace, preserve immutable run evidence and old readers, and add independent success/failure/negative-control tests through the public path.

Routine engineering choices are yours to make and record. Ask only for missing research inputs/access that genuinely block the selected work; continue other authorized offline work. Do not invent feed/date/strategy/budget/promotion defaults, call Alpaca automatically, access final data, trade or merge. Proposed commands/test names in this pack are not existing capabilities until implemented.

Return [the implementation handoff](../../docs/agent-workflow/handoff.md) with actual checks, evidence paths, supported scope and outstanding gates. Request an independent adversarial review for material behavior changes; do not claim your own checks are an independent review. Keep generated runs and credentials out of Git and follow the user's current branch/publication scope.

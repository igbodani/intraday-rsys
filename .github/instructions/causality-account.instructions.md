---
applyTo: "src/rsys/data/**/*.py,src/rsys/replay/**/*.py,src/rsys/strategies/**/*.py,src/rsys/execution/**/*.py,src/rsys/account/**/*.py"
---

Use [replay causality](../skills/replay-causality/SKILL.md) for changes to observations or execution eligibility. Distinguish market time, known/available time, decision time and fill eligibility. Future changes cannot alter earlier decisions. Timers must fire without a new observation; late prints cannot supply a pre-request fill. Corrections cannot rewrite committed ledger history. Keep full event identity and deterministic phase/rank order; do not relax unsupported equal-time execution into guessed ordering.

Keep reservations intent-owned and rejection atomic. Revalidate fill quantity/cash/risk with the configured costs and current equity. Protective exits continue after scanner removal and risk halts. Daily strategy state resets separately from persistent account/peak state. New account/feed behavior must be supported by the decision register and explicit input policy. No duplicate spread/slippage charge or silent change to recorded risk thresholds.

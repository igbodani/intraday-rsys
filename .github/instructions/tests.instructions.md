---
applyTo: "tests/**/*.py,fixtures/**/*"
---

Tests and fixtures are offline and deterministic. Use hand-calculated independent expectations, boundary cases and negative controls, not a second call to the implementation as the oracle. For causality, compare complete earlier behavior after a future-input mutation and prove an eligible past-input change can affect it. For rejection, assert cash, reservations, positions and trace remain consistent. For integration changes, exercise public subprocess exit codes and retained artifacts. Keep synthetic policy values explicit. Do not loosen assertions, skip a failing regression or bless snapshots simply because output changed. Add tests for meaningful behavior, not prose edits or implementation details.

---
applyTo: "src/rsys/**/*.py,tests/**/*.py,scripts/**/*.py"
---

Follow [architecture and model ownership](../../docs/ARCHITECTURE.md). Put plain domain records in their package's `models.py`; active aggregates, state machines and I/O remain services. Preserve public CLI and serialized contracts unless a deliberate migration is part of the task. Use explicit types and Decimal money. Keep core imports independent of orchestration/reporting. Run Ruff and the relevant tests; package moves also require `tests/test_package_structure.py`. Do not add dependencies or optional ML engines merely to simplify a local refactor.

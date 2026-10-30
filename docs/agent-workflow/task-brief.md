# Bounded task brief

Use this structure in the issue, chat or PR; a new checked-in document is optional.

- **Goal:** observable user behavior or defect, with a concrete input/output example.
- **Checkout:** branch/revision and pre-existing edits to preserve.
- **Scope:** owning packages, likely files/symbols, public contracts, non-goals.
- **Decisions:** relevant IDs/statuses, explicit synthetic assumptions and unresolved dependencies.
- **Design:** data/control path, model ownership, compatibility and failure behavior.
- **Acceptance:** independent expected values/invariants; success, boundary and failure assertions; test paths and public command.
- **Evidence:** artifacts and actual commands/results the engineer must retain.
- **Publication:** user's requested branch/PR/merge scope; do not infer it from an old unrelated task.
- **Next dependency:** what this ticket enables and what it leaves blocked.

Scale to the change. A typo does not need a design exercise; a loader or execution-policy change does.

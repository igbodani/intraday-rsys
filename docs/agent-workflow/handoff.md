# Implementation and review handoff

## Engineer output

1. **Behavior:** problem and resulting behavior, concrete example, affected public interface.
2. **Scope:** exact commit or HEAD plus working diff/files; distinguish pre-existing edits. Include model ownership and compatibility changes where relevant.
3. **Evidence:** command, result/exit, relevant assertions, artifact location and tested revision/diff. Say `not run` with reason for missing checks. Old counts and planned commands are not evidence.
4. **Research:** decision IDs/statuses, assumptions, synthetic versus market evidence, remaining blockers.
5. **Review/publication:** independent review performed or still pending, findings addressed, branch/PR if published under the task's authorization.
6. **Next step:** bounded dependency remaining; do not declare unimplemented historical/paper functionality complete.

## Independent reviewer output

State base/head or working-diff scope and checks run. For each finding give:

- **Severity:** critical (account/evidence compromise), high (incorrect behavior on supported input), medium (bounded correctness/validation defect), low (minor actionable defect).
- **Location and trigger:** file/line or symbol plus minimal input/sequence.
- **Required versus actual behavior:** cite the governing contract and explain impact.
- **Evidence and regression:** reproduction/output or static proof; clearly label unverified hypotheses.

Then give `changes requested` or `no blocking findings in reviewed scope`, plus coverage limits. Do not treat a review verdict as merge permission. If no defects are found, say so without inventing one.

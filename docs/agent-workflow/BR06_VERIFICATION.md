# BR-06: finite experiment runner

Base: upstream `a801063c1a21491ec148166439e39ca1274b9000`, local equivalent
`ed520ea`. Implements [BR-06](backtest-readiness-tickets.md#br-06--finite-experiment-plans-and-honest-comparison-reports)
and T18–T21. See [the operator guide](../EXPERIMENT_RUNNER.md).

## Contract and scope

One explicit version-1 plan runs a baseline plus finite named variations on pinned
historical inputs. No credentials, acquisition, optimization, final holdout opening,
market assumptions or promotion thresholds are introduced. Real data still needs the
BR-05 external/manual checks. D16/D17 and the imported decision-date provenance note
remain unchanged; the fixture budgets and numerical variations are test settings.

- Plain schemas and arm records live in `application/models.py`; trial context lives
  in `research/models.py`. Known path/category validation lives in
  `application/experiment_config.py`, before any cache/runner construction.
- `application/experiment.py` orchestrates the existing historical backtest serially,
  uses the existing file-only lifecycle, saves parent checkpoints and invokes existing
  sealed comparison checks. It does not implement another simulation engine.
- `research/studies.py` transactionally registers the frozen study and reserves slots
  before child preparation. Failed preparation, execution and interruptions count;
  the coordinator/status checks do not. Reruns retain all prior trial rows. External
  references are explicit and count; different IDs/roots are not global enforcement.
- Optional context is added at child record creation through historical/batch services.
  Parent/plan/trial links and expanded settings are frozen with the existing config;
  source/dependency checks protect same-revision comparisons. Ordinary runs retain
  their existing metadata and avoid an extra full config copy.
- `reporting/experiment.py` retains explicit unavailable metrics and reports existing
  session-end drawdown fractions as percentages correctly. Whole-account exit studies
  and ORB/VWAP comparisons do not claim common-entry causal attribution.
- CLI `experiment --plan --out` returns nonzero for partial or rejected studies.
  Existing `status` verifies its parent diagnostic seal and artifact inventory.

## Executed verification

Checks performed 2026-11-04 on Linux/Python 3.12 with the frozen environment.

- Before implementation: **33 historical-comparison/metric tests passed**.
- Initial new runner tests: **32 passed**.
- Expanded experiment, historical-comparison and package-boundary tests:
  **78 passed in 118.52 seconds**.
- Independent reviewer: **no blocking findings**; independently passed 20 focused
  checks and seven final economics/provenance/failure checks. A mistaken arithmetic
  comment was corrected; expected results were already correct.
- Reviewed Python fingerprint:
  `cb47008f19c29b70b0473431f44bcff4eacdf05f1a5d125a53725476514bffda`.
- Final full suite: **1,273 passed, 1 real-Alpaca integration test deliberately
  deselected in 456.22 seconds**, zero failures/skips. Command:
  `uv run --offline --frozen pytest tests/ -q`, with dummy Alpaca credentials and
  a verified inherited Python socket/DNS tripwire. Logs/exit record:
  `/tmp/br06-guarded-full.log`, `/tmp/br06-guarded-full.json`.
- Verification correction: the temporary guard from the previous session had been
  cleared, so the initial 1,273-pass full run did not establish the outer network
  tripwire. The guard was recreated, its DNS rejection verified, and the entire
  suite repeated successfully on the same source. The application-level network
  negative-control test was present in both runs.
- Ruff, public `experiment --help`, Copilot configuration/local-link checks and
  `git diff --check` passed. No real provider request was made.

`tests/test_experiment_plan.py` exercises public CLI execution, exact positive and
negative fixture outcomes, zero-trade/undefined states, all-arm validation before
cache/child access, forbidden dates, parameter-category isolation, changed costs,
feed-delay/full-system comparisons, transactional contention for the final slot,
external references, persisted counts across reruns, stop/continue/interruption,
real preparation failure links, network-negative control, code drift, tampered
artifacts, final-manifest failure, HTML escaping, input retention and drawdown units.

Independent fixture arithmetic uses eight shares at `102.5 × 1.0005`, a timed exit
at `104 × 0.9995`, zero fees: net `11.17400`. The declared early target `102.91` gives
`2.458360`. An exit fee of `20` reduces allowed size to two shares and gives
`2 × (103.9480 − 102.55125) − 20 = −17.20650`. A prohibitive entry fee yields zero
trades, not a fabricated profit factor. A failed fourth arm remains unavailable and
consumes its slot. These are synthetic software checks, not market results.

## Remaining limits and next work

- Real Alpaca verification and manual trade reconciliation remain unrun; local access,
  an approved feed/date scope and actual action/coverage review are needed for BR-07.
- Native Windows execution was not available in this Linux environment.
- No automatic resume or trial refund: hard termination may leave a counted `running`
  reservation; inspect its child evidence. No global trial accounting across roots.
- No MFE/MAE, common-entry exit windows, confidence intervals, optimizer or automatic
  promotion. Session-end drawdown does not represent worst intraday drawdown.
- Final evaluation stays blocked. No merge or real provider call was performed.

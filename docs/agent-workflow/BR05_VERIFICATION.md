# BR-05: explicitly enabled Alpaca validation

Based on upstream `53526fd806c0d2422247a928a2b3d950bfa4041e`, the completed lossless
history-compaction change. Implements the software portion of
[BR-05](backtest-readiness-tickets.md#br-05--explicitly-enabled-alpaca-validation-harness).
The [operator guide](../ALPACA_PROVIDER_VALIDATION.md) defines the public workflow.

## Scope and evidence

`validate-alpaca` requires an explicit local request, exact development execution
and warmup dates, raw units, feed/mapping/action inputs and a finite session budget.
It reuses existing acquisition/cache/preflight/backtest services, validates all
settings before acquisition, retains immutable pins and attempts, proves cache reuse,
and runs the simulation under a Python socket/DNS tripwire. The report separates
software status, per-check results and the still-pending real provider/research gates.

Shared file-only finalization is extracted from preflight, retaining its failure
tests. An invocation link is an optional addition to the existing diagnostics schema.
Existing commands and legacy evidence readers remain supported. The single actual
backtest creates one research trial; acquisition/preflight/validation attempts do not.
No trading, holdout opening, research-default change or merge is part of this work.

## Tests and review

Checks ran 2026-11-04 UTC on Linux/Python 3.12 with the locked environment.

- Focused provider, preflight and acquisition checks: **125 passed in 144.66 seconds**.
- After final hardening: **7 retention/integration-selection regressions passed**.
- Independent reviewer: **no blocking findings** on the final Python fingerprint
  `ae0000be3217bbd388ce40f1379fde92c5540643c71567de28186098b334589f`.
  The reviewer independently passed 44 initial provider tests, six existing shared
  finalization/failure checks, seven retention/selection checks and 14 affected
  transport/retention/selection checks on the final source.
- The review found an invalid-template retention gap. Fixed by capturing the template
  path/hash and redacted parsed config before dependent validation; malformed inputs
  retain identity without raw content. The reviewer re-ran the original reproduction.
- A final opt-in boundary check found that a falsey injected transport could fall
  back to the real downloader. Four regressions first failed with the original
  constructor, then passed after explicit `None` selection and callable validation.
  The independent reviewer reproduced both failure shapes and verified the repair;
  all requests were intercepted, with zero real HTTP calls.
- Final full suite: **1,235 passed, 1 integration test deliberately deselected in
  279.93 seconds**, zero failures/skips, using `uv run --offline --frozen pytest
  tests/ -q` with inherited socket/DNS tripwires and dummy Alpaca credential
  variables. The excluded real sample is not counted as passed. Logs and exit record:
  `/tmp/br05-final-full.log`, `/tmp/br05-final-full.json`.
- Public `validate-alpaca --enable-live` and `status` completed successfully with an
  intercepted synthetic provider: exactly six fake page requests, zero real HTTP.
  These artifacts were saved under `/tmp/br05-public-cli-g4byd03d` for inspection;
  they do not establish provider access.
- Ruff, public command help, Copilot metadata/local-link checks and `git diff --check`
  passed. Default/integration safety checks ran with socket/DNS tripwires and dummy
  credential environment variables; no real provider request occurred.

`tests/test_provider_validation.py` covers the real service composition with a fake
provider, exact dates/request counts, hand-calculated two-trade equity, unchanged
pins, repeated runs, explicit refresh preserving old objects, pagination/empty pages,
numeric and conflicting duplicates, malformed timestamps/OHLC, 401/403 and bounded
429/5xx retries, sanitized errors, partial-download reuse after interruption, denied
scope before cache/client construction, network-tripwire negative control, HTML
escaping, failed final manifest writes, public CLI/status and artifact tampering.

`tests/integration/test_alpaca_sample.py` is excluded by default. Explicit selection
without the enable flag and local request/output controls fails before HTTP. Its
real-provider execution is not run here and is not counted as passed.

## Remaining gates

- Real feed access, response fidelity and entitlement: unrun. Local credentials and
  explicit inputs are required for the bounded check; do not share keys in chat.
- Corporate-action audit, actual arrival/correction history and manual trade
  reconciliation: not established by fake transports or a successful download.
- D12/D20/D22 and the existing imported decision-date provenance warning are
  preserved. Research stays `unscored`; final evaluation remains blocked.
- Native Windows execution is unrun in this Linux environment.
- BR-06 finite experiment orchestration is next and can be built offline. BR-07
  needs the real-provider gate and explicit research protocol/settings.

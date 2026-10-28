# Coding-agent handoff: finish M0, then build the replay path

Updated: 2026-10-28. Applies to the reviewed `intraday-rsys.zip` snapshot. Recheck the checkout before implementing; do not assume a later branch still has the same defects.

## Next task

**Complete M0-01: configuration validation. Then complete M0-02: reliable run evidence.** The roadmap already exists in [tickets.md](tickets.md); this handoff adds the concrete findings needed to finish the current foundation. It does not replace the strategy or research specifications.

This update creates documentation only. No Python fixes, ZIP replacement, GitHub push, market experiment, or trading action is claimed here. Dispatch the prompt below to authorize the coding work.

## Prompt for the coding agent

> Implement **M0-01 only**, following `AGENTS.md`, `docs/NEXT_STEPS.md`, and the M0-01 row in `docs/tickets.md`. Read the referenced contracts and decision register first. Inspect the current checkout and reproduce the applicable defects before fixing them. Add regression tests, preserve fixture/research separation, and do not invent unresolved market settings. Run the M0-01 acceptance checks, update requirement-to-test mappings and the README to reflect actual behavior, then report changes, executed checks, remaining blockers, and readiness for M0-02. Do not begin M1, fetch market data, or trade as part of this task.

## Existing sources to use

| File | Purpose |
|---|---|
| [../AGENTS.md](../AGENTS.md) | Working rules and scope |
| [tickets.md](tickets.md) | Ordered tickets and completion commands |
| [decisions.md](decisions.md) | Decided, proposed, and open choices; what each blocks |
| [contracts.md](contracts.md) | Config rules, event phases, acceptance order, ledger and CLI contracts |
| [requirements.md](requirements.md) | Requirement IDs, test mapping, and future-perturbation harness |
| [data_rules.md](data_rules.md) | Corporate actions, calendar-aware volume history, and data quality |
| [strategies/orb.md](strategies/orb.md), [strategies/vwap.md](strategies/vwap.md) | Exact strategy state machines and expected behavior |
| [experiments.md](experiments.md) | Fair comparisons, chronological evaluation, holdout protection, and success criteria |
| [../fixtures/README.md](../fixtures/README.md) | Synthetic fixture interpretation |

Use [product.md](product.md) and [technical.md](technical.md) for context; [review_changes.md](review_changes.md) records later specializations. Surface a material conflict rather than silently choosing a new trading rule. `contracts.md` mentions an `examples/` directory that is absent from the reviewed snapshot: use its written record contract, and label any new examples as illustrative.

## Verified starting point

- Implemented: typed YAML profiles, partial parameter validation, the `validate` CLI command, and standalone run-record/lifecycle helpers.
- The previous review ran all 14 existing tests successfully. Independent probes still found invalid configurations and mutable/inconsistent evidence. Passing those 14 tests does not complete M0.
- Not implemented: market-data normalization, replay, scanner, strategy execution, ledger/fills, metrics, comparisons, or provider integration.
- `fixture`, `run`, `compare`, and `final-evaluate` remain CLI stubs. Synthetic JSON cases are expected outcomes, not executed backtests.

## M0-01: configuration fixes

**Primary files:** `src/rsys/config.py`, `registry.py`, `cli.py`, and `tests/test_config.py`.

1. **Normalize invalid-input handling.** Malformed YAML and a non-string `mode` currently escape the expected error path. Return a concise configuration error and CLI exit code 2. Keep unknown-field rejection and list all resolvable blockers without exposing secrets.
2. **Enforce fixture invariants.** Reject `network_allowed: true`, evidence labels other than synthetic-only, nonpositive starting cash/quantity step/price tick, and negative costs or delays. These checks validate the profile; the later fixture runner must independently enforce offline execution and synthetic report labels.
3. **Enforce numerical domains.** Apply the documented positive/nonnegative/finite checks to capital, quantities, costs, risk/exposure settings, position counts, trial budgets, and existing scanner/exit parameters. Validate fraction domains. Do not select actual risk limits or costs for the owner.
4. **Validate supported values.** Reject empty required identifiers and unsupported modes, strategy/scanner IDs, adjustment policies, sides, and other declared enums. Keep proposed v1 behavior distinct from confirmed market choices. Do not imply an Alpaca entitlement or strategy implementation exists merely because its name parses.
5. **Enforce dependencies.** Require maximum calendar lookback to cover the requested lookback. Parse dates and local times properly; validate ordered, nonoverlapping development/validation/final intervals. Enforce static interval/duration constraints. Mark calendar- or data-dependent checks as deferred until their actual M1 inputs exist, and enforce them before execution.
6. **Make gates structurally meaningful.** `promotion_gates: {}` must not satisfy D17. Define required fields from `experiments.md`; report unresolved values or unspecified method details instead of fabricating thresholds or a statistical algorithm.
7. **Keep validation reusable.** Preserve one validation entry point for CLI and future runners. A parsed model is not proof that all decisions, runtime preconditions, or provenance checks passed. Keep proposed values and unresolved decisions explicit.

### M0-01 acceptance

- The checked-in fixture profile passes; altered unsafe fixture profiles fail.
- The pending research profile reports all 27 currently unresolved fields and their decision references. Intentional schema changes require an explained test update, not removal of blockers to make tests pass.
- Regression cases reject negative capital/costs/risk, zero quantity steps or position limits, invalid fractions, inadequate lookback caps, malformed dates/times, overlapping splits, a zero trial budget, empty gates/feed, and unsupported strategy IDs or execution adjustments.
- For tests requiring a fully populated profile, use explicitly named synthetic test values. Do not turn them into market defaults.
- Malformed YAML, invalid mode types, unknown fields, and missing files produce controlled CLI failures without an uncaught traceback.
- Add stable requirement IDs for newly formalized invariants in `requirements.md`; map each to an executable test. Preserve existing IDs.

```bash
uv run pytest tests/test_config.py
uv run ruff check src/rsys/config.py src/rsys/registry.py src/rsys/cli.py tests/test_config.py
```

## M0-02: run-evidence fixes

**Primary files:** `src/rsys/experiments.py`, `storage.py`, and `tests/test_run_record.py`. Begin after M0-01 is accepted.

1. **Immutable inputs.** Separate frozen run inputs from changing status/results. Reject changes to resolved settings or fingerprints after creation. Identical configurations may have separate attempt IDs; never overwrite or restart a completed attempt.
2. **Legal lifecycle and recovery.** Enforce allowed status transitions, record handled interruption with an end time, and define recovery for a process that dies while marked running. Keep unsuccessful attempts. Do not pretend a force-killed process executed cleanup.
3. **Consistent persistence.** A record update and its transition must not disagree after a write failure. SQLite transactions are a suitable option already proposed in D04; a documented recoverable journal is also acceptable. Keep this local and small; no distributed infrastructure is required.
4. **Correct secret handling.** The current broad key-name matcher removes legitimate values such as `ranking_sort_key`, allowing different experiments to share a hash. Use explicit credential handling, preserve research settings, and sanitize saved error details and tracebacks. Test with fake secret canaries only.
5. **Complete provenance.** Capture configuration, code, dependency-lock, and input-data identity before execution. Include dirty source changes, not just a Git commit. Use a small local synthetic input for tests. If no data is involved, record an explicit not-applicable reason; missing required provenance must not masquerade as a complete research record.
6. **Safe storage paths.** Reject absolute/traversal run IDs and ensure resolved paths stay inside the storage root. Preserve duplicate-ID rejection and prior artifacts.
7. **Validated execution boundary.** Require an appropriate validated profile before running an experiment. Storage primitives may remain generic. Do not mark the still-unbuilt backtest CLI as functional.

### M0-02 acceptance

- Mutation of frozen inputs is rejected; every saved fingerprint corresponds to its saved canonical inputs.
- A finalized attempt cannot transition back to running; a rerun gets a fresh ID.
- Ordinary failure, handled interruption, and injected persistence failure preserve truthful, recoverable status.
- Fake secrets never appear in saved records/errors; legitimate configuration differences produce different fingerprints.
- Traversal IDs cannot escape the root, and provenance is complete or explicitly unavailable/not applicable.
- Update lifecycle/storage contracts and traceability for any new states or persistence design.

```bash
uv run pytest tests/test_run_record.py
uv run pytest tests/
uv run ruff check .
```

Run the full suite and lint once at M0 integration. Report unavailable, failed, or skipped checks explicitly; none counts as a pass. Refresh the stale README with working commands and remaining stubs.

## After M0: follow the existing tickets

| Order | Ticket | Concrete result |
|---|---|---|
| 1 | M1-01 | Normalized synthetic bars, instrument/session contracts, data-quality and calendar tests |
| 2 | M1-02 | Chronological event replay, bounded as-of views, deterministic same-time batches, and no-future-access tests |
| 3 | M2-01 | Decimal ledger, reservations/sizing, simple conservative fills, position management; F01–F17 pass |
| 4 | M2-02 | Causal features, scanner, and ORB following the existing exact specifications |
| 5 | M2-03 | Independently replaceable VWAP touch-and-hold strategy |
| 6 | M2-04 | Connected synthetic run/compare/report path, trade trace, account metrics, and protected holdout boundary |
| 7 | M3 specifications | Eligible-universe controls, matched random entries, full-window exit study, and uncertainty/promotion method |

Use each ticket's existing acceptance command. Preserve the core universe → replay → positions/features → scanner → strategies → proposal → acceptance → execution → results flow. Manage held positions even after a symbol leaves the scanner. Add strategy-specific config models when their tickets need them; do not force VWAP parameters into the current ORB-shaped model.

## Market decisions and evidence

- M0–M2 can proceed on explicit synthetic inputs. Do not block independent offline engineering on missing brokerage choices.
- Before market experiments, resolve applicable proposed/open decisions in `decisions.md`, especially D09–D13 and D16–D17; D19 controls expense interpretation. Use the early feasibility task in `tickets.md` for a separately authorized, bounded data audit.
- Do not access final-holdout outcomes during development. Do not begin parameter optimization before the comparison protocol, dates, search budget, and gates are frozen.
- Defer Kronos, Nautilus integration, additional strategies, and paper execution to their own tickets. The immediate goal is a trustworthy path from inputs to trades and account results.
- Software success means the causal/accounting tests pass. Strategy evidence requires appropriate unseen-period comparisons after costs and with shared-account constraints. A green test suite or synthetic profit is not evidence of a profitable strategy.

## Required agent completion report

State: ticket completed; changed files and reasons; exact commands and results; requirement IDs covered; remaining failures/open dependencies; whether the next ticket is ready. Distinguish software checks, hand-calculated fixtures, historical results, and paper results. Never invent performance results.

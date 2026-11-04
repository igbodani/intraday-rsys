# Backtest readiness: agent to-do and delivery plan

**Status: BR-00–BR-06 software implemented; BR-07–BR-08 remain planned. The BR-05 real-provider gate is unrun.** See the [BR-01 handoff](BR01_VERIFICATION.md), [BR-02 streaming verification](BR02_VERIFICATION.md), [BR-03 operator verification](BR03_VERIFICATION.md), [BR-04 data preflight verification](BR04_VERIFICATION.md) and [BR-05 provider workflow verification](BR05_VERIFICATION.md) for behavior, checks and limits. This plan was prepared on 2026-11-03 for `docs/copilot-backtest-readiness`, based on `develop` commit `a40c91260afc81b0ed79d5e4a87000c85c24f30d`. PR #4 is merged into that base. Its historical baseline was 937 passing offline tests; agents must establish their own checkout baseline rather than copying that count as new evidence.

## Goal and reading order

Make a small historical experiment understandable and recoverable: see progress while it runs, retain useful evidence when it fails, verify local data before simulation, and compare a finite declared set of strategies/settings without mixing assumptions. First prove the software with fixtures; then validate a bounded real Alpaca sample before strategy research.

1. Read this plan and [AGENTS.md](../../AGENTS.md).
2. Implement one task from [the development tickets](backtest-readiness-tickets.md).
3. Use [the testing plan](backtest-readiness-tests.md) for independent acceptance checks and review.
4. Return [the handoff](handoff.md), with actual commands, outcomes and the next unblocked task.

The existing [Copilot roles and skills](../COPILOT_WORKFLOW.md) remain authoritative. This pack adds a work order, not another agent framework. All new commands, flags, modules and test names in these documents are **proposed interfaces** until their owning ticket is implemented. Commands labelled **existing** can be used now.

## What already works

| Existing capability | Reuse these boundaries |
|---|---|
| Alpaca acquisition, retries, request-scoped immutable cache, pinned local backtests | `data/alpaca.py`, `data/bar_cache.py`, `application/historical.py` |
| Authorization before execution/warmup cache reads; final dates denied | `application/dataset_policy.py`, `research/holdout.py` |
| Completed-bar ORB and VWAP; scanner history; bar/trade synthetic execution | `strategies/`, `execution/bar.py`, `execution/trade.py`, `replay/` |
| Multi-session account, explicit equity sizing, loss/drawdown halts and costs | `application/batch.py`, `application/sessions.py`, `account/` |
| Sealed attempts, failures, comparisons and HTML reports | `research/storage.py`, `research/experiments.py`, `research/comparison.py`, `reporting/html.py` |
| Fixes to historical comparison axes, timestamps, duplicate bars and fixture trade conditions | [Pre-data audit](../PRE_DATA_VALIDATION.md) and its regression tests |

Do not rebuild these systems. BR-01 now saves operational invocation evidence before preparation and seals diagnostics at finalization. BR-02 streams the trading trace and saves inputs/session checkpoints during execution; `run_batch_cli()` prepares historical inputs before creating its research record; source hashes and resolved inputs are immutable once a record exists; scanner snapshots now share lossless observations/prefixes; real provider responses and large workloads remain unverified. BR-03 now exposes progress, read-only status, conservative recovery and per-session explanations; see [the operator guide](../RUN_DIAGNOSTICS.md). BR-04 adds offline data preflight/resource measurement; BR-04-F01 reduces retained history duplication; BR-05 adds the explicitly enabled provider workflow. BR-06 now runs finite plans with persistent attempt budgets and honest scorecards.

## Ordered to-do

Mark a box only after its acceptance tests and required review pass. A skipped live check is a named outstanding gate, never a completed check.

| Done | Ticket | Deliverable | Lead / reviewer | Depends on |
|---|---|---|---|---|
| [x] | BR-00 | Reproduced baseline and recorded lifecycle/trace design | Architect + engineer / adversarial reviewer | Current checkout |
| [x] | BR-01 | Safe append-only operational journal and early invocation record | Python engineer / adversarial reviewer | BR-00 |
| [x] | BR-02 | Streaming semantic trace and interrupted-run evidence | Python engineer / adversarial reviewer | BR-01 |
| [x] | BR-03 | Progress, status, recovery and useful failure/no-trade explanations | Python engineer / adversarial reviewer | BR-02 |
| [x] | BR-04 | Offline pinned-data preflight and resource inventory | Python engineer / quant researcher + reviewer | BR-01; integrate with BR-03 |
| [x] | BR-05 | Opt-in provider validation software/checklist; real gate unrun | Python engineer + quant researcher / reviewer | BR-03, BR-04 |
| [x] | BR-06 | Finite experiment plans, complete attempt registry and comparison scorecard | Architect + engineer / quant researcher + reviewer | BR-03, BR-04 |
| [ ] | BR-07 | First real-data reconciliation and controlled ORB/VWAP study | Owner + quant researcher / reviewer | BR-05 real-data gate, BR-06, explicit research settings |
| [ ] | BR-08 | Specification and tests for one additional approved momentum strategy | Quant researcher + architect + engineer / reviewer | BR-07 and a complete strategy definition |

**Next external work:** the BR-05 real-provider check and BR-07 reconciliation, with owner-supplied access and explicit inputs. BR-06 finite experiment plans are implemented; see [verification](BR06_VERIFICATION.md). **Before a real sample:** BR-05's software must pass, and the owner must supply access and explicit inputs. **Before comparative market experiments:** BR-06 and the research checklist must pass. BR-08 is a later bounded ticket, not permission to invent or implement a catalogue of strategies.

One engineer owns a ticket and its shared files. A researcher may audit source/decision requirements while the engineer builds offline logging. Do not let concurrent writers edit `application/batch.py`, CLI parsing, storage schemas or the same tests. Reviewers use a separate session/check-out view and leave tracked files unchanged. A plan does not automatically launch subagents; use the existing client-supported role handoffs.

## Agent assignments and handoffs

| Agent | Required output | Boundary |
|---|---|---|
| `architect` | Exact public contract, package/model ownership, lifecycle transitions, migration impact and test oracle | Read-only design; no new market assumptions |
| `python-engineer` | One working vertical slice, meaningful tests, run artifacts and evidence handoff | Only implementation writer; no automatic live calls or merges |
| `quant-researcher` | Evidence classification, decision dependencies, provider verification checklist and experiment isolation | Does not select profitable settings after looking at outcomes or change decision statuses |
| `adversarial-reviewer` | Concrete failure reproductions, checks run, coverage limits and review verdict | Does not edit the engineer's code or call its own implementation independently reviewed |

For BR-00, ask the architect to inspect the linked source paths and return the concrete design described in that ticket. The engineer records it in the handoff and implements following tickets. Routine engineering decisions do not need owner approval; record them. Ask the owner only for genuinely missing research inputs/access, and continue unrelated offline work.

## Cross-ticket contracts

- **Separate clocks:** wall-clock timestamps, elapsed runtime, progress and machine/process identity belong in operational diagnostics. Simulation timestamps, decisions, fills and account values belong in the deterministic semantic trace. Logging must not change strategy visibility, event order, costs or any account outcome.
- **Separate outcomes:** software completion, data eligibility, research evidence and promotion status are different fields. An exit code of zero never means profitable, complete market coverage or approved for paper trading.
- **Keep old evidence readable:** existing schema-2/3 runs, synthetic callers and public command output remain compatible. Add versioned diagnostics/comparison fields; do not silently reinterpret old hashes or relax artifact checks.
- **Do not rewrite frozen inputs:** `RunStorage` freezes `resolved_config`, config/data/code hashes and parent identity. Early preflight logging needs an invocation record that can later link to the final research run; do not move `start_run()` earlier and then mutate its frozen fields.
- **Start before expensive work:** allocate an invocation ID and persist sanitized original-request identity before cache scans, network requests, historical expansion or simulation. A preflight crash must leave a discoverable invocation, even if no resolved research run exists yet. Linking invocation and run IDs must not count them as two trials.
- **Never fake completion:** missing exit references, failed output writes, interrupted runs and incomplete/invalid journals remain non-success. Preserve primary failure details if a later cleanup/write also fails. If the disk cannot write at all, return nonzero and state that durable evidence could not be saved.
- **Model ownership:** plain records, enums and configuration schemas go in each owning package's `models.py`. Core `data`, `replay`, `strategies`, `account` and `execution` must not import application, CLI, research storage or reporting. Use narrow optional sink/callback contracts; application code wires them to writers.
- **No new research defaults:** keep existing explicit costs/risk and source/feed identity; no silent fallback feed, date split, zero-volume insertion, strategy threshold, trial budget or promotion score.
- **Offline by default:** all default tests and simulations use pinned local inputs. Credentials do not make a test eligible to call Alpaca automatically. No broker order endpoints, purchases, live trading, final holdout access or notifications to people are part of this pack.

## Research inputs the owner must supply

These values are not needed to implement or test BR-00 through BR-06 with declared fixtures. They are needed before the corresponding real-data actions.

| Input / decision | Required evidence or choice |
|---|---|
| D12: source/feed/access | Locally configured credentials, chosen entitled feed, current official provider contract and actual bounded response check |
| D13 / D20: scope and actions | Explicit symbol and action-reviewed window, raw units, mapping policy; current calendar is 2024 only |
| D14 / D15: scanner and strategy | Explicit lookback/search budget, liquidity/freshness settings and entry/exit parameters; no unreviewed defaults silently activated |
| D10 / D11: account and costs | Preserve recorded policy through explicit settings; provisional slippage/delay stay labelled provisional |
| D16: study dates and trial accounting | Chronological development/validation/final dates; finite named variants; explicit budget/counting rule and seeds if used. The proposed 50-trial cap is not an approved default. |
| D17: interpretation | Predeclared controls, sample sufficiency and uncertainty/promotion rules before scoring. Until complete, results remain `unscored`; no automatic winner or promotion. |

D09's broker account/settlement verification does not prevent the declared synthetic-account historical slice. D22 does not permit treating fixture trade conditions as Alpaca rules. Preserve the decision register's imported-date provenance warning. Do not resolve any of these questions by choosing whichever setting improves the report.

## Delivery sequence and stopping conditions

1. **Engineering-ready:** BR-00–BR-04 pass offline checks, failure injection and independent review. An operator can inspect a running/failed attempt, identify missing data and locate preserved evidence.
2. **Provider sample verified:** BR-05 passes offline tests and its explicitly enabled real-data checklist. Without credentials/access, report `live verification not run`; software implementation may be complete while this gate remains open.
3. **First controlled research:** BR-06–BR-07 use the same pinned datasets, code, account and costs for both arms. Record all attempts, including zero-trade and failed variants. Only then expand dates or implement BR-08.

Do not expand the calendar, universe, data intervals, execution model or storage format while doing the logging work. Resource measurements can motivate a separate evidence-compaction ticket; a logging change must not claim to solve the scanner's existing memory growth.

## What to tell Copilot

Select `python-engineer`, use `/build-backtest-readiness` in a compatible local client, and supply a ticket ID. Otherwise paste:

> Read AGENTS.md and docs/agent-workflow/backtest-readiness-plan.md, then implement BR-00 from backtest-readiness-tickets.md. Inspect this checkout, reproduce the baseline and record the lifecycle/trace design for review. Next, implement BR-01 only once its technical dependency is resolved. Use backtest-readiness-tests.md, keep all provider calls offline, preserve frozen evidence and put plain models in their owning models.py. Return actual checks and a handoff. Do not invent missing research settings or merge changes.

For later work, replace the ticket ID. To the independent reviewer:

> Review ticket BR-XX against the supplied base/head and its test rows. Inspect the public call path, try to falsify its preservation/causality claims, and run focused offline checks. Leave tracked files unchanged. Report concrete findings, unverified limits and a verdict for this exact diff.

For a multi-ticket request, still implement and verify one bounded slice at a time. Reuse the established design and approvals; do not ask for permission again for routine work already authorized. Stop only the work that depends on missing research input.

## Publication and completion

This branch contains the specifications, prompt, BR-01–BR-06 software and BR-04-F01 evidence compaction. The remaining interfaces are proposed until their tickets pass. Future feature branches should start from this branch or a descendant containing it; after it is merged, start from the updated `develop`. Use one focused PR per ticket or tightly coupled slice. Do not merge or run live acquisition merely because a planning document says they are later steps.

Every ticket handoff must include its ID, base/head or working-tree scope, changed files, design/compatibility decisions, tests actually run, evidence paths, supported failures, review result and remaining owner dependencies. Store generated runs and credentials outside Git. Passing this pack's structural checks proves its references/metadata are valid, not that the proposed logging features already work.

## Original specification-pack verification (historical)

The delivered documentation/prompt diff passed the Copilot metadata/local-link checker, Ruff and `git diff --check`. All **937 existing offline tests passed in 121.82 seconds** with the frozen environment. A document consistency check verified nine unique tickets, 24 acceptance scenarios, balanced code fences and 25 existing test-file references. Application source, runtime tests, dependencies and fixture/config inputs are unchanged. No proposed ticket is marked implemented, and no provider call or download was performed while preparing this pack.

BR-04 measured substantial scanner-evidence growth on small synthetic workloads.
[BR-04-F01 evidence compaction](evidence-compaction-followup.md) is implemented
and measured, but real-data pilots should remain bounded before scaling experiments.
BR-05 software is implemented; its live check still requires explicit access and inputs.

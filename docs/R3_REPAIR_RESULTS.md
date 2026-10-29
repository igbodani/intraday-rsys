# R3 repair results

Date: 2026-10-29 UTC. Scope: the supplied `intraday-rsys(3).zip` snapshot, whose embedded Git HEAD is `7084d4a` and whose newer working files had 381 passing tests. The starting point is the uploaded working tree, not that older commit. Existing prior repairs were preserved. The repair pass performed no M2 implementation, market-data request or broker action. GitHub publication is a separate follow-up requested by the owner.

## Outcome

The reproduced historical-visibility leaks and missing runtime enforcement have been corrected in the public runner, contexts and CLI. Forming-bar observations remain supported. The current acceptance suite passes **426 tests**, up from 381; lint is clean. Eight real CLI invocations (four configurations, twice each) produced distinct retained attempts with stable semantic hashes within each pair.

The original 381-test suite passed before this repair. Ten added regression cases failed on the supplied code before fixes: delayed timer visibility, delayed action visibility, automatic closure, completed-bar cadence, five unsupported configuration cases and public quote freshness. Additional acceptance tests now cover whole runner-generated prefixes, same-time phases/symbols, delayed first arrivals, provider redelivery/conflicts, source coverage, receipt/model timing, CLI observer dispatch, failure retention and parity prerequisites.

This establishes the tested software behaviors below. It is not a general proof of every possible event stream, verified provider behavior, execution realism or strategy profitability.

## Corrections and how they work

| Area | Previous failure or gap | Implemented behavior |
|---|---|---|
| Historical visibility | A late closure/action could publish into a past cutoff; saved contexts could consult later router contents | Immutable context event/version collections; fixed availability/phase/sequence cutoff; monotonic publication guards; delayed effects published at availability |
| Clock and cadence | Closing required an input timer; closed-only callbacks ran on unrelated arrivals | Internal per-minute closure schedule; completed-publication cadence; explicit revision opt-in; independent intrabar batch cadence |
| Configuration and inputs | Unsupported intervals/rules/policies/dates passed parsing; recorded timing lacked receipts | Implemented-only literals and cross-field checks; supported regular-session dates; declared timing/source/window/interval checks before dispatch |
| Quotes | A separate freshness helper did not constrain strategy-facing access | Public usability filtering, explicit diagnostic state, no fallback to an older good quote after a bad update; quotes remain separate from trade bars |
| Coverage | Timer/end-marker presence was used as completion evidence | Separate source inventory with delivery counts and per-minute observed/verified-no-trade/missing states; no fabricated quiet bars |
| CLI and evidence | Observer selection was absent; unreadable input could escape retained attempts | Two configured synthetic observers; every evaluation recorded with input references; one read/hash/decode of input bytes; explicit unavailable provenance and retained failures |
| Corrections/actions/parity | Unsupported split combinations and publication comparisons could overclaim | Late-known actions invalidate affected buckets without changing past contexts; mixed-unit runs are non-success; conflicting provider revisions rejected; parity needs matching source/session/units and explicit revised-version cutoff |
| Acceptance | Snapshot-only comparisons could miss future leakage | Entire runner-generated prefix comparisons after complete runs, saved-context rechecks, reversed input, multiple symbols, future trades/quotes/corrections/actions, and an intentionally leaky negative control |

## Files changed

- Runtime and causal access: `src/rsys/replay.py`, `replay_runtime.py`, `views.py`, new `data/keys.py`, `data/bar_builder.py`, `data/bar_version_store.py`, `data/events.py`, `data/quotes.py`.
- Input/CLI/evidence: `src/rsys/config.py`, new `replay_validation.py`, `replay_io.py`, `cli.py`, `experiments.py`; comparison: `parity.py`.
- Acceptance: new `tests/test_r3_clock_fixes.py` and `tests/test_replay_contract_fixes.py`, plus updated bar-revision, config, CLI and provider-parity tests.
- Fixtures/configs: declared receipt/coverage examples in the existing bar/trade fixtures; corrected relative input paths and window declarations; new `replay_intrabar_observer_fixture.yaml` and `replay_closed_observer_fixture.yaml`.
- Documentation: README, contracts, decisions, requirements, tickets, this report. Prior R3 handoffs are retained with an explicit historical/superseded notice.

No dependency or lockfile change was needed. Existing tests were adjusted only where the tightened contract made their synthetic setup invalid or where the old test bypassed the real path: late-event examples now have actual later availability; quote examples select the compatible profile; parity examples carry source/session metadata and a cutoff; CLI replay tests exercise the real orchestrator and expect the declared data-error code. The detailed requirement mapping is `docs/requirements.md` R3SAFE-001 through R3SAFE-012.

## Verification executed

A fresh Linux environment was installed from the existing lockfile. The archive's copied Windows environment was not used. Commands were run from the project root with `UV_PROJECT_ENVIRONMENT=/tmp/rsys-r3-venv`:

```bash
uv sync --frozen --group dev
uv run pytest tests/ -q --tb=short
uv run ruff check .
```

Results: **426 passed**, **All checks passed**. The intentionally leaky harness is a passing negative-control test because it verifies that its prefix comparison detects the leak; it is not an ignored failure or xfail.

Each command below was also executed twice, with a separate evidence root for each configuration:

```bash
uv run rsys replay --config configs/replay_bar_fixture.yaml --out <evidence-root>/bar
uv run rsys replay --config configs/replay_trade_fixture.yaml --out <evidence-root>/trade
uv run rsys replay --config configs/replay_intrabar_observer_fixture.yaml --out <evidence-root>/intrabar_observer
uv run rsys replay --config configs/replay_closed_observer_fixture.yaml --out <evidence-root>/closed_observer
```

SQLite status and manifest completeness were checked for all eight attempts, along with distinct IDs and matching semantic hashes within each pair. Named intrabar observers produced eight evaluation records per run (including no-decision records); named closed observers produced two. Baseline replay examples intentionally select no observer.

| Config suffix | First run ID | Second run ID | Semantic trace hash (both runs) |
|---|---|---|---|
| `bar` | `20261029T164737751930-fb19eb64` | `20261029T164738023878-87f7deec` | `9961517ba13482e015e5fa7078279edb8dc47d3be85dfcf08b9e4b94ec4a49d3` |
| `trade` | `20261029T164738266926-4523b7b6` | `20261029T164738491202-38fb7e4e` | `54e3670805dede3e7abd25a09cdbfd00d2582fa17825dec5840b11d441720277` |
| `intrabar_observer` | `20261029T164738722211-f93933c8` | `20261029T164738950731-731860ca` | `ab0465aca7f0093eed7c81c74bb1fd5b4aeb4581fb863510a209e0592e71ff62` |
| `closed_observer` | `20261029T164739183612-26dbf696` | `20261029T164739410649-4c7e7f68` | `f6b2e02e270e64ebc222698145f31324551848272e2cfd52098b891ce8dc35c9` |

The delivery's verification directory contains full manifests, JSONL traces, SQLite evidence roots, command logs and `cli_verification.json` with data/config/code/lock/rule hashes. Paths inside run records identify the verification workspace where those attempts actually ran; use the corresponding packaged relative directories when inspecting a downloaded copy. The semantic hash is portable; the resolved-config hash includes the resolved local input path.

## Migration

1. Apply these changes to the uploaded working-tree version, preserving existing prior repairs. Do not reset to `7084d4a`: that commit alone omits newer supplied work. The delivery patch is against the uploaded files.
2. Replay input paths are now relative to the YAML file, not the shell's current directory. The examples use `../fixtures/...`.
3. Add full `coverage` settings and source-JSON coverage declarations as shown in the updated fixtures. A short fixture uses `window_end`; `session_close` must match the calendar's actual close. Source inventory includes timers and repeated deliveries.
4. For recorded receipt timing, provide receipt timestamps. For reconstructed timing, choose `modeled_availability`, provide `modeled_delay_ms`, and label all external events modeled. Fixture receipt timestamps are synthetic test data, not captured-live evidence.
5. Select an observer explicitly to generate evaluations through the CLI. `none` preserves data-only replay. Threshold values are test-only. Revision-driven callbacks require `on_revisions: true`.
6. Consumers should accept trace/manifest schema 2, internal closure events, observer rows and CLI exit 3 for data/coverage errors. Every rerun gets a new attempt directory; existing evidence roots may be reused without overwriting attempts.
7. Recreate the Python environment locally. The original archive contains a platform-specific virtual environment that should not be reused on another operating system.

## Limits and next work

- One declared regular session, 2024 calendar, one-minute bars, one declared provider/feed, raw units and synthetic rules are supported by the CLI. Unsupported settings fail explicitly. Wider date coverage, multi-session ingestion and real provider rules remain separate work.
- Historical finalized bars or modeled trade timing cannot reconstruct missing original arrivals, corrections or transport delays. The fixture inventory is a declared source assertion, not independent proof of historical market completeness.
- Split-straddled intervals fail conservatively. Full split-aware rolling-feature and position conversion is not wired into replay; do not infer it from the separate conversion utilities. Quote freshness currently means age since availability under a synthetic policy, not venue freshness or guaranteed fill liquidity.
- Replay holds events/version history in memory and snapshots references for contexts. No market-scale throughput or bounded-memory benchmark was run. Research performance claims remain blocked on the provider/data audit.
- The revised parity comparator checks the supplied pair and cutoff; callers must select the correct as-of versions. Synthetic matching is not real-provider certification.
- Context APIs constrain cooperative strategy code; Python object privacy is not a security sandbox against malicious code. Future strategy/execution integration needs its own causality tests.
- Failure evidence is retained when the output destination is writable. Media/permission failures cannot guarantee a complete artifact set. No broad storage fault-injection campaign is claimed here.

The next ticket is **M2-01**: implement Decimal ledger, sizing/reservations and conservative execution using F01–F17, with no same-event lookahead fills and position management independent of scanner membership. Then implement the separately specified trade execution adapter and scanner/strategy tickets. Existing ORB/VWAP baselines remain completed-bar strategies; explicitly specified intrabar variants can use the repaired forming-bar context. D09–D13, D16–D17 and provider-facing D22 decisions remain open. This pass stops before M2.

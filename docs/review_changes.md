# Review patch — revision 1.1

All nine review items were addressed. The earlier product/technical Markdown snapshots are preserved; the proposed operational supplement now states its specializations explicitly. No market assumptions or risk limits were confirmed on the owner's behalf.

| Review item | Change |
|---|---|
| 1 Acceptance sort | Full deterministic key, proposal identity, cash reservation order and pending fill order |
| 2 Scanner description | Explicit RVOL-first baseline with liquidity eligibility and gain tie-break; no claim of a composite movement score |
| 3 VWAP name | Rename to `vwap_touch_hold_v1`; keep tested logic; true reclaim remains separate |
| 4 Corporate actions | Raw execution, as-of split conversion, position/feature units, unsupported-action policy and F17 |
| 5 Early-close RVOL | Select prior sessions that contain the requested elapsed minute; missing selected data stays missing; H01–H03 |
| 6 Fixture gaps | F11–F17 plus 4 timezone and 8 quality cases; traces added to requirements |
| 7 Repeatable command | Fixture `--out auto` creates a fresh retained temporary directory every run |
| 8 Ranking storage | Compact evidence with reconstruction digest; diagnostic full modes and measurement before scale |
| 9 Feed effects | D12 caveat, same-feed invariant, mixed-feed rejection and source references |

The pack now includes 17 account/execution cases, 4 strategy setup examples, 4 DST cases, 3 RVOL/calendar cases and 8 quality cases (36 named cases). M3 research tickets identify the fixtures/methods still required before their implementation.

Validation performed on the planning artifacts: parsed YAML/JSON, checked all listed hashes and ZIP integrity, recomputed stated account/sizing/split/RVOL arithmetic, validated timezone conversions with the IANA zone database, and checked case-ID and reference consistency. These are authoring checks. No engine tests, strategy experiments, provider account checks or market-data profitability studies were run.

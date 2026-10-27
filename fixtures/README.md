# Synthetic acceptance fixtures

`cases.json` supplies inputs and hand-calculated expected states. These are not simulated or observed strategy results. All money/price/quantity strings must be parsed as Decimal. Cases are independent; cash for a seeded position is already reduced by its purchase. Default profile is `configs/fixture.yaml`.

| Case | Hand calculation / expected behavior |
|---|---|
| F01 | Buy 5 × $100; mark at $102 gives equity $1,010; sell at $104 gives cash $1,020 and net profit $20 |
| F02 | Entry bar touches both $98 stop and $104 target; stop-first gives $990 final cash |
| F03 | Existing 5 shares bought at $100; gap to $95 exits there, giving $975 total cash and loss $25 |
| F04 | One share costs $101 + $1 fee, exceeding $100; zero shares fit; reject |
| F05 | Equal rankings use ABC before XYZ; ABC reserves/buys 6 × $100; XYZ can fit 4; final cash $0 and equity $1,000 |
| F06 | Session closes at 13:00; flatten at 12:55; selling 5 shares at $101 yields total cash $1,005 |
| F07 | Old $98 stop survives bar low $99; close-time change to $100 cannot hit retroactively; next open $99 exits for $995 total cash |
| F08 | Entry: 5 × $100.10 + $0.50 = $501; exit: 5 × $101.90 - $0.50 = $509; final cash $1,008 |
| F09 | Entry reference equals protective stop; reject nonpositive risk distance |
| F10 | A 100ms delay after 09:31 prevents use of the 09:31 open; first eligible open is 09:32 at $101 |

Checkpoint `realized_pnl` denotes cumulative net completed-trade P&L. Fees are deducted from cash when charged; reports must also expose expenses on open positions so marked equity reconciles before close. No time inside a bar is inferred from its high/low.

`bar_complete` contains its start/end interval; its `time_local` is the base availability time before a configured feed delay. `proposal` represents a decision event; an execution delay governs its readiness, not a retroactive feature timestamp. Missing bars in F10 are intentional because it tests open-event eligibility rather than indicator computation. Do not silently turn these targeted streams into full market-data fixtures.

`strategy_cases.json` supplies a positive and negative example for each strategy. Implement the additional edge tests listed in the strategy documents. Passing these account/setup fixtures alone does not validate profitability.


## Added review fixtures

| Case | Hand calculation / expected behavior |
|---|---|
| F11 | Accepted stop $98; actual entry open $97; reject and release reservation; cash remains $1,000 |
| F12 | Existing target $104, favorable open $107; conservative exit $104; cash $1,020 |
| F13 | $10 risk budget: `2q + $1 entry fee + $1 exit fee <= $10`; q=4, reservation $401, stop exit leaves $990 |
| F14 | $3 risk budget cannot cover one share's $2 distance plus $2 fees; reject |
| F15 | Total cash $900 includes unavailable $500; one $500 share cannot use spendable $400. After explicit release it can; final cash $400 plus holdings $500 |
| F16 | Candidate list drops AAA; its 5-share position still stops at $97; cash $985 |
| F17 | 2-for-1 split: 5 shares at $100 become 10 at $50; stop/target become $49/$52; equity stays $1,000; prior volume doubles |

`data_cases.json` covers timezone, calendar, half-day lookbacks and data quality. Each named expected outcome is for the future implementation to reproduce. The authoring check verifies input consistency and explicit arithmetic, not the behavior of an engine.

`rsys fixture --case all` runs the account/execution suite only. Strategy examples and data/calendar cases are inputs to their named pytest suites. Data-quality cases are minimal contract fragments, not complete provider records; their tests supply the common valid metadata before altering the stated field.

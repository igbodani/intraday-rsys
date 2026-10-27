> Markdown snapshot of the 26 October 2026 document. Read `decisions.md` and this pack's supplements for proposed implementation detail.

# Intraday Strategy Research System

Product definition and success criteria

Design version 1.0 | 26 October 2026 | Owner Daniel Igbokwe

We are building a research system to determine whether specific stock-selection, entry and exit rules can grow a small account after costs. It will replay historical markets, compare controlled experiments and carry promising rules into forward paper observation. The first release prioritizes reliable strategy evidence and fast iteration.

### Product objective

The account starts at $1,000. The longer-term aspiration is $1 million, with no withdrawals planned before that level. That aspiration does not set a return forecast, deadline or reason to increase risk. The product must be useful even when the evidence says that no tested strategy is ready.

### Who uses it

- The account owner chooses research priorities, reviews evidence and resolves risk, funding and deployment decisions.

- A research or coding agent implements registered experiments, reproduces results, investigates failures and reports what the evidence supports.

### Scope and decision status

| Established direction | Proposed first release |
| --- | --- |
| Stocks and ETFs; Alpaca; crypto remains tentative. | Regular-session, long-only research with no borrowing; crypto disabled. |
| Preserve the universe, replay, scanner, strategy and proposal flow. | A small modular Python system with a command-line interface and generated reports. |
| Prioritize strategy testing over detailed fill simulation. | Use simple conservative bar execution and cost stress tests. |
| Evaluate useful parts of Claude day-trader-skill, Kronos and Nautilus. | Adapt selected workflows; add forecasting and alternate backends through optional interfaces. |
| Configurable parameters and reproducible comparisons. | Automatic rule-based proposal acceptance in backtests; confirmation remains open. |

### First release boundaries

Historical research comes first. Forward paper trading is a later stage using the same strategy logic. Detailed order-book queues, an autonomous conversational trader, short selling, options, a hosted dashboard and live-money execution are outside the first release. Basic cash constraints, costs and causal timing remain necessary to judge profits.

## The research workflow

The core flow stays intact. Position management runs independently of candidate selection, so a symbol leaving the scanner cannot leave an open trade unmanaged.

- Configure the experiment. Choose dates, data snapshot, universe rules, scanner, strategy, exits, costs and account profile. Record the hypothesis before running.

- Select the universe. Build the eligible stock list using information known at that date. Keep universe membership separate from intraday opportunity ranking.

- Replay the market. Advance one clock across all symbols and reveal events in order. A strategy sees completed bars and only information available at that time.

- Update positions and features. Apply eligible pending orders and existing exits. Update shared indicators and account state; manage positions even when they are no longer candidates.

- Scan candidates. Rank opportunities using current relative volume, movement and liquidity. Store each ranking snapshot and reason for inclusion.

- Run strategies. Evaluate explicit setups and produce proposals with entry conditions, stops, exit policy, expiry and supporting feature values.

- Accept or reject. Apply the configured acceptance policy and portfolio limits. Resolve simultaneous proposals with a declared rule and log every rejection.

- Execute and record. Accepted proposals become pending orders, not immediate positions. Model entry at the next eligible event, then update cash and positions from fills.

- Evaluate and compare. Generate a run card, equity path, trade diagnostics and comparisons. Decide whether to reject, investigate, freeze or advance a candidate.

### What the user receives

- A run overview with the resolved settings, data coverage, status, net results and limitations.

- A trade inspector showing the scanner snapshot, proposal, acceptance decision, entry, stop changes and exit on a chart with timestamps.

- A comparison view that explains which single change improved or weakened results and whether that finding held on unseen periods.

The proposed first interface is a command line plus portable HTML reports. These same information views can later become application screens without changing the research logic.

## Strategy and signal coverage

Each strategy is a falsifiable hypothesis with a mechanical definition, configurable parameters and a separate version. Names such as bull flag are insufficient until the detection window, confirmation timing, trigger and invalidation are specified. Adding a strategy must not require rewriting the replay engine.

| Strategy family | Research question | Sequence |
| --- | --- | --- |
| Opening range breakout | Does a confirmed range break continue after the opening period? | First |
| Rising VWAP pullback | Does a reclaim of rising session VWAP improve entry timing? | First |
| Gap and go | Does a premarket-high break extend an opening gap? | Expand |
| Micro pullback | Does a brief retracement after a surge resume upward? | Expand |
| Bull flag | Does a defined consolidation after a price rise precede continuation? | Expand |
| Tight range breakout | Does compression followed by participation predict expansion? | Expand |
| VWAP mean reversion | Does an overshoot revert when the intraday reference is flat? | Expand |
| Failed breakdown recovery | Does a quick reclaim of prior support improve entries? | Expand |
| Gap down recovery | Does a controlled recovery capture part of an overnight decline? | Expand |
| Relative strength momentum | Do leaders outperform a simple volume-based ranking? | Expand |
| Earnings continuation | Does an as-of earnings surprise add value to a breakout? | Data gated |
| ABCD pattern | Does a confirmed swing sequence improve risk and reward? | Later |

### Features shared across strategies

Initial feature groups are same-time relative volume, dollar volume, gap percentage, opening and premarket levels, prior-day ATR, session VWAP and slope, time of day, and market direction. Every feature declares its lookback, units and earliest availability. A ratio of opening volume to average daily volume is a different feature from same-time relative volume.

News, float, short interest and earnings require historical sources with valid availability times. Missing sources disable dependent experiments. Forecasts and broad market filters are optional additions, tested separately from the underlying strategy. They do not receive credit merely for making a chart look plausible.

## How we test entry and exit quality

The product should identify which decision creates value. A profitable trade alone cannot tell us whether the scanner, entry rule, exit policy or market direction caused the result.

| Question | Controlled experiment | Evidence to inspect |
| --- | --- | --- |
| Does the scanner help? | Hold strategy and exits fixed; compare scanner rankings with a declared eligible-universe control. | Net returns, coverage, rejected opportunities and concentration. |
| Are entries well timed? | Hold exits and sizing fixed; compare the entry rule with matched random or simple entries on eligible stocks and times. | Forward returns at fixed horizons, net expectancy and adverse movement. |
| Do exits retain profit? | Hold entry opportunities fixed; compare fixed targets, trailing rules, time exits and session close. | Net profit, favorable move retained, giveback, losses and holding time. |
| Does the full strategy work? | Replay the complete portfolio with shared cash, actual ordering and frozen rules. | Account growth, drawdown, costs and performance on unseen periods. |

### Diagnostics that explain trades

- Maximum favorable excursion, or MFE, is the largest favorable move while a trade is open. Maximum adverse excursion, or MAE, is the largest adverse move. Both are outcomes measured after entry, never entry-time signals.

- Measure exit giveback and favorable-move capture, but do not demand the exact historical high as an achievable exit. Report both winning and losing trades.

- For exit comparisons, collect the full prespecified observation window from raw data. A trade stopped early under one rule cannot supply the whole price path for a different exit rule.

- An isolated entry or exit improvement must survive a complete account rerun. Different holding times can change which later trades a $1,000 account can afford.

### Comparisons must be fair

Controls use the same candidate availability, time windows, costs and capital constraints. A buy-at-open benchmark cannot trade stocks first selected later that morning. Random entries are repeated with registered seeds, not selected for favorable outcomes. Include a cash benchmark; a passive ETF benchmark supplies opportunity-cost context with its different holding exposure disclosed.

## Experiments and definitions of success

Run a fixed baseline, screen eligible strategies, tune a bounded set of candidates on development data, stress the surviving settings, then freeze one selection before opening final unseen data. Use chronological validation. A final test reused to select another candidate becomes development evidence.

### Experiment discipline

- Register the hypothesis, date split, parameter ranges, trial budget, metric and rejection rule before viewing results. Protect a final holdout and log every access.

- Change scanner, entry, exit and model layers separately before selected combinations. Keep costs and risk fixed during signal comparisons. Prefer stable neighborhoods of parameters over an isolated peak.

- Check higher costs, delayed entries, different months and market conditions, symbol concentration and dependence on a few large winners. Preserve sequences of losing days in drawdown studies.

- Keep failed and incomplete trials. Freeze strategy and configuration for paper observation; a rule change starts a new cohort rather than resetting the old results.

| Success level | Required outcome |
| --- | --- |
| Software correctness | Known-answer trades reconcile; future data cannot alter earlier decisions; repeated inputs reproduce outputs; missing data and invalid settings are visible. |
| Research usefulness | An agent can add a strategy, run bounded comparisons and explain results without changing unrelated modules. Every claim traces to a run record. |
| Strategy evidence | Positive net results on predeclared unseen periods, acceptable drawdown, sufficient observations and improvement over relevant controls. Publish uncertainty and cost sensitivity. |
| Paper readiness | Frozen rules run on arriving data with accountable signals, orders and positions. Observe performance and operational errors without treating paper fills as live execution proof. |

### Scorecard and interpretation

Report net account return, net average trade, profit factor, win rate with average win and loss, maximum drawdown and duration, trade count, turnover, cost burden and results by month and symbol. Include time and money spent on data and compute separately from trading P&L. Report sample size and uncertainty alongside any ranking.

Numerical promotion gates and risk limits require confirmation before results are viewed. The attached plan's 1% risk, 25% drawdown and +0.20R target are not adopted as approved requirements. Too little data produces an inconclusive finding. Failing to find an edge is a valid research outcome; consistent profit does not mean winning every day.

## Why this design and what comes next

| Choice | Reason and consequence |
| --- | --- |
| Small modular research core | Makes scanner, entry and exit experiments easy to isolate. Keep cash and timing checks; postpone detailed market microstructure. |
| Historical replay before paper | Historical data supports many controlled comparisons. Paper then tests frozen behavior on newly arriving data. |
| One strategy interface | The same rules consume a bounded as-of view in replay and paper mode. Changing the data source must not silently change the strategy. |
| Deterministic acceptance proposed | Rule-based approval makes historical runs reproducible. AI judgment may be a separately versioned experiment; its authority remains bounded. |
| Optional external integrations | Reuse useful components without making every run depend on a model, an LLM session or a large framework. |

### Role of external projects

Claude day-trader-skill contributes candidate workflow and rule ideas for scanning, entries, exits and risk review. Reused code must be inspected, attributed and tested; its simulation odds are not validation evidence. Kronos supplies optional candle forecasts that must beat simple forecasts and improve trading results after costs. NautilusTrader remains an optional later backtest comparison or backend. [1-3]

Alpaca provides data and the later paper interface. Feed access, historical coverage and account restrictions must be verified. Paper trading simulates fills and omits several live execution effects, so we retain cost sensitivity rather than treating paper results as guaranteed live outcomes. [4]

### Delivery sequence and open decisions

- First: data validation, causal replay, basic accounting, cash control, ORB and VWAP baselines, run records and reports.

- Next: configurable scanner and expanded strategy library, entry/exit comparisons, bounded tuning and final validation. Then add forecasting and forward paper observation.

- Confirm before dependent build work: automatic versus AI/manual acceptance, bar interval, eligible universe and sessions, account/quantity profile, risk limits, data budget, test dates, trial budget and numerical promotion criteria.

These documents define the product and proposed implementation. The companion technical design describes module boundaries and acceptance tests. Strategy results will be established through the specified experiments.

### Sources and decision provenance

Owner discussions and handwritten flow, 25-26 October 2026; supplied Intraday Strategy Research System Build, Test & Tune Plan, especially sections 7-9. Latest owner direction governs scope.

[1] https://github.com/Jakeschincariol/day-trader-skill

[2] https://github.com/shiyu-coder/Kronos

[3] https://nautilustrader.io/docs/latest/concepts/backtesting/

[4] https://docs.alpaca.markets/us/docs/paper-trading

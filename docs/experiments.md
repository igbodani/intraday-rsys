# Research protocol and success

## First research questions

1. **Scanner:** with entry/exit rules fixed, does ranking improve results over a declared eligible-universe control?
2. **Entry:** with sizing/exits fixed, does the entry rule improve fixed-horizon forward returns and net trade outcomes over eligible-time simple or seeded random entries?
3. **Exit:** for common entry opportunities and complete prespecified observation windows, which exit retains more net profit with acceptable losses?
4. **Account:** after the isolated comparisons, does the complete strategy still work with one shared $1,000 account, reservations, account restrictions and costs?

MFE/MAE and peak timing are post-entry diagnostics. They cannot be used to choose an entry using future information. An exit study needs the raw observation window beyond an early exit, and a full portfolio rerun because holding time changes later affordability.

## Run sequence

Register hypothesis → validate data/settings → baseline → bounded development comparisons → chronological validation → freeze one candidate or a preregistered comparison → final evaluation → frozen forward paper cohort if evidence warrants it.

Record failed runs, manual changes and all parameter/seed attempts. Specify the search subset and trial budget before searching. Do not expand the search because a final result disappointed. Fit learned transforms using training data only; purge overlapping outcome windows and declare boundary treatment. Simple rules still need chronological evaluation and complete trial accounting.

## Final holdout procedure

- Keep its dates/data partition separate from normal development APIs. Generic `run` rejects access.
- A final manifest records candidate code/config/data hashes, eligible universe, cost/account settings, all relevant trials, metrics/gates, uncertainty method, and the owner-approved evaluation scope.
- Verify the manifest and immutable hashes, log access, then evaluate the frozen selection. Do not use results to retune or select a replacement on the same final period.
- A correction to a confirmed software bug is versioned and disclosed with both outcomes. Once final observations influence selection, label them development evidence; obtain genuinely new final data for the revised selection.

## Promotion contract

Market runs cannot produce `pass` until all required gates are numeric and frozen. Until then, status is `unscored`; missing sample sufficiency yields `inconclusive` rather than pass/fail.

| Gate field | Required definition before scoring |
|---|---|
| minimum_evaluation_days / trades | Counts and treatment of dependent observations |
| net_return_requirement | Positive net result plus any predeclared target; deposits excluded |
| control_improvement_requirement | Which control and paired improvement measure |
| drawdown_ceiling | Maximum permitted drawdown and optional recovery-duration limit |
| uncertainty_rule | Confidence level, daily/block resampling design and acceptance criterion |
| cost_stress_rule | Additional cost/delay scenarios that must pass |
| concentration_rule | Limits/diagnostics for dependence on symbols, periods or a few winners |
| paper_observation_rule | Frozen forward duration/sample requirements and incident handling |

Report account return, marked equity/drawdown, net expectancy, win rate and average win/loss, profit factor, costs, turnover, exposure, sample size and period/symbol breakdowns. Define zero-trade/no-loss metric states explicitly. Preserve loss clusters in drawdown resampling; do not shuffle individual trades and call it a reliable account-risk distribution.

Software success is passing causal/accounting checks. Research success is a reproducible answer to the registered question. Strategy success requires sufficient positive evidence after costs on unseen periods within the agreed limits; finding no supported edge is a valid research result. None of these alone promises the $1M aspiration.

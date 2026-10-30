# M2 implementation status

Updated 2026-10-29, following the repairs on `fix/r3-replay-causality` from base `de33f33`.

The system now runs offline synthetic ORB/VWAP bar examples through ReplayRunner-backed source publication and bounded strategy views, a shared phase queue, owned reservations, fill-time sizing, protective exits, timers, marked equity and retained evidence. F01–F17 use the same phase queue and the validated account/sizing modules. The isolated trade adapter is hardened but remains outside account execution.

The earlier 557-test standalone implementation contained timing, cash, metric and evidence defects despite a green suite. The new regressions and their outcomes are recorded in [M2_REPAIR_RESULTS.md](M2_REPAIR_RESULTS.md). The complete module/test inventory, limitations and step-by-step Copilot plan are in [CURRENT_SYSTEM_AND_NEXT_STEPS.md](CURRENT_SYSTEM_AND_NEXT_STEPS.md).

| Milestone area | Current state | Remaining gate |
|---|---|---|
| M2-01 account/bar execution | Synthetic core repaired; real phase scheduling, ledger checks and F01–F17 executable | Consolidate remaining fixture/session adapter policy, complete declared account/corporate-action contracts; do not call the whole milestone complete from fixture counts alone |
| M2-01T | Isolated subsequent-trade eligibility/exit adapter tested | ReplayRunner/SeriesRouter → acceptance → cash/quantity checks → trade fills → ledger → evidence integration |
| M2-02 | ORB strategy and deterministic scanner ranking implemented | Calendar-aware historical RVOL, liquidity eligibility, historical universe and source evidence |
| M2-03 | Completed-bar VWAP state machine and synthetic session composition implemented | Verified data/features, broader session composition and the remaining full-engine acceptance matrix |
| M2-04 | Strict synthetic run, net metrics, guarded pair comparison and portable HTML report | Multi-session evaluation, complete loader holdout enforcement, richer reports and authorized final protocol |

`rsys replay` remains data-only; `rsys run` remains synthetic bar-only. Real provider adapters, historical research, optimizer/search, paper/live trading and final evaluation are not implemented. D09–D13/D16/D17 and provider-facing D22 remain open; no research choices were made by this repair.

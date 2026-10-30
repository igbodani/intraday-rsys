"""Summarize one executed session without invoking CLI or batch orchestration."""

from dataclasses import asdict

from rsys.research.evaluation import compute_metrics


def summarize_runner(runner, starting_equity):
    ending_equity = runner.portfolio.equity({})
    metrics = compute_metrics(
        starting_equity, ending_equity, runner.portfolio.realized_pnl, runner.fills
    )
    return {
        "starting_equity": starting_equity,
        "ending_equity": ending_equity,
        "decisions": [asdict(d) for d in runner.decisions],
        "fills": [asdict(f) for f in runner.fills],
        "metrics": asdict(metrics),
        "trace": runner.trace,
        "open_positions": {
            symbol: {
                "quantity": p.quantity,
                "entry": p.entry_price,
                "stop": p.stop,
                "target": p.target,
            }
            for symbol, p in runner.portfolio.positions.items()
        },
    }

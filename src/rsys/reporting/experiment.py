"""Honest scorecard presentation; existing metric meanings remain unchanged."""

import json
from decimal import Decimal
from html import escape


def scorecard_metrics(manifest, retained_metrics):
    metrics = manifest["metrics"]
    multi = retained_metrics["multi_session_metrics"]
    fraction = multi["drawdown"]["max_drawdown_pct"]

    def percent(value):
        return (
            value
            if isinstance(value, str) and not value[:1].isdigit()
            else str(Decimal(str(value)) * 100)
        )

    return {
        "net_change_currency": metrics["net_change"],
        "completed_trades": metrics["completed_trade_count"],
        "win_rate_fraction": metrics["win_rate"],
        "expectancy_currency_per_trade": metrics["expectancy"],
        "profit_factor_ratio": metrics["profit_factor"],
        "exposure_fraction": multi["exposure"]["fraction"],
        "session_end_drawdown_currency": multi["drawdown"]["max_drawdown"],
        "session_end_drawdown_fraction": fraction,
        "session_end_drawdown_percent": percent(fraction),
    }


def experiment_html(result):
    rows = []
    for row in result.get("arms", []):
        score = row.get("scorecard", {})
        rows.append(
            "<tr>"
            + "".join(
                "<td>" + escape(str(value)) + "</td>"
                for value in (
                    row["name"],
                    row["state"],
                    score.get("status", "unavailable"),
                    score.get("metrics", {}).get("net_change_currency", "unavailable"),
                    score.get("reason", ""),
                )
            )
            + "</tr>"
        )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Experiment</title>'
        "<h1>Experiment scorecard — unscored</h1><p>No automatic winner or promotion. "
        "Drawdown is measured at session ends, not the worst intraday decline. "
        "Exit changes rerun the whole account; they are not common-entry attribution.</p>"
        "<table><tr><th>Arm</th><th>Execution</th><th>Evidence</th><th>Net change (currency)</th><th>Reason</th></tr>"
        + "".join(rows)
        + "</table><pre>"
        + escape(json.dumps(result, indent=2, sort_keys=True, default=str))
        + "</pre></html>"
    )

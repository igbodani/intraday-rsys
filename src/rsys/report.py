"""Portable HTML report for a retained `rsys run` attempt (docs/M2_IMPLEMENTATION_HANDOFF.md
step 9, report subset). Reads manifest.json + trace.jsonl only; no external assets or
network call is required to open the rendered file.

Renders legacy synthetic bar runs and version-3 bar/trade session manifests, including
partial failures. Data-only `rsys replay` attempts use their own evidence format.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


class ReportError(Exception):
    """Missing or malformed manifest/trace for the requested attempt."""


def load_attempt(attempt_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path = attempt_dir / "manifest.json"
    trace_path = attempt_dir / "trace.jsonl"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReportError(f"cannot read manifest: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ReportError(f"manifest is not valid JSON: {exc}") from exc
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReportError(f"cannot read trace: {trace_path}") from exc
    try:
        records = [json.loads(line) for line in lines if line]
    except json.JSONDecodeError as exc:
        raise ReportError("trace is not valid JSONL") from exc
    if manifest.get("schema_version") != 3 and (
        manifest.get("status", "success") != "success" or "metrics" not in manifest
    ):
        raise ReportError("attempt did not complete successfully")
    return manifest, records


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _metrics_table(metrics: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in metrics.items()
    )
    return f"<table><caption>Metrics</caption><tbody>{rows}</tbody></table>"


def _decisions_table(records: list[dict[str, Any]]) -> str:
    by_time = {
        (r.get("session_date", ""), r["available_at_ms"]): r
        for r in records
        if r.get("kind") in ("decision", "accept")
    }
    decisions = [by_time[t] for t in sorted(by_time)]
    header = "<tr><th>session</th><th>time (ms)</th><th>candidates</th><th>proposed</th><th>accepted</th><th>rejected</th></tr>"
    rows = "".join(
        f"<tr><td>{_esc(d.get('session_date', ''))}</td><td>{_esc(d['available_at_ms'])}</td><td>{_esc(d['candidates'])}</td>"
        f"<td>{_esc(d['proposed'])}</td><td>{_esc(d['accepted'])}</td><td>{_esc(d['rejected'])}</td></tr>"
        for d in decisions
    )
    return f"<table><caption>Decisions ({len(decisions)})</caption><thead>{header}</thead><tbody>{rows}</tbody></table>"


def _fills_table(records: list[dict[str, Any]]) -> str:
    fills = [r for r in records if r.get("kind") == "fill"]
    header = "<tr><th>session</th><th>time (ms)</th><th>symbol</th><th>status</th><th>price</th><th>quantity</th><th>target</th><th>reference</th><th>fee</th><th>execution cost</th><th>reason</th></tr>"
    rows = "".join(
        f"<tr><td>{_esc(f.get('session_date', ''))}</td><td>{_esc(f['available_at_ms'])}</td><td>{_esc(f['symbol'])}</td><td>{_esc(f['status'])}</td>"
        f"<td>{_esc(f.get('price'))}</td><td>{_esc(f.get('quantity'))}</td><td>{_esc(f.get('target'))}</td>"
        f"<td>{_esc(f.get('reference_id'))}</td><td>{_esc(f.get('fee'))}</td><td>{_esc(f.get('execution_cost'))}</td><td>{_esc(f.get('reason'))}</td></tr>"
        for f in fills
    )
    return f"<table><caption>Fills ({len(fills)})</caption><thead>{header}</thead><tbody>{rows}</tbody></table>"


def render_report(manifest: dict[str, Any], records: list[dict[str, Any]]) -> str:
    metrics = manifest.get("metrics", {})
    trade_count = metrics.get("completed_trade_count", 0)
    status_label = (
        "no completed trades"
        if trade_count == 0
        else f"{trade_count} completed trade(s)"
    )
    if manifest.get("status", "success") != "success":
        status_label = f"{manifest['status']} — partial diagnostics only; {status_label}"
    sessions = manifest.get("sessions", [])
    sessions_table = ""
    if sessions:
        rows = "".join(
            f"<tr><td>{_esc(s['session_date'])}</td><td>{_esc(s['status'])}</td>"
            f"<td>{_esc(s.get('starting_equity', 'unavailable'))}</td><td>{_esc(s.get('ending_equity', 'unavailable'))}</td>"
            f"<td>{_esc(s.get('risk_state', {}).get('halt_reason'))}</td><td>{_esc(s.get('error', ''))}</td></tr>"
            for s in sessions
        )
        sessions_table = "<table><caption>Sessions</caption><thead><tr><th>date</th><th>status</th><th>starting equity</th><th>ending equity</th><th>entry halt</th><th>error</th></tr></thead><tbody>" + rows + "</tbody></table>"
    details = ""
    for key in ("error", "risk", "costs", "execution", "risk_state", "multi_session_metrics"):
        if key in manifest:
            details += f"<h2>{_esc(key)}</h2><pre>{_esc(json.dumps(manifest[key], indent=2, default=str))}</pre>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>rsys run {_esc(manifest["run_id"])}</title>
<style>
body {{ font-family: monospace; margin: 2rem; }}
table {{ border-collapse: collapse; margin-bottom: 2rem; }}
caption {{ font-weight: bold; text-align: left; margin-bottom: .25rem; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; }}
.summary span {{ margin-right: 1.5rem; }}
</style>
</head>
<body>
<h1>Synthetic account execution — run {_esc(manifest["run_id"])}</h1>
<p><em>Synthetic, offline evidence; not a market-performance claim. Status: {_esc(status_label)}.</em></p>
<p class="summary">
<span>Universe: {_esc(manifest.get("universe"))}</span>
<span>Starting equity: {_esc(manifest.get("starting_equity", "unavailable"))}</span>
<span>Ending equity: {_esc(manifest.get("ending_equity", "unavailable"))}</span>
</p>
<p>Metrics scope: {_esc(manifest.get("metrics_scope", "complete_attempt"))}. Multi-session drawdown uses session-end equity; exposure uses scheduled regular hours. Neither establishes complete market-data coverage.</p>
{_metrics_table(metrics)}
{sessions_table}
{_decisions_table(records)}
{_fills_table(records)}
{details}
</body>
</html>
"""


def render_report_for_attempt(attempt_dir: Path, out_path: Path | None = None) -> Path:
    manifest, records = load_attempt(attempt_dir)
    out_path = out_path or (attempt_dir / "report.html")
    out_path.write_text(render_report(manifest, records), encoding="utf-8")
    return out_path

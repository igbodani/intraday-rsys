"""Portable HTML report for a retained `rsys run` attempt (docs/M2_IMPLEMENTATION_HANDOFF.md
step 9, report subset). Reads manifest.json + trace.jsonl only; no external assets or
network call is required to open the rendered file.

Honesty note: renders only `run_cli.py`'s synthetic `StandaloneRunner` manifests/traces,
not `rsys replay` attempts -- see docs/M2_IMPLEMENTATION_STATUS.md.
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
    return manifest, [json.loads(line) for line in lines if line]


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _metrics_table(metrics: dict[str, Any]) -> str:
    rows = "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in metrics.items())
    return f"<table><caption>Metrics</caption><tbody>{rows}</tbody></table>"


def _decisions_table(records: list[dict[str, Any]]) -> str:
    decisions = [r for r in records if r.get("kind") == "decision"]
    header = "<tr><th>time (ms)</th><th>candidates</th><th>proposed</th><th>accepted</th><th>rejected</th></tr>"
    rows = "".join(
        f"<tr><td>{_esc(d['available_at_ms'])}</td><td>{_esc(d['candidates'])}</td>"
        f"<td>{_esc(d['proposed'])}</td><td>{_esc(d['accepted'])}</td><td>{_esc(d['rejected'])}</td></tr>"
        for d in decisions
    )
    return f"<table><caption>Decisions ({len(decisions)})</caption><thead>{header}</thead><tbody>{rows}</tbody></table>"


def _fills_table(records: list[dict[str, Any]]) -> str:
    fills = [r for r in records if r.get("kind") == "fill"]
    header = "<tr><th>time (ms)</th><th>symbol</th><th>status</th><th>price</th><th>quantity</th><th>target</th></tr>"
    rows = "".join(
        f"<tr><td>{_esc(f['available_at_ms'])}</td><td>{_esc(f['symbol'])}</td><td>{_esc(f['status'])}</td>"
        f"<td>{_esc(f.get('price'))}</td><td>{_esc(f.get('quantity'))}</td><td>{_esc(f.get('target'))}</td></tr>"
        for f in fills
    )
    return f"<table><caption>Fills ({len(fills)})</caption><thead>{header}</thead><tbody>{rows}</tbody></table>"


def render_report(manifest: dict[str, Any], records: list[dict[str, Any]]) -> str:
    trade_count = manifest["metrics"]["completed_trade_count"]
    status_label = "no completed trades" if trade_count == 0 else f"{trade_count} completed trade(s)"
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
<h1>Synthetic StandaloneRunner session -- run {_esc(manifest["run_id"])}</h1>
<p><em>Synthetic, offline evidence; not a market-performance claim. Status: {_esc(status_label)}.</em></p>
<p class="summary">
<span>Universe: {_esc(manifest.get("universe"))}</span>
<span>Starting equity: {_esc(manifest["starting_equity"])}</span>
<span>Ending equity: {_esc(manifest["ending_equity"])}</span>
</p>
{_metrics_table(manifest["metrics"])}
{_decisions_table(records)}
{_fills_table(records)}
</body>
</html>
"""


def render_report_for_attempt(attempt_dir: Path, out_path: Path | None = None) -> Path:
    manifest, records = load_attempt(attempt_dir)
    out_path = out_path or (attempt_dir / "report.html")
    out_path.write_text(render_report(manifest, records), encoding="utf-8")
    return out_path

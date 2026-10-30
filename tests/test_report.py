"""Portable HTML report tests (docs/M2_IMPLEMENTATION_HANDOFF.md step 9, report subset)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rsys.reporting.errors import ReportError
from rsys.reporting.html import load_attempt, render_report, render_report_for_attempt

_MANIFEST = {
    "run_id": "r-1", "starting_equity": "1000", "ending_equity": "1020",
    "universe": ["AAA"],
    "metrics": {
        "starting_equity": "1000", "ending_equity": "1020", "net_change": "20",
        "realized_pnl": "20", "completed_trade_count": 1, "win_count": 1, "loss_count": 0,
        "win_rate": "1", "expectancy": "20", "gross_profit": "20", "gross_loss": "0",
        "profit_factor": "NOT_DEFINED_NO_LOSSES",
    },
}
_RECORDS = [
    {"kind": "decision", "available_at_ms": 1000, "candidates": ["AAA"], "proposed": ["AAA"], "accepted": ["AAA"], "rejected": {}},
    {"kind": "fill", "available_at_ms": 2000, "symbol": "AAA", "status": "FILLED", "price": "100", "quantity": "5", "target": "110"},
    {"kind": "fill", "available_at_ms": 3000, "symbol": "AAA", "status": "EXIT", "price": "104", "quantity": "5"},
]


def test_render_report_contains_run_id_metrics_and_trace_rows():
    document = render_report(_MANIFEST, _RECORDS)
    assert "r-1" in document
    assert "NOT_DEFINED_NO_LOSSES" in document
    assert "FILLED" in document
    assert "EXIT" in document
    assert document.startswith("<!DOCTYPE html>")


def test_render_report_escapes_untrusted_values():
    manifest = dict(_MANIFEST)
    manifest["run_id"] = "<script>alert(1)</script>"
    document = render_report(manifest, _RECORDS)
    assert "<script>alert(1)</script>" not in document
    assert "&lt;script&gt;" in document


def test_render_report_for_attempt_writes_a_readable_self_contained_file(tmp_path: Path):
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    (attempt_dir / "manifest.json").write_text(json.dumps(_MANIFEST))
    with (attempt_dir / "trace.jsonl").open("w") as handle:
        for record in _RECORDS:
            handle.write(json.dumps(record) + "\n")

    report_path = render_report_for_attempt(attempt_dir)
    assert report_path == attempt_dir / "report.html"
    content = report_path.read_text(encoding="utf-8")
    assert "r-1" in content
    assert "http://" not in content and "https://" not in content  # no external assets/network needed


def test_load_attempt_raises_report_error_for_missing_manifest(tmp_path: Path):
    attempt_dir = tmp_path / "empty"
    attempt_dir.mkdir()
    with pytest.raises(ReportError):
        load_attempt(attempt_dir)


def test_zero_trades_is_labeled_not_silently_blank():
    manifest = dict(_MANIFEST)
    manifest["metrics"] = dict(_MANIFEST["metrics"])
    manifest["metrics"]["completed_trade_count"] = 0
    document = render_report(manifest, [])
    assert "no completed trades" in document

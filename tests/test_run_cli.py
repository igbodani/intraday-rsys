"""CLI subprocess tests for `rsys run` (docs/M2_IMPLEMENTATION_HANDOFF.md step 9, run subset).

Honesty note: `rsys run` drives only the synthetic `BarSessionRunner`, not the real
`ReplayRunner`/market data -- see docs/M2_IMPLEMENTATION_STATUS.md.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CONFIG = REPO_ROOT / "fixtures" / "standalone_run_fixture.json"


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "import sys; from rsys.cli import main; sys.exit(main(sys.argv[1:]))", *args],
        cwd=cwd, capture_output=True, text=True, check=False,
    )


def test_run_succeeds_and_retains_an_attempt(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    result = _run_cli("run", "--config", str(FIXTURE_CONFIG), "--out", str(out_dir), cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ending_equity: 9880" in result.stdout
    trace_path = next((out_dir / "attempts").glob("*/trace.jsonl"))
    manifest = json.loads(next((out_dir / "attempts").glob("*/manifest.json")).read_text())
    assert manifest["ending_equity"] == "9880"
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert any(r["kind"] == "fill" and r["status"] == "FILLED" for r in records)
    assert any(r["kind"] == "fill" and r["status"] == "EXIT" for r in records)


def test_run_with_report_flag_renders_a_portable_html_file(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    result = _run_cli("run", "--config", str(FIXTURE_CONFIG), "--out", str(out_dir), "--report", cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    report_path = next((out_dir / "attempts").glob("*/report.html"))
    assert "report:" in result.stdout
    assert "9880" in report_path.read_text(encoding="utf-8")



def test_run_rejects_a_malformed_config(tmp_path: Path) -> None:
    bad_config = tmp_path / "bad.json"
    bad_config.write_text("{not valid json")
    result = _run_cli("run", "--config", str(bad_config), "--out", str(tmp_path / "out"), cwd=REPO_ROOT)
    assert result.returncode == 2
    assert "not valid JSON" in result.stderr


def test_run_rejects_an_unknown_strategy_id(tmp_path: Path) -> None:
    config = json.loads(FIXTURE_CONFIG.read_text())
    config["symbols"]["AAA"]["strategy"] = "nonexistent_strategy_v1"
    bad_config = tmp_path / "bad_strategy.json"
    bad_config.write_text(json.dumps(config))
    result = _run_cli("run", "--config", str(bad_config), "--out", str(tmp_path / "out"), cwd=REPO_ROOT)
    assert result.returncode == 2
    assert "unknown strategy id" in result.stderr


def test_repeated_invocations_get_distinct_retained_attempts_with_identical_output(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    first = _run_cli("run", "--config", str(FIXTURE_CONFIG), "--out", str(out_dir), cwd=REPO_ROOT)
    second = _run_cli("run", "--config", str(FIXTURE_CONFIG), "--out", str(out_dir), cwd=REPO_ROOT)
    assert first.returncode == 0 and second.returncode == 0
    attempt_ids = [p.name for p in (out_dir / "attempts").iterdir() if p.is_dir()]
    assert len(attempt_ids) == 2
    traces = [
        (out_dir / "attempts" / attempt_id / "trace.jsonl").read_text() for attempt_id in attempt_ids
    ]
    assert traces[0] == traces[1]  # identical semantic output despite distinct run IDs

"""Persisted CLI evidence, compatibility gates, and meaningful negative controls."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

from rsys.compare_cli import CompareCliError, compare_run_pair
from rsys.evaluation import ComparisonError
from rsys.fixture_cli import run_fixture_cli
from rsys.fixture_engine import compare_case, run_case
from rsys.run_cli import RunConfigError, run_cli_entry
from rsys.standalone_run import IncompleteSessionError
from rsys.storage import RunStorage

ROOT = Path(__file__).parents[1]


def config():
    return json.loads((ROOT / "fixtures/standalone_run_fixture.json").read_text())


def run(tmp_path, value=None, *, report=False):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config() if value is None else value))
    return run_cli_entry(path, tmp_path / "out", report=report)


def record(root, run_id):
    store = RunStorage(root)
    try:
        return store.load_record(run_id)
    finally:
        store.close()


def test_input_hash_is_file_content_and_artifacts_are_sealed(tmp_path):
    _, identity, trace, manifest = run(tmp_path, report=True)
    data = json.loads(manifest.read_text())
    assert (
        data["data_hash"]
        == "sha256:"
        + hashlib.sha256((tmp_path / "config.json").read_bytes()).hexdigest()
    )
    saved = record(tmp_path / "out", identity)
    assert saved.status == "success"
    assert set(saved.artifacts) == {
        str(trace),
        str(manifest),
        str(trace.parent / "report.html"),
    }
    assert saved.metrics["realized_pnl"] == "-120"
    assert data["semantic_trace_hash"] == hashlib.sha256(trace.read_bytes()).hexdigest()
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    keys = [(r["available_at_ns"], r["phase"]) for r in rows]
    assert keys == sorted(keys)
    assert {r["phase"] for r in rows} >= {10, 20, 30, 40, 50, 60, 70}
    assert any(r.get("input_versions") for r in rows if r["kind"] == "decision")
    entry = next(r for r in rows if r["kind"] == "fill" and r["status"] == "FILLED")
    assert entry["available_at_ms"] == (9 * 60 + 46) * 60_000
    assert entry["account"]["reserved_cash"] == "0.00"
    assert data["strategies"]["AAA"]["config"]["range_minutes"] == 15


@pytest.mark.parametrize("bad", ["unreadable", "malformed", "schema", "incomplete"])
def test_failed_invocation_is_retained_with_partial_evidence(tmp_path, bad):
    path = tmp_path / "config.json"
    expected = RunConfigError
    if bad == "malformed":
        path.write_text("{not json")
    elif bad in ("schema", "incomplete"):
        c = config()
        if bad == "schema":
            c["schema_version"] = 999
        else:
            c["symbols"]["AAA"]["bars"] = c["symbols"]["AAA"]["bars"][:17]
            c["execution"]["flatten_at"] = "09:48:00"
            expected = IncompleteSessionError
        path.write_text(json.dumps(c))
    with pytest.raises(expected):
        run_cli_entry(path, tmp_path / "out")
    attempts = list((tmp_path / "out/attempts").iterdir())
    assert len(attempts) == 1
    saved = record(tmp_path / "out", attempts[0].name)
    assert saved.status == "failed" and saved.error
    assert len(saved.artifacts) == 2
    manifest = json.loads((attempts[0] / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    with pytest.raises(CompareCliError):
        compare_run_pair(tmp_path / "out", attempts[0].name, attempts[0].name)
    if bad == "incomplete":
        assert (attempts[0] / "trace.jsonl").stat().st_size > 0


@pytest.mark.parametrize(
    "change,expected",
    [
        ("cash", "initial_cash"),
        ("data", "market_data_hash"),
        ("scanner", "scanner_profile"),
        ("strategy", "strategies"),
    ],
)
def test_comparison_rejects_each_material_assumption(tmp_path, change, expected):
    _, a, _, _ = run(tmp_path)
    c = config()
    if change == "cash":
        c["initial_cash"] = "20000"
    elif change == "data":
        c["symbols"]["AAA"]["bars"][-1].update(
            open="91", high="91", low="91", close="91"
        )
    elif change == "scanner":
        c["top_n"] = 10
    else:
        c["symbols"]["AAA"]["strategy_config"] = {"target_r": "3"}
    _, b, _, _ = run(tmp_path, c)
    with pytest.raises(ComparisonError, match=expected):
        compare_run_pair(tmp_path / "out", a, b)


@pytest.mark.parametrize("artifact", ["manifest.json", "trace.jsonl"])
def test_comparison_checks_evidence_integrity(tmp_path, artifact):
    _, identity, trace, _ = run(tmp_path)
    p = trace.parent / artifact
    p.write_text(p.read_text() + " ")
    with pytest.raises(CompareCliError, match="integrity"):
        compare_run_pair(tmp_path / "out", identity, identity)


def test_fixture_artifacts_saved_before_finalization(tmp_path):
    outcomes, trace, manifest = run_fixture_cli("all", tmp_path)
    assert len(outcomes) == 17 and all(o.ok for o in outcomes)
    data = json.loads(manifest.read_text())
    saved = record(tmp_path, data["run_id"])
    assert saved.artifacts == [str(trace), str(manifest)]
    assert (
        data["data_hash"]
        == "sha256:"
        + hashlib.sha256((ROOT / "fixtures/cases.json").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "cid,key,value",
    [
        ("F01", "exit_price", "999"),
        ("F10", "expected_entry_time", "09:31:00"),
        ("F06", "expected_flatten_time", "12:56:00"),
        ("F15", "first_rejection", "WRONG"),
    ],
)
def test_fixture_oracle_negative_controls(cid, key, value):
    c = next(
        c
        for c in json.loads((ROOT / "fixtures/cases.json").read_text())["cases"]
        if c["id"] == cid
    )
    result = run_case(c)
    assert compare_case(c, result) == []
    changed = deepcopy(c)
    changed[key] = value
    assert any(key in problem for problem in compare_case(changed, result))


def test_fixture_split_preserves_marked_not_entry_equity():
    c = next(
        c
        for c in json.loads((ROOT / "fixtures/cases.json").read_text())["cases"]
        if c["id"] == "F17"
    )
    c["initial_position"]["target"] = None
    c["events"].insert(
        0,
        {"type": "bar_open", "time_local": "09:29:00", "symbol": "AAA", "open": "110"},
    )
    c["events"] = c["events"][:-1]  # no later mark to hide the split transition
    result = run_case(c)
    assert result.final_equity == D(1050)
    assert result.positions["AAA"]["entry"] == D(50)


def test_final_evaluate_is_truthfully_blocked():
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from rsys.cli import main; raise SystemExit(main())",
            "final-evaluate",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 5 and "blocked" in proc.stderr

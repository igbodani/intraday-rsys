"""BR-04 independent coverage arithmetic, real CLI and failure controls."""

import json
import os
import socket
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from test_alpaca_pipeline import cached

from rsys.application import preflight
from rsys.application.batch import SessionBatch
from rsys.application.historical import prepare_backtest
from rsys.cli import main
from rsys.data.alpaca import AlpacaBarsClient
from rsys.data.bar_cache import (
    BarCache,
    digest,
    encode,
    normalize_pages,
    request_identity,
)
from rsys.research.diagnostics import start_invocation
from rsys.research.inspection import inspect_attempt
from rsys.strategies.orb import OrbStrategy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config(tmp_path):
    return cached(tmp_path / "setup")[1]


def cache_files(config):
    return {
        str(p.relative_to(config["cache"])): p.read_bytes()
        for p in Path(config["cache"]).rglob("*")
        if p.is_file()
    }


def object_path(config, day):
    req = request_identity(
        config["dataset"]["symbol"],
        date.fromisoformat(day),
        config["dataset"]["feed"],
        config["dataset"]["asof"],
    )
    return (
        Path(config["cache"])
        / "objects"
        / digest(req)
        / (config["objects"][day] + ".json")
    )


def rewrite(config, day, mutate, *, normalize=False):
    original = object_path(config, day)
    payload = json.loads(original.read_bytes())
    mutate(payload)
    if normalize:
        payload["bars"], payload["quality"] = normalize_pages(
            payload["pages"], payload["request"]
        )
    config["objects"][day] = digest(payload)
    original.with_name(digest(payload) + ".json").write_bytes(encode(payload))


def check(config, tmp_path):
    result, manifest = preflight.check_data(config, tmp_path / "checks")
    assert json.loads(manifest.read_text())["status"] == result["status"]
    return result, manifest


def test_known_calendar_counts_inventory_no_network_strategy_or_cache_mutation(
    config, tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail(
            "preflight constructed a provider/strategy or simulated/acquired data"
        )

    before = cache_files(config)
    real_get = os.environ.get
    monkeypatch.setattr(
        os.environ,
        "get",
        lambda key, *args: (
            forbidden()
            if key.startswith(("APCA_", "ALPACA_"))
            else real_get(key, *args)
        ),
    )
    monkeypatch.setattr(AlpacaBarsClient, "__init__", forbidden)
    monkeypatch.setattr(BarCache, "acquire", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(OrbStrategy, "__init__", forbidden)
    monkeypatch.setattr(SessionBatch, "__init__", forbidden)
    result, manifest = check(config, tmp_path)
    second, _ = check(config, tmp_path)
    assert result == second
    assert result["can_execute"] and result["history_coverage_complete"]
    rows = result["datasets"]
    assert [
        (r["date"], r["role"], r["expected_minutes"], r["observed_minutes"])
        for r in rows
    ] == [
        ("2024-11-27", "warmup", 390, 390),
        ("2024-11-29", "both", 210, 210),
        ("2024-12-02", "execution", 390, 390),
    ]
    assert all(r["missing_intervals"] == [] and r["duplicate_rows"] == 0 for r in rows)
    assert result["inventory"]["verified_bars"] == 990
    assert result["inventory"]["verified_object_bytes"] == sum(
        object_path(config, d).stat().st_size for d in config["objects"]
    )
    prepared = prepare_backtest(config, [])
    assert result["inventory"]["prepared_input_json_bytes"] == len(encode(prepared))
    assert result["inventory"]["peak_memory_bytes"] is None
    assert result["inventory"]["backtest_evidence_bytes"] is None
    assert result["research_status"] == "unscored"
    assert any(
        "2024-12-02" in w and "insufficient comparable" in w for w in result["warnings"]
    )
    assert before == cache_files(config)
    assert not (tmp_path / "checks/runs.db").exists()
    status = inspect_attempt(tmp_path / "checks", manifest.parent.name)
    assert status.outcome == "success" and status.completeness == "complete"


@pytest.mark.parametrize(
    "day,can_execute",
    [("2024-11-27", True), ("2024-11-29", False), ("2024-12-02", False)],
)
def test_missing_minutes_vs_observed_zero_and_loader_agreement(
    config, tmp_path, day, can_execute
):
    def gap(payload):
        rows = payload["pages"][0]["bars"]["AAPL"]
        rows[0]["v"] = "0"
        del rows[1:3]

    rewrite(config, day, gap, normalize=True)
    result, _ = check(config, tmp_path)
    assert result["can_execute"] is can_execute
    row = next(r for r in result["datasets"] if r["date"] == day)
    assert row["zero_volume_minutes"] == 1
    assert row["observed_minutes"] == row["expected_minutes"] - 2
    assert row["missing_intervals"] == [
        {
            "start": f"{day}T14:31:00+00:00",
            "end_exclusive": f"{day}T14:33:00+00:00",
            "minutes": 2,
        }
    ]
    if can_execute:
        prepare_backtest(config, [])
        assert not result["history_coverage_complete"] and result["warnings"]
    else:
        with pytest.raises(ValueError, match="Incomplete execution-day"):
            prepare_backtest(config, [])


def test_empty_warmup_is_unavailable_not_verified_zero(config, tmp_path):
    rewrite(
        config,
        "2024-11-27",
        lambda p: p.update(pages=[{"bars": {"AAPL": []}}]),
        normalize=True,
    )
    result, _ = check(config, tmp_path)
    row = result["datasets"][0]
    assert result["can_execute"] and not result["history_coverage_complete"]
    assert row["observed_minutes"] == row["zero_volume_minutes"] == 0
    assert (
        row["coverage_status"] == "gaps"
        and row["missing_intervals"][0]["minutes"] == 390
    )


def test_permissions_survive_hard_exit_before_first_cache_read(config, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    out = tmp_path / "crashed"
    source = """
import os, sys
from rsys.application.preflight import check_data
from rsys.data.bar_cache import BarCache
BarCache.load = lambda *args: os._exit(71)
check_data(None, sys.argv[2], input_path=sys.argv[1])
"""
    result = subprocess.run(
        [sys.executable, "-c", source, str(path), str(out)], cwd=ROOT, check=False
    )
    assert result.returncode == 71
    assert not list(out.glob("preflights/*/preflight.json"))
    journal = next(out.glob("invocations/*/diagnostics.jsonl"))
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [
        r["details"]["date"]
        for r in records
        if r["event"] == "data_access_permitted" and "date" in r["details"]
    ] == ["2024-11-27", "2024-11-29", "2024-12-02"]
    status = inspect_attempt(out, journal.parent.name)
    assert status.outcome == "running" and status.owner_state == "dead"


@pytest.mark.parametrize(
    "denied", ["execution", "deep_warmup", "unknown", "unsupported_year"]
)
def test_authorization_happens_before_any_cache_inspection(
    config, tmp_path, monkeypatch, denied
):
    config["scanner"]["max_lookback_calendar_sessions"] = 3
    if denied == "execution":
        config["partitions"] = [
            {"name": "development", "start": "2024-01-01", "end": "2024-11-29"},
            {"name": "final", "start": "2024-12-02", "end": "2024-12-31"},
        ]
    elif denied == "deep_warmup":
        config["partitions"] = [
            {"name": "final", "start": "2024-01-01", "end": "2024-11-25"},
            {"name": "development", "start": "2024-11-26", "end": "2024-12-31"},
        ]
    elif denied == "unknown":
        config["partitions"] = [
            {"name": "development", "start": "2024-11-26", "end": "2024-12-31"}
        ]
    else:
        config["sessions"] = ["2030-01-02"]
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    cache_root = Path(config["cache"])
    for operation in ("stat", "resolve", "read_bytes", "open"):
        original = getattr(Path, operation)

        def guarded(self, *args, _original=original, **kwargs):
            assert not self.is_relative_to(cache_root), (
                "unauthorized cache filesystem inspection"
            )
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(Path, operation, guarded)
    monkeypatch.setattr(
        BarCache,
        "__init__",
        lambda *args: pytest.fail("cache constructed before authorization"),
    )
    assert (
        main(["check-data", "--config", str(path), "--out", str(tmp_path / "out")]) != 0
    )
    report = json.loads(
        next((tmp_path / "out/preflights").glob("*/preflight.json")).read_text()
    )
    assert not report["can_execute"]
    if denied != "unsupported_year":
        assert report["access_log"][-1]["allowed"] is False
        journal = next(
            (tmp_path / "out/invocations").glob("*/diagnostics.jsonl")
        ).read_text()
        assert '"data_access_denied"' in journal


@pytest.mark.parametrize(
    "mutation",
    [
        "bytes",
        "missing_object",
        "schema",
        "provider",
        "calendar",
        "request_feed",
        "request_asof",
        "request_adjustment",
        "timing",
        "quality",
        "subminute",
        "volume",
        "ohlc",
        "conflict",
        "wrong_date_pin",
        "symlink",
    ],
)
def test_exact_pins_and_integrity_fail_in_preflight_and_backtest(
    config, tmp_path, mutation
):
    day = (
        "2024-11-27"  # A missing warmup object is fatal, unlike a valid sparse object.
    )
    if mutation == "bytes":
        with object_path(config, day).open("ab") as stream:
            stream.write(b" ")
    elif mutation == "missing_object":
        object_path(config, day).unlink()
    elif mutation == "wrong_date_pin":
        config["objects"][day] = config["objects"]["2024-11-29"]
    elif mutation == "symlink":
        target = tmp_path / "outside.json"
        target.write_bytes(object_path(config, day).read_bytes())
        object_path(config, day).unlink()
        try:
            object_path(config, day).symlink_to(target)
        except OSError:
            pytest.skip("symlink creation unavailable on this host")
    else:

        def corrupt(p):
            if mutation == "schema":
                p["schema_version"] = True
            elif mutation == "provider":
                p["provider"] = "other"
            elif mutation == "calendar":
                p["calendar_version"] = "other"
            elif mutation.startswith("request_"):
                p["request"][mutation.removeprefix("request_")] = {
                    "request_feed": "iex",
                    "request_asof": "2024-11-27",
                    "request_adjustment": "all",
                }[mutation]
            elif mutation == "timing":
                p["timing_basis"] = "recorded"
            elif mutation == "quality":
                p["quality"]["duplicate_rows"] += 1
            else:
                rows = p["pages"][0]["bars"]["AAPL"]
                if mutation == "subminute":
                    rows[0]["t"] = f"{day}T14:30:00.000000001Z"
                elif mutation == "volume":
                    rows[0]["v"] = "-1"
                elif mutation == "ohlc":
                    rows[0]["l"] = "10000"
                elif mutation == "conflict":
                    rows.append({**rows[0], "v": "9999"})

        rewrite(config, day, corrupt)
    result, _ = check(config, tmp_path)
    assert result["can_execute"] is False and result["errors"]
    assert result["datasets"][0]["coverage_status"] == "unverified"
    assert result["datasets"][0]["observed_minutes"] is None
    with pytest.raises((OSError, ValueError)):
        prepare_backtest(config, [])


def test_numeric_duplicate_counted_once_and_request_alias_never_read(config, tmp_path):
    def duplicate(p):
        rows = p["pages"][0]["bars"]["AAPL"]
        rows.append({**rows[0], "v": "50.000"})

    rewrite(config, "2024-11-27", duplicate, normalize=True)
    for alias in (Path(config["cache"]) / "requests").glob("*"):
        alias.write_text("not json")
    result, _ = check(config, tmp_path)
    assert result["can_execute"]
    assert result["datasets"][0]["duplicate_rows"] == 1
    assert result["datasets"][0]["observed_minutes"] == 390


@pytest.mark.parametrize(
    "field",
    ["risk", "costs", "strategy", "scanner", "flatten", "action", "sessions", "pins"],
)
def test_configuration_validation_agrees(config, tmp_path, field):
    if field == "risk":
        config["account"]["risk"]["per_trade_fraction"] = "2"
    elif field == "costs":
        config["account"]["costs"]["entry_fee"] = "-1"
    elif field == "strategy":
        config["strategy"]["strategy"] = "unknown"
    elif field == "scanner":
        config["scanner"]["lookback_sessions"] = 2
    elif field == "flatten":
        config["account"]["flatten_at"] = "16:00:00"
    elif field == "action":
        config["action_review"]["start"] = "2024-11-29"
    elif field == "sessions":
        config["sessions"].reverse()
    else:
        config["objects"].pop("2024-11-27")
    with pytest.raises((ValueError, TypeError)):
        prepare_backtest(config, [])
    with pytest.raises((ValueError, TypeError)):
        check(config, tmp_path)
    report = json.loads(
        next((tmp_path / "checks/preflights").glob("*/preflight.json")).read_text()
    )
    assert not report["can_execute"] and report["status"] == "failed"


def test_real_cli_success_failure_and_status(config, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    command = [
        sys.executable,
        "-m",
        "rsys.cli",
        "check-data",
        "--config",
        str(path),
        "--out",
        str(tmp_path / "out"),
    ]
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "can_execute: true" in result.stdout and "report:" in result.stdout
    manifest = next((tmp_path / "out/preflights").glob("*/manifest.json"))
    status = subprocess.run(
        [
            sys.executable,
            "-m",
            "rsys.cli",
            "status",
            "--storage-root",
            str(tmp_path / "out"),
            "--id",
            manifest.parent.name,
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["outcome"] == "success"
    object_path(config, "2024-12-02").unlink()
    failed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert failed.returncode == 3 and "can_execute: false" in failed.stdout


def test_bad_json_retained_and_html_escaped(config, tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b'{"api_key":"private-value",')
    assert (
        main(["check-data", "--config", str(path), "--out", str(tmp_path / "bad")]) != 0
    )
    report = next((tmp_path / "bad/preflights").glob("*/preflight.json")).read_text()
    assert "private-value" not in report
    config["action_review"]["source"] = "<script>alert(1)</script>"
    _, manifest = check(config, tmp_path)
    html = (manifest.parent / "report.html").read_text()
    assert "<script>" not in html and "&lt;script&gt;" in html
    (manifest.parent / "report.html").write_text("tampered")
    with pytest.raises(ValueError, match="artifact integrity"):
        inspect_attempt(tmp_path / "checks", manifest.parent.name)


@pytest.mark.parametrize(
    "target", ["preflight.json", "first_manifest", "final_manifest", "report.html"]
)
def test_required_write_failure_never_commits_success(
    config, tmp_path, monkeypatch, target
):
    real_json = preflight.atomic_write_json
    real_text = Path.write_text
    calls = 0

    def fail_json(path, value):
        nonlocal calls
        if path.name == "manifest.json":
            calls += 1
        if (
            path.name == target
            or target == "first_manifest"
            and calls == 1
            and path.name == "manifest.json"
            or target == "final_manifest"
            and calls == 2
            and path.name == "manifest.json"
        ):
            raise OSError("injected disk failure")
        return real_json(path, value)

    def fail_text(path, *args, **kwargs):
        if path.name == target:
            raise OSError("injected disk failure")
        return real_text(path, *args, **kwargs)

    monkeypatch.setattr(preflight, "atomic_write_json", fail_json)
    monkeypatch.setattr(Path, "write_text", fail_text)
    with pytest.raises(OSError, match="injected") as error:
        check(config, tmp_path)
    manifest = error.value.manifest_path
    assert (
        inspect_attempt(tmp_path / "checks", manifest.parent.name).outcome == "failed"
    )
    assert json.loads(manifest.read_text())["status"] == "failed"


def test_primary_error_survives_secondary_write_error(config, tmp_path, monkeypatch):
    config["account"]["initial_cash"] = "-1"
    monkeypatch.setattr(
        preflight,
        "atomic_write_json",
        lambda *args: (_ for _ in ()).throw(OSError("secondary")),
    )
    with pytest.raises(ValueError) as error:
        check(config, tmp_path)
    assert "secondary" not in str(error.value)
    assert isinstance(error.value.diagnostic_write_error, OSError)


def test_attempt_collision_does_not_overwrite(config, tmp_path):
    invocation = start_invocation(tmp_path, command="check-data")
    attempt = tmp_path / "preflights" / invocation.invocation_id
    attempt.mkdir(parents=True)
    (attempt / "manifest.json").write_text("existing")
    with pytest.raises(FileExistsError):
        preflight.check_data(config, tmp_path, invocation=invocation)
    assert (attempt / "manifest.json").read_text() == "existing"


def test_equivalent_cache_paths_keep_same_resolved_comparison_provenance(
    config, tmp_path
):
    # Canonicalization must move after authorization, not disappear. Previously
    # equivalent path spellings would otherwise become a protected source change.
    first = prepare_backtest(config, [])
    root = Path(config["cache"])
    config["cache"] = str(root / ".." / root.name)
    second = prepare_backtest(config, [])
    assert first == second
    result, _ = check(config, tmp_path)
    assert result["inventory"]["prepared_input_json_bytes"] == len(encode(first))


def test_benchmark_inventory_counts_actual_bytes_and_known_workloads(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "benchmark", ROOT / "scripts/benchmark_backtest_evidence.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "a").write_bytes(b"1234")
    (tmp_path / "b").write_bytes(b"567")
    assert sum(module.file_inventory(tmp_path).values()) == 7
    for name, bars, objects in [("early-close", 600, 2), ("multi-session", 1770, 5)]:
        config = module.workload_config(tmp_path / name, name)
        result, _ = preflight.check_data(config, tmp_path / name / "out")
        assert result["can_execute"]
        assert result["inventory"]["verified_bars"] == bars
        assert result["inventory"]["verified_objects"] == objects
    memory, basis = module.peak_memory()
    assert memory is None or memory > 0
    assert basis

"""History -> public replay -> ranks/fills -> sealed evidence, with independent oracles."""

import hashlib
import json
import runpy
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

from rsys.application.batch import SessionBatch, run_batch_cli
from rsys.application.batch_config import validate_batch
from rsys.application.errors import RunConfigError
from rsys.research.comparison import compare_run_pair
from rsys.research.errors import CompareCliError, ComparisonError

ROOT = Path(__file__).resolve().parents[1]
build_fixture = runpy.run_path(str(ROOT / "scripts/make_history_scanner_fixture.py"))[
    "build_fixture"
]


def run(config):
    batch = SessionBatch(config)
    batch.run()
    return batch


def decisions(batch, session=0):
    return batch.runners[session].decisions


def retained(config, path):
    _, run_id, trace, manifest = run_batch_cli(
        config, json.dumps(config).encode(), path, report=True
    )
    return run_id, trace.parent, json.loads(manifest.read_text())


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_public_history_cli_runs_two_sessions_and_seals_reconstructable_evidence(
    tmp_path, mode
):
    config_path = tmp_path / "input.json"
    generated = subprocess.run(
        [
            sys.executable,
            "scripts/make_history_scanner_fixture.py",
            "--mode",
            mode,
            "--out",
            str(config_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rsys.cli",
            "run",
            "--config",
            str(config_path),
            "--out",
            str(tmp_path / "runs"),
            "--report",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    (attempt,) = (tmp_path / "runs" / "attempts").iterdir()
    manifest = json.loads((attempt / "manifest.json").read_text())
    assert manifest["status"] == "success"
    assert manifest["scanner_basis"] == "synthetic_minute_history_v1"
    assert manifest["metrics"]["completed_trade_count"] == 2
    assert {f["symbol"] for f in manifest["fills"]} == {"AAA"}
    assert (
        manifest["sessions"][1]["starting_equity"]
        == manifest["sessions"][0]["ending_equity"]
    )
    assert D(manifest["ending_equity"]) == D(
        "1022.34800" if mode == "bar" else "1034.14420"
    )
    assert json.loads((attempt / "input.json").read_text()) == json.loads(
        config_path.read_text()
    )
    snapshots = json.loads((attempt / "scanner_history.json").read_text())
    for identity, payload in snapshots.items():
        assert (
            hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            == identity
        )
    first = manifest["decisions"][0]
    evidence = first["scanner_evidence"]
    assert first["ranked"] == ["AAA", "BBB"]
    assert first["candidates"] == ["AAA"]
    assert evidence["symbols"]["BBB"]["selection"] == "OUTSIDE_TOP_N"
    a = evidence["symbols"]["AAA"]
    assert a["elapsed_minutes"] == 1
    assert D(a["current_volume"]) == 100
    # Independent arithmetic: 100 / 50 = 2; previous full day = 390 * 50.
    assert D(a["rvol"]["value"]) == 2
    assert D(a["rvol"]["mean_volume"]) == 50
    assert a["rvol"]["selected_dates"] == ["2024-11-27"]
    assert D(a["previous_session"]["volume"]) == 19500
    assert D(a["gain"]["value"]) == 0
    assert D(evidence["symbols"]["BBB"]["rvol"]["value"]) == D("0.5")
    previous = snapshots[a["previous_session"]["snapshot_id"]]
    assert len(previous["selected_revisions"]) == 390
    assert previous["selected_revisions"][0]["source"] == "synthetic-history-demo"
    for decision in manifest["decisions"]:
        ev = decision["scanner_evidence"]
        encoded = json.dumps(
            ev["ranking"], sort_keys=True, separators=(",", ":")
        ).encode()
        assert hashlib.sha256(encoded).hexdigest() == ev["ranking_digest"]
        assert ev["current_input_versions"]
        for detail in ev["symbols"].values():
            for row in detail.get("history_sessions", []):
                assert row["snapshot_id"] in snapshots
    for session in manifest["sessions"]:
        assert (attempt / "sessions" / f"{session['session_date']}.json").exists()
        assert (
            session["decisions"][0]["scanner_evidence"]["symbols"]["AAA"][
                "current_volume"
            ]
            == "100"
        )
    report = (attempt / "report.html").read_text()
    assert "History scanner decisions" in report and "OUTSIDE_TOP_N" in report
    assert (
        compare_run_pair(tmp_path / "runs", manifest["run_id"], manifest["run_id"])[
            "ending_equity_delta"
        ]
        == "0.00000"
    )


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_entire_prefix_is_unchanged_by_future_inputs_with_trade_changing_negative_control(
    mode,
):
    config = build_fixture(mode)
    baseline = run(config)
    cutoff = 9 * 3_600_000 + 47 * 60_000 + 30_000
    prefix = lambda batch: [
        r
        for r in batch.trace
        if r["session_date"] == "2024-11-29" and r["available_at_ms"] <= cutoff
    ]
    assert any(
        r["kind"] == "fill" and r["status"] == "FILLED" for r in prefix(baseline)
    )
    future = deepcopy(config)
    row = deepcopy(future["scanner"]["history"]["AAA"][0])
    row.update(revision=1, volume="999999", available_at="2024-11-29T15:17:01+00:00")
    future["scanner"]["history"]["AAA"].append(row)
    # Current/future session history must not enter earlier denominator/eligibility.
    future["scanner"]["history"]["AAA"][-2]["volume"] = "999999"
    for spec in future["sessions"][1]["symbols"].values():
        for source in spec["bars" if mode == "bar" else "trades"]:
            source["volume" if mode == "bar" else "size"] = "700"
    for spec in future["sessions"][0]["symbols"].values():
        spec["bars" if mode == "bar" else "trades"][-1][
            "volume" if mode == "bar" else "size"
        ] = "900"
    assert prefix(run(future)) == prefix(baseline)
    past = deepcopy(config)
    for row in past["scanner"]["history"]["AAA"]:
        if row["session_date"] == "2024-11-27":
            row["volume"] = "400"
    changed = run(past)
    assert prefix(changed) != prefix(baseline)
    assert changed.runners[0].fills[0].symbol == "BBB"
    assert decisions(changed)[0].ranked == ["BBB", "AAA"]


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_later_history_revision_changes_only_later_rankings_and_preserves_exits(mode):
    config = build_fixture(mode)
    for original in list(config["scanner"]["history"]["AAA"]):
        if original["session_date"] == "2024-11-27":
            row = {
                **original,
                "volume": "400",
                "revision": 1,
                "available_at": "2024-11-29T14:47:00+00:00",
            }
            config["scanner"]["history"]["AAA"].append(row)
    batch = run(config)
    before = [d for d in decisions(batch) if d.available_at_ms < 35_220_000]
    after = [d for d in decisions(batch) if d.available_at_ms >= 35_220_000]
    assert before and after
    assert before[-1].ranked == ["AAA", "BBB"]
    assert after[0].ranked == ["BBB", "AAA"]
    assert [f.symbol for f in batch.runners[0].fills] == ["AAA", "AAA"]
    assert batch.runners[0].fills[-1].status != "FILLED"


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_missing_history_and_verified_zero_keep_distinct_statuses(mode):
    config = build_fixture(mode)
    config["scanner"]["history"]["AAA"] = []
    for row in config["scanner"]["history"]["BBB"]:
        if row["minute_index"] < 30:
            row.update(status="NO_TRADE", close=None, volume="0")
    batch = run(config)
    evidence = decisions(batch)[0].scanner_evidence["symbols"]
    assert evidence["AAA"]["rvol"]["status"] == "MISSING_SELECTED_HISTORY"
    assert evidence["AAA"]["rvol"]["mean_volume"] is None
    assert evidence["BBB"]["rvol"]["status"] == "ZERO_DENOMINATOR"
    assert evidence["BBB"]["rvol"]["mean_volume"] == 0
    assert decisions(batch)[0].candidates == []
    assert not batch.runners[0].fills


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_delayed_input_uses_completed_interval_not_delivery_minute(mode):
    config = build_fixture(mode)
    config["costs"]["feed_delay_ms"] = 61_000
    config["scanner"]["max_current_bar_age_ms"] = 120_000
    # No positions needed: this fixture isolates the scanner's clock under long delay.
    config["scanner"]["min_dollar_volume"] = "999999999"
    batch = run(config)
    first = next(
        d
        for d in decisions(batch)
        if d.scanner_evidence["symbols"]["AAA"].get("current_volume") is not None
    )
    a = first.scanner_evidence["symbols"]["AAA"]
    assert a["elapsed_minutes"] == 1
    assert a["current_volume"] == 100
    assert a["rvol"]["mean_volume"] == 50
    assert (
        first.available_at_ms == 34_321_000
        if mode == "bar"
        else first.available_at_ms == 34_320_000
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("feed", "sip"),
        ("unit_basis", "split_adjusted"),
        ("corporate_actions", "splits"),
        ("mode", "alpaca"),
        ("lookback_sessions", 0),
        ("max_lookback_calendar_sessions", 0),
        ("min_dollar_volume", "0"),
        ("max_current_bar_age_ms", -1),
        ("history", []),
    ],
)
def test_unsupported_or_implicit_history_policies_fail_closed(field, value):
    config = build_fixture()
    config["scanner"][field] = value
    with pytest.raises(RunConfigError):
        validate_batch(config)


def test_mixed_scanner_modes_and_naive_history_timestamps_are_rejected():
    config = build_fixture()
    config["sessions"][0]["scanner_inputs"] = {}
    with pytest.raises(RunConfigError, match="unsupported fields"):
        validate_batch(config)
    del config["sessions"][0]["scanner_inputs"]
    config["scanner"]["history"]["AAA"][0]["available_at"] = "2024-11-27T09:31:00"
    with pytest.raises(RunConfigError, match="timezone"):
        validate_batch(config)


def test_history_artifact_is_sealed_and_different_policies_cannot_compare(tmp_path):
    config = build_fixture()
    first, path, _ = retained(config, tmp_path)
    config["scanner"]["min_dollar_volume"] = "1001"
    second, _, _ = retained(config, tmp_path)
    with pytest.raises(ComparisonError, match="scanner"):
        compare_run_pair(tmp_path, first, second)
    (path / "scanner_history.json").write_text("{}")
    with pytest.raises(CompareCliError, match="artifact integrity mismatch"):
        compare_run_pair(tmp_path, first, first)


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_a_missing_current_minute_is_not_counted_as_zero_volume(mode):
    config = build_fixture(mode)
    for session in config["sessions"]:
        del session["symbols"]["BBB"]
        spec = session["symbols"]["AAA"]
        key = "bars" if mode == "bar" else "trades"
        timestamp = "bar_start" if mode == "bar" else "event_time"
        spec[key] = [r for r in spec[key] if "T09:31:" not in r[timestamp]]
    batch = run(config)
    first, *later = decisions(batch)
    assert first.scanner_evidence["symbols"]["AAA"]["current_volume"] == 100
    gap = [
        d
        for d in later
        if d.scanner_evidence["symbols"]["AAA"]["current_status"]
        == "INCOMPLETE_CURRENT_MINUTES"
    ]
    assert gap
    assert all(d.candidates == [] for d in gap)
    assert all(
        "current_volume" not in d.scanner_evidence["symbols"]["AAA"] for d in gap
    )
    assert not batch.runners[0].fills


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_history_first_publication_becomes_visible_at_its_declared_cutoff(mode):
    config = build_fixture(mode)
    config["scanner"]["history"]["AAA"][0]["available_at"] = "2024-11-29T14:33:00+00:00"
    batch = run(config)
    first = decisions(batch)[0].scanner_evidence["symbols"]["AAA"]
    assert first["rvol"]["status"] == "MISSING_SELECTED_HISTORY"
    assert first["previous_session"]["exclusion_reason"] == "MISSING_MINUTE"
    later = next(d for d in decisions(batch) if d.available_at_ms >= 34_380_000)
    detail = later.scanner_evidence["symbols"]["AAA"]
    assert detail["rvol"]["status"] is None
    assert detail["rvol"]["value"] == 2


def test_trade_correction_replaces_current_volume_without_double_counting():
    config = build_fixture("trade")
    spec = config["sessions"][0]["symbols"]["AAA"]
    spec["corrections"] = [
        {
            "original_trade_id": spec["trades"][0]["trade_id"],
            "available_at": "2024-11-29T09:32:30",
            "action": "correct",
            "corrected_price": "100",
            "corrected_size": "225",
        }
    ]
    batch = run(config)
    by_time = {
        d.available_at_ms: d.scanner_evidence["symbols"]["AAA"]
        for d in decisions(batch)
    }
    assert by_time[34_260_000]["current_volume"] == 100  # 09:31, original first minute
    assert (
        by_time[34_320_000]["current_volume"] == 200
    )  # 09:32, still before correction
    # First minute: one 25-share print replaced by 225, other three prints = 75.
    assert by_time[34_380_000]["current_volume"] == 500  # (225 + 75) + 100 + 100
    assert by_time[34_380_000]["rvol"]["value"] == D(500) / D(150)


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_runtime_history_failure_retains_partial_attempt_and_prior_sessions(
    tmp_path, mode
):
    config = build_fixture(mode)
    # Two contradictory highest revisions become visible during the second session.
    original = next(
        r
        for r in config["scanner"]["history"]["AAA"]
        if r["session_date"] == "2024-11-29"
    )
    for volume in ("400", "500"):
        config["scanner"]["history"]["AAA"].append(
            {
                **original,
                "revision": 1,
                "volume": volume,
                "available_at": "2024-12-02T14:47:30+00:00",
            }
        )
    config["sessions"].append(
        {**deepcopy(config["sessions"][-1]), "date": "2024-12-03"}
    )
    final = config["sessions"][-1]
    # Shift the copied market timestamps to keep this a structurally valid later session.
    for spec in final["symbols"].values():
        for key in ("bars", "trades", "corrections"):
            for row in spec.get(key, []):
                for name in ("bar_start", "bar_end", "event_time", "available_at"):
                    if name in row:
                        row[name] = row[name].replace("2024-12-02", "2024-12-03")
    with pytest.raises(ValueError, match="ambiguous minute revision"):
        retained(config, tmp_path)
    (attempt,) = (tmp_path / "attempts").iterdir()
    manifest = json.loads((attempt / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert [s["status"] for s in manifest["sessions"]] == [
        "success",
        "failed",
        "not_started",
    ]
    assert manifest["sessions"][0]["metrics"]["completed_trade_count"] == 1
    assert any(f["status"] == "FILLED" for f in manifest["sessions"][1]["fills"])
    assert json.loads((attempt / "input.json").read_text()) == config
    assert json.loads((attempt / "scanner_history.json").read_text())
    assert "partial diagnostics only" in (attempt / "report.html").read_text()
    with pytest.raises(CompareCliError, match="successful"):
        compare_run_pair(tmp_path, manifest["run_id"], manifest["run_id"])


def test_supplied_and_history_modes_cannot_be_silently_compared(tmp_path):
    history, _, _ = retained(build_fixture(), tmp_path)
    supplied_config = json.loads(
        (ROOT / "fixtures/sessions_bar_fixture.json").read_text()
    )
    supplied_config["scanner"] = {"mode": "supplied"}
    supplied, _, manifest = retained(supplied_config, tmp_path)
    assert manifest["scanner_basis"] == "supplied_synthetic_scores"
    with pytest.raises(ComparisonError):
        compare_run_pair(
            tmp_path, history, supplied, permitted_difference="scanner_profile"
        )


def test_history_report_escapes_symbols_and_does_not_emit_raw_markup(tmp_path):
    config = build_fixture()
    symbol = '<img src=x onerror="alert(1)">'
    config["scanner"]["history"][symbol] = config["scanner"]["history"].pop("AAA")
    for session in config["sessions"]:
        session["symbols"][symbol] = session["symbols"].pop("AAA")
    _, attempt, _ = retained(config, tmp_path)
    report = (attempt / "report.html").read_text()
    assert symbol not in report
    assert "&lt;img src=x onerror=" in report


def test_invalid_history_cli_has_nonzero_exit_and_retains_the_attempt(tmp_path):
    config = build_fixture()
    config["scanner"]["feed"] = "sip"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(config))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rsys.cli",
            "run",
            "--config",
            str(path),
            "--out",
            str(tmp_path / "runs"),
            "--report",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    (attempt,) = (tmp_path / "runs" / "attempts").iterdir()
    manifest = json.loads((attempt / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["error"]["type"] == "RunConfigError"
    assert manifest["scanner_basis"] == "unresolved_invalid_config"
    assert json.loads((attempt / "input.json").read_text()) == config


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_stale_symbol_leaves_ranking_and_cannot_block_an_eligible_stock(mode):
    config = build_fixture(mode)
    config["sessions"] = config["sessions"][:1]
    spec = config["sessions"][0]["symbols"]["AAA"]
    key = "bars" if mode == "bar" else "trades"
    timestamp = "bar_start" if mode == "bar" else "event_time"
    spec[key] = [r for r in spec[key] if "T09:30:" in r[timestamp]]
    batch = run(config)
    assert decisions(batch)[0].candidates == ["AAA"]
    last = decisions(batch)[-1]
    assert (
        last.scanner_evidence["symbols"]["AAA"]["current_status"] == "STALE_CURRENT_BAR"
    )
    assert last.scanner_evidence["symbols"]["AAA"]["current_bar_age_ms"] > 60_000
    assert last.candidates == ["BBB"]
    assert {fill.symbol for fill in batch.runners[0].fills} == {"BBB"}
    assert len(batch.runners[0].fills) == 2

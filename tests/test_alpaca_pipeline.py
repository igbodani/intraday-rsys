"""Credential-free provider fakes exercise acquisition, pinned cache and actual offline runs."""

import json
import subprocess
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError

import pytest

from rsys.application.batch import SessionBatch
from rsys.application.historical import backtest, download, prepare_backtest
from rsys.data.alpaca import AlpacaBarsClient
from rsys.data.bar_cache import BarCache, request_identity
from rsys.data.calendar import session_duration_minutes, session_open_utc
from rsys.research.comparison import compare_run_pair

ROOT = Path(__file__).resolve().parents[1]


def inputs(tmp_path):
    base = json.loads((ROOT / "fixtures/sessions_bar_fixture.json").read_text())
    acquisition = {
        "schema_version": 1,
        "cache": str(tmp_path / "cache"),
        "dataset": {"symbol": "AAPL", "feed": "sip", "asof": "-"},
        "start": "2024-11-27",
        "end": "2024-12-02",
        "purpose": "development",
        "partitions": [
            {"name": "development", "start": "2024-01-01", "end": "2024-12-02"},
            {"name": "final", "start": "2024-12-03", "end": "2024-12-31"},
        ],
    }
    simulation = {
        k: deepcopy(acquisition[k])
        for k in ("schema_version", "cache", "dataset", "purpose", "partitions")
    }
    simulation.update(
        objects={},
        sessions=["2024-11-29", "2024-12-02"],
        timing_basis="final_bars_modeled_at_end",
        account={
            k: deepcopy(base[k])
            for k in (
                "initial_cash",
                "sizing",
                "risk",
                "costs",
                "execution",
                "completion_policy",
            )
        },
        strategy={"strategy": "orb_close_cross_v1", "strategy_config": {}},
        scanner={
            "top_n": 1,
            "lookback_sessions": 1,
            "max_lookback_calendar_sessions": 1,
            "min_dollar_volume": "1000",
            "max_current_bar_age_ms": 60000,
        },
        action_review={
            "status": "verified_none",
            "source": "synthetic test fixture only",
            "start": "2024-11-27",
            "end": "2024-12-02",
        },
    )
    simulation["account"]["flatten_at"] = "09:48:05"
    return acquisition, simulation


def response(params):
    day = datetime.fromisoformat(params["start"]).date()
    base = json.loads((ROOT / "fixtures/sessions_bar_fixture.json").read_text())
    source = next(
        (
            s["symbols"]["AAA"]["bars"]
            for s in base["sessions"]
            if s["date"] == str(day)
        ),
        [],
    )
    rows = []
    for minute in range(session_duration_minutes(day)):
        row = {
            "t": (session_open_utc(day) + timedelta(minutes=minute)).isoformat(),
            "o": "100",
            "h": "100",
            "l": "100",
            "c": "100",
            "v": "50" if str(day) == "2024-11-27" else "100",
        }
        if minute < len(source):
            row.update(
                {
                    k: source[minute][v]
                    for k, v in (
                        ("o", "open"),
                        ("h", "high"),
                        ("l", "low"),
                        ("c", "close"),
                        ("v", "volume"),
                    )
                }
            )
        rows.append(row)
    if params.get("page_token"):
        return {"bars": {"AAPL": rows[100:]}, "next_page_token": None}
    return {"bars": {"AAPL": rows[:100]}, "next_page_token": "next"}


def cached(tmp_path):
    acquisition, simulation = inputs(tmp_path)
    result, _ = download(
        acquisition, tmp_path / "acquire", client=AlpacaBarsClient(transport=response)
    )
    simulation["objects"] = {
        item["date"]: item["object_id"] for item in result["datasets"]
    }
    return acquisition, simulation


def test_pagination_cache_reuse_and_actual_offline_backtest(tmp_path, monkeypatch):
    acquisition, config = cached(tmp_path)

    def denied(*args, **kwargs):
        pytest.fail("Network requested during cache reuse/backtest")

    monkeypatch.setattr(AlpacaBarsClient, "_request", denied)
    repeat, _ = download(acquisition, tmp_path / "acquire")
    assert {r["date"]: r["object_id"] for r in repeat["datasets"]} == config["objects"]
    result, first, trace, manifest = backtest(
        config, json.dumps(config).encode(), tmp_path / "runs"
    )
    data = json.loads(manifest.read_text())
    assert data["status"] == "success"
    assert data["evidence_type"] == "historical_bar_estimate"
    assert data["scanner_basis"] == "historical_final_bar_features_v1"
    assert Decimal(data["ending_equity"]) == Decimal("1022.34800")
    assert data["metrics"]["completed_trade_count"] == 2
    assert data["historical_source"]["dataset_hashes"] == config["objects"]
    assert data["historical_source"]["promotion_status"] == "unscored"
    assert "event:alpaca:sip:AAPL:" in trace.read_text()
    assert (
        "Final historical bars with modeled timing"
        in (trace.parent / "report.html").read_text()
    )
    assert (trace.parent / "data_access.json").exists()
    _, second, _, _ = backtest(config, json.dumps(config).encode(), tmp_path / "runs")
    assert (
        compare_run_pair(tmp_path / "runs", first, second)["ending_equity_delta"]
        == "0.00000"
    )
    assert len(result["fills"]) == 4


def test_public_offline_command_and_no_credentials_required(tmp_path, monkeypatch):
    _, config = cached(tmp_path)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    path = tmp_path / "run.json"
    path.write_text(json.dumps(config))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rsys.cli",
            "backtest",
            "--config",
            str(path),
            "--out",
            str(tmp_path / "runs"),
            "--report",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "4 simulated fill(s)" in result.stdout


@pytest.mark.parametrize("purpose", ["development", "validation"])
def test_holdout_denied_before_network_or_cache_read_including_warmup(
    tmp_path, monkeypatch, purpose
):
    acquisition, config = inputs(tmp_path)
    config["purpose"] = acquisition["purpose"] = purpose
    config["partitions"] = acquisition["partitions"] = [
        {"name": "final", "start": "2024-11-27", "end": "2024-11-27"},
        {"name": "development", "start": "2024-11-28", "end": "2024-12-31"},
    ]

    def denied(*args, **kwargs):
        pytest.fail("Read attempted before authorization")

    monkeypatch.setattr(BarCache, "load", denied)
    monkeypatch.setattr(BarCache, "acquire", denied)
    with pytest.raises(ValueError, match="access denied"):
        download(acquisition, tmp_path / "download")
    with pytest.raises(ValueError, match="access denied"):
        backtest(config, b"request", tmp_path / "runs")
    (manifest,) = (tmp_path / "runs").glob("attempts/*/manifest.json")
    assert json.loads(manifest.read_text())["status"] == "failed"
    log = json.loads((manifest.parent / "data_access.json").read_text())
    assert log[-1]["allowed"] is False


@pytest.mark.parametrize(
    "field,value",
    [("o", "NaN"), ("v", "-1"), ("l", "101"), ("t", "2024-11-27T09:30:00")],
)
def test_invalid_provider_payload_not_committed(tmp_path, field, value):
    config, _ = inputs(tmp_path)

    def invalid(params):
        page = response(params)
        page["bars"]["AAPL"][0][field] = value
        return page

    with pytest.raises(ValueError):
        download(
            config, tmp_path / "attempts", client=AlpacaBarsClient(transport=invalid)
        )
    assert not list((tmp_path / "cache").glob("requests/*"))
    (manifest,) = (tmp_path / "attempts").glob("downloads/*/manifest.json")
    assert json.loads(manifest.read_text())["status"] == "failed"


def test_missing_bars_remain_missing_and_execution_day_is_rejected(tmp_path):
    acquisition, config = inputs(tmp_path)

    def missing(params):
        page = response(params)
        if not params.get("page_token"):
            del page["bars"]["AAPL"][0]
        return page

    result, _ = download(
        acquisition, tmp_path / "attempts", client=AlpacaBarsClient(transport=missing)
    )
    assert all(
        not d["quality"]["coverage_complete"]
        and len(d["quality"]["missing_minutes"]) == 1
        for d in result["datasets"]
    )
    config["objects"] = {r["date"]: r["object_id"] for r in result["datasets"]}
    with pytest.raises(ValueError, match="Incomplete execution-day"):
        backtest(config, b"request", tmp_path / "runs")


def test_refresh_preserves_pinned_old_objects_and_tampering_is_rejected(tmp_path):
    acquisition, config = cached(tmp_path)
    cache = BarCache(config["cache"])
    req = request_identity("AAPL", date(2024, 11, 27), "sip", "-")
    old = config["objects"]["2024-11-27"]

    def changed(params):
        page = response(params)
        page["bars"]["AAPL"][0]["v"] = "200"
        return page

    result, _ = download(
        acquisition,
        tmp_path / "acquire",
        refresh=True,
        client=AlpacaBarsClient(transport=changed),
    )
    assert result["datasets"][0]["object_id"] != old
    assert cache.load(old, req)["bars"][0]["v"] == "50"
    path = next(Path(config["cache"]).glob(f"objects/*/{old}.json"))
    path.write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        backtest(config, b"request", tmp_path / "runs")


def test_duplicate_and_page_cycle_handling(tmp_path):
    config, _ = inputs(tmp_path)

    def cyclic(params):
        return {"bars": {"AAPL": []}, "next_page_token": "same"}

    with pytest.raises(ValueError, match="repeated"):
        download(
            config, tmp_path / "attempts", client=AlpacaBarsClient(transport=cyclic)
        )

    def conflict(params):
        page = response(params)
        page["bars"]["AAPL"].append({**page["bars"]["AAPL"][0], "v": "999"})
        return page

    with pytest.raises(ValueError, match="Conflicting duplicate"):
        download(
            config, tmp_path / "attempts", client=AlpacaBarsClient(transport=conflict)
        )


@pytest.mark.parametrize("status,calls", [(401, 1), (403, 1), (429, 3), (503, 3)])
def test_http_failures_retry_bounded_and_do_not_expose_secrets(tmp_path, status, calls):
    config, _ = inputs(tmp_path)
    requests = []

    def failure(params):
        requests.append(params)
        raise HTTPError("https://example.invalid", status, "SENTINEL_SECRET", {}, None)

    with pytest.raises(ValueError, match=f"HTTP {status}"):
        download(
            config,
            tmp_path / "attempts",
            client=AlpacaBarsClient(transport=failure, sleep=lambda _: None),
        )
    assert len(requests) == calls
    assert all(
        "SENTINEL_SECRET" not in p.read_text()
        for p in (tmp_path / "attempts").rglob("*.json")
    )


def test_transport_value_error_is_sanitized(tmp_path):
    config, _ = inputs(tmp_path)

    def failure(params):
        raise ValueError("invalid header value SENTINEL_SECRET")

    with pytest.raises(ValueError, match="verify APCA"):
        download(config, tmp_path, client=AlpacaBarsClient(transport=failure))
    assert all("SENTINEL_SECRET" not in p.read_text() for p in tmp_path.rglob("*.json"))


def test_action_review_and_direct_historical_bypass_fail_closed(tmp_path):
    _, config = cached(tmp_path)
    expanded = prepare_backtest(config, [])
    with pytest.raises(ValueError):
        SessionBatch(expanded)
    config["action_review"]["status"] = "pending"
    with pytest.raises(ValueError, match="corporate-actions review"):
        backtest(config, b"request", tmp_path / "runs")


@pytest.mark.parametrize("command", ["download", "backtest"])
@pytest.mark.parametrize("content", ["malformed SENTINEL_SECRET", "[]", None])
def test_public_invalid_input_retains_failure_without_raw_secrets(
    tmp_path, command, content
):
    path = tmp_path / "input.json"
    if content is not None:
        path.write_text(content)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rsys.cli",
            command,
            "--config",
            str(path),
            "--out",
            str(tmp_path / "runs"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    manifests = list((tmp_path / "runs").rglob("manifest.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["status"] == "failed"
    assert "SENTINEL_SECRET" not in result.stderr
    assert all(
        "SENTINEL_SECRET" not in p.read_text()
        for p in (tmp_path / "runs").rglob("*.json")
    )


@pytest.mark.parametrize(
    "field,value",
    [("schema_version", 2), ("provider", "other"), ("timing_basis", "recorded")],
)
def test_cache_rejects_unsupported_evidence_even_with_a_matching_hash(
    tmp_path, field, value
):
    from rsys.data.bar_cache import digest, encode

    _, config = cached(tmp_path)
    request = request_identity("AAPL", date(2024, 11, 27), "sip", "-")
    cache = BarCache(config["cache"])
    payload = cache.load(config["objects"]["2024-11-27"], request)
    payload[field] = value
    identity = digest(payload)
    target = Path(config["cache"]) / "objects" / digest(request) / (identity + ".json")
    target.write_bytes(encode(payload))
    with pytest.raises(ValueError, match="schema/provider/timing"):
        cache.load(identity, request)


def test_request_scoping_and_symlinks_cannot_alias_a_different_dataset(tmp_path):
    _, config = cached(tmp_path)
    cache = BarCache(config["cache"])
    request = request_identity("AAPL", date(2024, 11, 27), "iex", "-")
    identity = config["objects"]["2024-11-27"]
    with pytest.raises(FileNotFoundError):
        cache.load(identity, request)
    source = next(Path(config["cache"]).glob(f"objects/*/{identity}.json"))
    target = source.with_suffix(".other")
    source.rename(target)
    source.symlink_to(target)
    with pytest.raises(ValueError, match="escapes"):
        cache.load(identity, request_identity("AAPL", date(2024, 11, 27), "sip", "-"))


def test_download_partial_success_is_retained_and_resume_fetches_only_missing_days(
    tmp_path,
):
    config, _ = inputs(tmp_path)

    def partial(params):
        if params["start"].startswith("2024-11-29"):
            raise HTTPError("https://example.invalid", 403, "forbidden", {}, None)
        return response(params)

    with pytest.raises(ValueError):
        download(
            config, tmp_path / "attempts", client=AlpacaBarsClient(transport=partial)
        )
    (manifest,) = (tmp_path / "attempts").glob("downloads/*/manifest.json")
    assert len(json.loads(manifest.read_text())["datasets"]) == 1
    requested = []

    def resume(params):
        requested.append(params["start"])
        return response(params)

    result, _ = download(
        config, tmp_path / "attempts", client=AlpacaBarsClient(transport=resume)
    )
    assert len(result["datasets"]) == 3
    assert all(not start.startswith("2024-11-27") for start in requested)


def test_real_transport_missing_credentials_fails_before_open(tmp_path, monkeypatch):
    import rsys.data.alpaca as module

    config, _ = inputs(tmp_path)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        module,
        "build_opener",
        lambda *args: pytest.fail("HTTP opened without credentials"),
    )
    with pytest.raises(ValueError, match="APCA_API_KEY_ID"):
        download(config, tmp_path)


def test_pinned_future_bar_change_preserves_earlier_full_trace_with_negative_control(
    tmp_path,
):
    _, config = cached(tmp_path)
    cache = BarCache(config["cache"])

    def execute(cfg):
        _, _, trace, _ = backtest(
            cfg, json.dumps(cfg).encode(), tmp_path / "runs", report=False
        )
        return [
            row
            for row in map(json.loads, trace.read_text().splitlines())
            if row["session_date"] == "2024-11-29"
            and row["available_at_ms"] <= 35_250_000
        ]

    baseline = execute(config)
    assert any(row["kind"] == "fill" and row["status"] == "FILLED" for row in baseline)

    def future(params):
        page = response(params)
        for row in page["bars"]["AAPL"]:
            if datetime.fromisoformat(row["t"]).hour >= 16:
                row["v"] = "9999"
        return page

    changed = deepcopy(config)
    request = request_identity("AAPL", date(2024, 11, 29), "sip", "-")
    changed["objects"]["2024-11-29"] = cache.acquire(
        request, AlpacaBarsClient(transport=future), refresh=True
    )
    assert execute(changed) == baseline

    def past(params):
        page = response(params)
        for row in page["bars"]["AAPL"]:
            row["v"] = "0"
        return page

    changed = deepcopy(config)
    request = request_identity("AAPL", date(2024, 11, 27), "sip", "-")
    changed["objects"]["2024-11-27"] = cache.acquire(
        request, AlpacaBarsClient(transport=past), refresh=True
    )
    negative = execute(changed)
    assert negative != baseline
    assert not any(
        row["kind"] == "fill" and row["status"] == "FILLED" for row in negative
    )

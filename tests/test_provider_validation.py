"""Bounded provider workflow: fake HTTP, real cache/preflight/backtest/artifacts."""

import hashlib
import json
import os
import socket
import subprocess
import sys
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from test_alpaca_pipeline import ROOT, inputs, response

from rsys.application import provider_validation as service
from rsys.cli import main
from rsys.data.alpaca import AlpacaBarsClient
from rsys.data.bar_cache import BarCache, request_identity
from rsys.research.errors import InspectionError
from rsys.research.inspection import inspect_attempt


def sample(tmp_path, change=None):
    _, config = inputs(tmp_path)
    if change:
        change(config)
    template = tmp_path / "template.json"
    template.write_text(json.dumps(config))
    request = {
        "schema_version": 1,
        "backtest_config": template.name,
        "sample_dates": ["2024-11-27", "2024-11-29", "2024-12-02"],
        "max_sessions": 3,
        "adjustment": "raw",
    }
    path = tmp_path / "sample.json"
    path.write_text(json.dumps(request))
    return path, config


def retained(root):
    paths = list((root / "provider-validations").glob("*/validation.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text()), paths[0].parent


@pytest.mark.parametrize(
    "flag,environment", [(False, None), (True, None), (False, "1")]
)
def test_credentials_alone_never_enable_provider_access(
    tmp_path, monkeypatch, flag, environment
):
    path, _ = sample(tmp_path)
    monkeypatch.setenv("APCA_API_KEY_ID", "PRIVATE_KEY")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "PRIVATE_SECRET")
    if environment:
        monkeypatch.setenv("RSYS_ENABLE_ALPACA_VALIDATION", environment)
    else:
        monkeypatch.delenv("RSYS_ENABLE_ALPACA_VALIDATION", raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Disabled workflow constructed a provider or opened the cache")

    monkeypatch.setattr(service, "AlpacaBarsClient", forbidden)
    monkeypatch.setattr(BarCache, "__init__", forbidden)
    with pytest.raises(ValueError, match="Live sample disabled"):
        service.validate_sample(path, tmp_path / "out", enable_live=flag)
    report, attempt = retained(tmp_path / "out")
    assert report["real_provider_gate"] == "not_run"
    assert report["status"] == "failed" and report["dates"] == []
    assert inspect_attempt(tmp_path / "out", attempt.name).outcome == "failed"


def test_full_fake_workflow_retains_exact_pins_and_hand_calculated_trade_result(
    tmp_path, monkeypatch
):
    path, config = sample(tmp_path)
    calls = []

    def transport(params):
        calls.append(deepcopy(params))
        return response(params)

    result, manifest = service.validate_sample(
        path, tmp_path / "out", transport=transport
    )
    assert result["status"] == "success"
    assert result["evidence_type"] == "synthetic_provider_validation"
    assert result["real_provider_gate"] == "not_run"
    assert len(calls) == 6
    assert [p["start"][:10] for p in calls] == [
        "2024-11-27",
        "2024-11-27",
        "2024-11-29",
        "2024-11-29",
        "2024-12-02",
        "2024-12-02",
    ]
    assert all(
        p["feed"] == "sip" and p["asof"] == "-" and p["adjustment"] == "raw"
        for p in calls
    )
    assert result["provider_stats"] == {
        "request_count": 6,
        "retry_count": 0,
        "page_count": 6,
        "http_errors": {},
    }
    assert [r["observed"]["bars"] for r in result["dates"]] == [390, 210, 390]
    checklist = result["checklist"]
    assert checklist["cache_reuse"] == {"status": "verified", "additional_requests": 0}
    assert checklist["coverage"]["status"] == "verified"
    for name in (
        "live_feed_access",
        "corporate_action_audit",
        "arrival_history",
        "operator_review",
    ):
        assert checklist[name]["status"] == "not_run"
    simulation = json.loads(Path(checklist["offline_backtest"]["manifest"]).read_text())
    assert Decimal(simulation["ending_equity"]) == Decimal("1022.34800")
    assert simulation["metrics"]["completed_trade_count"] == 2
    pins = json.loads((manifest.parent / "backtest.json").read_text())["objects"]
    assert pins == simulation["historical_source"]["dataset_hashes"]
    assert json.loads((tmp_path / "template.json").read_text()) == config
    assert inspect_attempt(tmp_path / "out", manifest.parent.name).outcome == "success"
    assert len(list((tmp_path / "out/attempts").glob("*/manifest.json"))) == 1

    # Repeat the entire harness; a miss would fail instead of touching a provider.
    def forbidden(_params):
        pytest.fail("Cached rerun requested provider data")

    repeated, second = service.validate_sample(
        path, tmp_path / "repeat", transport=forbidden
    )
    assert repeated["provider_stats"]["request_count"] == 0
    assert repeated["real_provider_gate"] == "not_run"
    assert json.loads((second.parent / "backtest.json").read_text())["objects"] == pins
    (manifest.parent / "backtest.json").write_text("{}")
    with pytest.raises(InspectionError, match="integrity"):
        inspect_attempt(tmp_path / "out", manifest.parent.name)


@pytest.mark.parametrize("code", [401, 403, 429, 500, 502, 503, 504])
def test_http_failures_are_bounded_sanitized_and_retained(tmp_path, code, monkeypatch):
    path, _ = sample(tmp_path)
    monkeypatch.setenv("APCA_API_SECRET_KEY", "PRIVATE_SECRET")
    sleeps = []

    def denied(_params):
        raise HTTPError("https://PRIVATE_SECRET/", code, "PRIVATE_SECRET", {}, None)

    with pytest.raises(ValueError, match=f"HTTP {code}"):
        service.validate_sample(
            path, tmp_path / "out", transport=denied, sleep=sleeps.append
        )
    result, attempt = retained(tmp_path / "out")
    count = 1 if code in (401, 403) else 3
    assert result["provider_stats"] == {
        "request_count": count,
        "retry_count": count - 1,
        "page_count": 0,
        "http_errors": {str(code): count},
    }
    assert sleeps == ([] if count == 1 else [1, 2])
    assert [r["status"] for r in result["dates"]] == ["failed", "not_run", "not_run"]
    assert result["checklist"]["acquisition"]["status"] == "failed"
    assert inspect_attempt(tmp_path / "out", attempt.name).outcome == "failed"
    for artifact in (tmp_path / "out").rglob("*"):
        if artifact.is_file():
            assert b"PRIVATE_SECRET" not in artifact.read_bytes()


@pytest.mark.parametrize(
    "error",
    [
        URLError("secret transport"),
        OSError("secret transport"),
        ValueError("secret transport"),
    ],
)
def test_transport_errors_do_not_retain_raw_exception_text(tmp_path, error):
    path, _ = sample(tmp_path)
    sleeps = []

    def fail(_params):
        raise error

    with pytest.raises(ValueError):
        service.validate_sample(
            path, tmp_path / "out", transport=fail, sleep=sleeps.append
        )
    report, _ = retained(tmp_path / "out")
    assert "secret transport" not in json.dumps(report)
    assert sleeps == ([] if type(error) is ValueError else [1, 2])


def test_retry_recovery_empty_page_and_numeric_duplicate_use_same_workflow(tmp_path):
    path, _ = sample(tmp_path)
    calls, sleeps = [], []

    def unusual(params):
        calls.append(deepcopy(params))
        if len(calls) <= 2:
            raise HTTPError("unused", 429, "rate limit", {}, None)
        if "page_token" not in params:
            return {"bars": {"AAPL": []}, "next_page_token": "empty-first"}
        if params["page_token"] == "empty-first":
            page = response({k: v for k, v in params.items() if k != "page_token"})
            duplicate = deepcopy(page["bars"]["AAPL"][0])
            for name in ("o", "h", "l", "c", "v"):
                duplicate[name] = str(
                    Decimal(duplicate[name]).quantize(Decimal("0.00"))
                )
            page["bars"]["AAPL"].append(duplicate)
            return page
        return response(params)

    result, _ = service.validate_sample(
        path, tmp_path / "out", transport=unusual, sleep=sleeps.append
    )
    assert result["status"] == "success" and sleeps == [1, 2]
    assert result["provider_stats"] == {
        "request_count": 11,
        "retry_count": 2,
        "page_count": 9,
        "http_errors": {"429": 2},
    }
    assert all(row["quality"]["duplicate_rows"] == 1 for row in result["dates"])


@pytest.mark.parametrize("defect", ["ohlc", "timestamp", "conflict", "token", "shape"])
def test_malformed_response_never_becomes_a_successful_sample(tmp_path, defect):
    path, _ = sample(tmp_path)

    def malformed(params):
        page = response(params)
        row = page["bars"]["AAPL"][0]
        if defect == "ohlc":
            row["h"] = "0"
        elif defect == "timestamp":
            row["t"] = row["t"].replace(":00+", ":00.000000001+")
        elif defect == "conflict":
            other = deepcopy(row)
            other["v"] = "9999"
            page["bars"]["AAPL"].append(other)
        elif defect == "token":
            page["next_page_token"] = "repeated"
        else:
            page["bars"] = []
        return page

    with pytest.raises((ValueError, TypeError)):
        service.validate_sample(path, tmp_path / "out", transport=malformed)
    result, _ = retained(tmp_path / "out")
    assert result["status"] == "failed"
    assert result["checklist"]["offline_backtest"]["status"] == "not_run"
    assert result["dates"][0]["status"] == "failed"
    assert not list((tmp_path / "out/attempts").glob("*/manifest.json"))


def test_interrupted_second_date_preserves_first_pin_and_retry_reuses_it(tmp_path):
    path, _ = sample(tmp_path)

    def interrupted(params):
        if params["start"].startswith("2024-11-29"):
            raise KeyboardInterrupt()
        return response(params)

    with pytest.raises(KeyboardInterrupt):
        service.validate_sample(path, tmp_path / "out", transport=interrupted)
    report, attempt = retained(tmp_path / "out")
    assert report["status"] == "interrupted"
    assert [r["status"] for r in report["dates"]] == [
        "success",
        "interrupted",
        "not_run",
    ]
    assert inspect_attempt(tmp_path / "out", attempt.name).outcome == "interrupted"
    first_pin = report["dates"][0]["object_id"]
    calls = []

    def retry(params):
        calls.append(params["start"][:10])
        return response(params)

    complete, _ = service.validate_sample(path, tmp_path / "retry", transport=retry)
    assert complete["dates"][0]["object_id"] == first_pin
    assert complete["dates"][0]["request_count"] == 0
    assert calls == ["2024-11-29", "2024-11-29", "2024-12-02", "2024-12-02"]


@pytest.mark.parametrize(
    "defect", ["holdout", "warmup", "pins", "cost", "scanner", "strategy", "flatten"]
)
def test_invalid_scope_or_settings_fail_before_cache_or_client(
    tmp_path, monkeypatch, defect
):
    def change(config):
        if defect == "holdout":
            config["purpose"] = "final"
        elif defect == "warmup":
            config["partitions"][0]["start"] = "2024-11-29"
        elif defect == "pins":
            config["objects"] = {"2024-11-27": "0" * 64}
        elif defect == "cost":
            config["account"]["costs"]["entry_fee"] = "-1"
        elif defect == "scanner":
            config["scanner"]["lookback_sessions"] = 0
        elif defect == "strategy":
            config["strategy"]["strategy"] = "unknown"
        else:
            config["account"]["flatten_at"] = "16:00:00"

    path, _ = sample(tmp_path, change)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid scope/settings touched cache or provider")

    monkeypatch.setattr(BarCache, "__init__", forbidden)
    monkeypatch.setattr(service, "AlpacaBarsClient", forbidden)
    with pytest.raises((ValueError, TypeError)):
        service.validate_sample(path, tmp_path / "out", transport=response)
    report, _ = retained(tmp_path / "out")
    assert report["dates"] == []


@pytest.mark.parametrize(
    "dates,maximum",
    [
        ([], 3),
        (["2024-11-29", "2024-12-02"], 3),
        (["2024-11-27", "2024-11-29", "2024-12-02"], 2),
    ],
)
def test_explicit_sample_budget_never_automatically_expands(tmp_path, dates, maximum):
    path, _ = sample(tmp_path)
    request = json.loads(path.read_text())
    request.update(sample_dates=dates, max_sessions=maximum)
    path.write_text(json.dumps(request))

    def forbidden(_params):
        pytest.fail("Invalid date budget reached HTTP")

    with pytest.raises(ValueError):
        service.validate_sample(path, tmp_path / "out", transport=forbidden)


def test_offline_tripwire_has_a_negative_control_and_restores_socket(
    tmp_path, monkeypatch
):
    path, _ = sample(tmp_path)
    original = socket.getaddrinfo

    def leaking_backtest(*args, **kwargs):
        socket.getaddrinfo("invalid.test", 443)

    monkeypatch.setattr(service, "backtest", leaking_backtest)
    with pytest.raises(RuntimeError, match="network access is disabled"):
        service.validate_sample(path, tmp_path / "out", transport=response)
    report, _ = retained(tmp_path / "out")
    assert report["checklist"]["offline_backtest"]["status"] == "failed"
    assert socket.getaddrinfo is original


def test_refresh_is_explicit_and_preserves_old_objects(tmp_path):
    path, _ = sample(tmp_path)
    first, _ = service.validate_sample(path, tmp_path / "first", transport=response)

    def revised(params):
        page = response(params)
        page["bars"]["AAPL"][-1]["v"] = "12345"
        return page

    second, _ = service.validate_sample(
        path, tmp_path / "second", transport=revised, refresh=True
    )
    cache = BarCache(tmp_path / "cache")
    for old, new in zip(first["dates"], second["dates"], strict=True):
        assert old["object_id"] != new["object_id"]
        identity = request_identity("AAPL", date.fromisoformat(old["date"]), "sip", "-")
        assert cache.load(old["object_id"], identity)
        assert cache.load(new["object_id"], identity)


def test_public_cli_disabled_and_status_are_safe_in_subprocess(tmp_path):
    path, _ = sample(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "RSYS_ENABLE_ALPACA_VALIDATION"}
    env.update(APCA_API_KEY_ID="FAKE_PRESENT", APCA_API_SECRET_KEY="FAKE_PRESENT")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "rsys.cli",
            "validate-alpaca",
            "--config",
            str(path),
            "--out",
            str(tmp_path / "out"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 3 and "Live sample disabled" in completed.stderr
    _, attempt = retained(tmp_path / "out")
    assert str(attempt / "manifest.json") in completed.stderr
    status = subprocess.run(
        [
            sys.executable,
            "-m",
            "rsys.cli",
            "status",
            "--storage-root",
            str(tmp_path / "out"),
            "--id",
            attempt.name,
            "--json",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert status.returncode == 0 and json.loads(status.stdout)["outcome"] == "failed"


def test_invalid_json_and_html_content_cannot_leak_or_execute(tmp_path):
    path, _ = sample(
        tmp_path,
        lambda c: c["action_review"].update(source="<script>alert(1)</script>"),
    )
    _, manifest = service.validate_sample(path, tmp_path / "good", transport=response)
    html = (manifest.parent / "report.html").read_text()
    assert "<script>" not in html and "&lt;script&gt;" in html
    path.write_text('{"api_key":"private-malformed",')
    assert (
        main(["validate-alpaca", "--config", str(path), "--out", str(tmp_path / "bad")])
        == 3
    )
    report, _ = retained(tmp_path / "bad")
    assert "private-malformed" not in json.dumps(report)


def test_manifest_only_failure_is_retained_as_failure(tmp_path, monkeypatch):
    path, _ = sample(tmp_path)
    real_write = service.atomic_write_json
    calls = 0

    def fail_final(path, value):
        nonlocal calls
        if path.name == "manifest.json":
            calls += 1
            if calls == 2:
                raise OSError("synthetic final write failure")
        return real_write(path, value)

    monkeypatch.setattr(service, "atomic_write_json", fail_final)
    with pytest.raises(OSError, match="synthetic final write") as error:
        service.validate_sample(path, tmp_path / "out", transport=response)
    manifest = error.value.manifest_path
    assert json.loads(manifest.read_text())["status"] == "failed"
    assert inspect_attempt(tmp_path / "out", manifest.parent.name).outcome == "failed"


@pytest.mark.parametrize("enabled", [False, True])
def test_explicit_integration_selection_without_required_controls_fails_safely(
    tmp_path, enabled
):
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "RSYS_ENABLE_ALPACA_VALIDATION",
            "RSYS_ALPACA_SAMPLE_CONFIG",
            "RSYS_ALPACA_SAMPLE_OUT",
        )
    }
    env.update(APCA_API_KEY_ID="FAKE_PRESENT", APCA_API_SECRET_KEY="FAKE_PRESENT")
    if enabled:
        env["RSYS_ENABLE_ALPACA_VALIDATION"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/test_alpaca_sample.py",
            "-m",
            "alpaca_integration",
            "-q",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert (
        "RSYS_ALPACA_SAMPLE_CONFIG" in completed.stdout
        if enabled
        else "RSYS_ENABLE_ALPACA_VALIDATION=1" in completed.stdout
    )
    assert "Set APCA_API" not in completed.stdout
    assert "network" not in completed.stdout.lower()


def test_nonconsecutive_dates_do_not_download_the_intervening_range(tmp_path):
    def change(config):
        config["sessions"] = ["2024-11-29", "2024-12-10"]
        config["partitions"] = [
            {"name": "development", "start": "2024-11-01", "end": "2024-12-10"}
        ]
        config["action_review"]["end"] = "2024-12-10"

    path, _ = sample(tmp_path, change)
    request = json.loads(path.read_text())
    request.update(
        sample_dates=["2024-11-27", "2024-11-29", "2024-12-09", "2024-12-10"],
        max_sessions=4,
    )
    path.write_text(json.dumps(request))
    calls = []

    def transport(params):
        calls.append(params["start"][:10])
        return response(params)

    result, _ = service.validate_sample(path, tmp_path / "out", transport=transport)
    assert result["status"] == "success"
    assert calls == [day for day in request["sample_dates"] for _ in range(2)]


def test_overriding_default_pytest_filter_does_not_implicitly_enable_integration(
    tmp_path,
):
    path, _ = sample(tmp_path)
    env = {
        **os.environ,
        "RSYS_ENABLE_ALPACA_VALIDATION": "1",
        "RSYS_ALPACA_SAMPLE_CONFIG": str(path),
        "RSYS_ALPACA_SAMPLE_OUT": str(tmp_path / "forbidden"),
        "APCA_API_KEY_ID": "FAKE_PRESENT",
        "APCA_API_SECRET_KEY": "FAKE_PRESENT",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/test_alpaca_sample.py",
            "-m",
            "not unrelated_marker",
            "-q",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "Select this real-data check explicitly" in completed.stdout
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize("missing_day", ["2024-11-27", "2024-11-29"])
def test_empty_data_is_reported_as_missing_and_not_verified_zero(tmp_path, missing_day):
    path, _ = sample(tmp_path)

    def empty(params):
        if params["start"].startswith(missing_day):
            return {"bars": {"AAPL": []}, "next_page_token": None}
        return response(params)

    if missing_day == "2024-11-29":
        with pytest.raises(ValueError, match="preflight blocked"):
            service.validate_sample(path, tmp_path / "out", transport=empty)
        report, _ = retained(tmp_path / "out")
        assert report["status"] == "failed"
        assert report["checklist"]["offline_backtest"]["status"] == "not_run"
    else:
        report, _ = service.validate_sample(path, tmp_path / "out", transport=empty)
        assert report["status"] == "success"
    assert report["checklist"]["coverage"]["status"] == "failed"
    assert any("absent minutes" in item for item in report["discrepancies"])
    assert (
        report["dates"][0 if missing_day.endswith("27") else 1]["quality"][
            "coverage_complete"
        ]
        is False
    )


def test_enabled_path_with_mocked_http_still_requires_manual_review_and_cache_does_not_prove_access(
    tmp_path, monkeypatch
):
    path, _ = sample(tmp_path)
    monkeypatch.setenv("RSYS_ENABLE_ALPACA_VALIDATION", "1")
    # Exercise the enabled branch without making any real provider request.
    monkeypatch.setattr(AlpacaBarsClient, "_request", staticmethod(response))
    result, _ = service.validate_sample(path, tmp_path / "out", enable_live=True)
    assert result["real_provider_gate"] == "pending_manual_review"
    assert result["checklist"]["live_feed_access"]["status"] == "verified"
    assert result["checklist"]["operator_review"]["status"] == "not_run"

    def forbidden(_params):
        pytest.fail("Warm cache read HTTP/credentials")

    monkeypatch.setattr(AlpacaBarsClient, "_request", staticmethod(forbidden))
    repeat, _ = service.validate_sample(path, tmp_path / "again", enable_live=True)
    assert repeat["real_provider_gate"] == "not_run"
    assert repeat["checklist"]["live_feed_access"]["status"] == "not_run"


@pytest.mark.parametrize("stage", ["preflight", "reuse"])
def test_attempted_failed_check_is_not_mislabeled_as_not_run(
    tmp_path, monkeypatch, stage
):
    path, _ = sample(tmp_path)
    if stage == "preflight":

        def fail(*args, **kwargs):
            raise OSError("synthetic preflight failure")

        monkeypatch.setattr(service, "check_data", fail)
    else:
        original = service.download
        calls = 0

        def fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("synthetic reuse failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(service, "download", fail)
    with pytest.raises(OSError):
        service.validate_sample(path, tmp_path / "out", transport=response)
    report, _ = retained(tmp_path / "out")
    name = "coverage" if stage == "preflight" else "cache_reuse"
    assert report["checklist"][name]["status"] == "failed"
    assert report["checklist"]["offline_backtest"]["status"] == "not_run"


@pytest.mark.parametrize("defect", ["account", "purpose", "malformed", "unreadable"])
def test_failed_template_preserves_identity_before_validation(tmp_path, defect):
    path, config = sample(tmp_path)
    template = tmp_path / "template.json"
    if defect == "account":
        config["account"]["initial_cash"] = "-123"
        template.write_text(json.dumps(config))
    elif defect == "purpose":
        config["purpose"] = "final"
        template.write_text(json.dumps(config))
    elif defect == "malformed":
        template.write_text('{"api_key":"SECRET_BAD_TEMPLATE",')
    else:
        template.unlink()
    raw_hash = (
        hashlib.sha256(template.read_bytes()).hexdigest() if template.exists() else None
    )

    def forbidden(_params):
        pytest.fail("Invalid template touched provider")

    with pytest.raises((ValueError, OSError)):
        service.validate_sample(path, tmp_path / "out", transport=forbidden)
    report, _ = retained(tmp_path / "out")
    identity = report["backtest_template"]
    assert identity["path"] == str(template.resolve())
    assert identity["sha256"] == raw_hash
    assert report["checklist"]["request_scope"]["status"] == "failed"
    if defect in ("account", "purpose"):
        template.write_text("{}")  # A later edit cannot erase the failed attempt.
        assert identity["config"] == config
    else:
        assert "config" not in identity
        assert "SECRET_BAD_TEMPLATE" not in json.dumps(report)


@pytest.mark.parametrize("transport", [False, 0, ""])
def test_invalid_transport_cannot_fall_back_to_real_provider(
    tmp_path, monkeypatch, transport
):
    path, _ = sample(tmp_path)

    def forbidden(_params):
        pytest.fail("Invalid injected transport selected the real downloader")

    monkeypatch.setattr(AlpacaBarsClient, "_request", staticmethod(forbidden))
    with pytest.raises(TypeError, match="transport must be callable"):
        service.validate_sample(path, tmp_path / "out", transport=transport)
    report, _ = retained(tmp_path / "out")
    assert report["status"] == "failed" and report["real_provider_gate"] == "not_run"
    assert report["provider_stats"] == {} and report["dates"] == []


def test_falsey_callable_transport_stays_injected_without_live_enablement(
    tmp_path, monkeypatch
):
    path, _ = sample(tmp_path)

    class FalseyTransport:
        def __bool__(self):
            return False

        def __call__(self, params):
            return response(params)

    def forbidden(_params):
        pytest.fail("Falsey callable was discarded for the real downloader")

    monkeypatch.setattr(AlpacaBarsClient, "_request", staticmethod(forbidden))
    result, _ = service.validate_sample(
        path, tmp_path / "out", transport=FalseyTransport()
    )
    assert result["status"] == "success"
    assert result["evidence_type"] == "synthetic_provider_validation"
    assert result["real_provider_gate"] == "not_run"
    assert result["provider_stats"]["request_count"] == 6

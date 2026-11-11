"""Public sparse-data workflow and hand-checked missing-candle boundaries."""

import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from test_alpaca_pipeline import inputs, response

from rsys.application.historical import backtest, download, prepare_backtest
from rsys.application.preflight import check_data
from rsys.data.alpaca import AlpacaBarsClient, AlpacaTradesClient
from rsys.data.bar_cache import BarCache, digest, request_identity
from rsys.data.gaps import classify_gap, trade_request
from rsys.research.comparison import load_run_manifest
from rsys.research.errors import CompareCliError


def trade(minute, *, size=7, conditions=None, identity=1, price=100):
    return {
        "t": (datetime.fromisoformat(minute) + timedelta(seconds=1)).isoformat(),
        "i": identity,
        "x": "V",
        "z": "C",
        "p": price,
        "s": size,
        "c": ["@", "I"] if conditions is None else conditions,
    }


def gap_pages(rows):
    return [{"trades": {"AAPL": rows}, "next_page_token": None}]


@pytest.mark.parametrize(
    "conditions,status",
    [
        (["I"], "VOLUME_ONLY"),
        (["@", "I", "F"], "VOLUME_ONLY"),
        (["@"], "UNKNOWN"),
        (["I", "M"], "UNKNOWN"),
        (["I", "unknown"], "UNKNOWN"),
    ],
)
def test_odd_lots_have_volume_but_no_candle_and_unknown_is_not_zero(conditions, status):
    request = request_identity("AAPL", date(2024, 11, 29), "iex", "-")
    minute = request["start"]
    result = classify_gap(
        gap_pages([trade(minute, conditions=conditions)]), request, minute
    )
    assert result["status"] == status
    assert result["volume"] == ("7" if status == "VOLUME_ONLY" else None)
    assert not {"open", "close", "high", "low"} & result.keys()


def test_empty_paginated_feed_response_and_duplicates_are_distinct_from_missing_evidence():
    request = request_identity("AAPL", date(2024, 11, 29), "iex", "-")
    minute = request["start"]
    assert (
        classify_gap(gap_pages([]), request, minute)["status"] == "NO_REPORTED_TRADES"
    )
    row = trade(minute)
    assert classify_gap(gap_pages([row, row]), request, minute)["volume"] == "7"
    with pytest.raises(ValueError, match="Conflicting"):
        classify_gap(gap_pages([row, {**row, "s": 8}]), request, minute)
    for pages in ([], [{"trades": {}, "next_page_token": "more"}], [{"trades": None}]):
        with pytest.raises(ValueError):
            classify_gap(pages, request, minute)
    params = trade_request(request, minute)
    assert params["end"] == "2024-11-29T14:30:59.999999999Z"
    assert "timeframe" not in params and "adjustment" not in params


def sparse_cache(tmp_path, *, gaps=None, conditions=None, enrich=True):
    acquisition, config = inputs(tmp_path)
    acquisition["dataset"]["feed"] = config["dataset"]["feed"] = "iex"
    config["missing_bar_policy"] = "continue"
    # Missing range bar, after-range bar and a bar spanning a requested flatten.
    gaps = gaps or {"2024-11-27": [5], "2024-11-29": [5, 17, 19], "2024-12-02": [5]}
    if enrich:
        acquisition["gap_evidence"] = "trades"

    def bars(params):
        result = response(params)
        day = datetime.fromisoformat(params["start"]).date().isoformat()
        opening = datetime.fromisoformat(params["start"])
        missing = {opening + timedelta(minutes=m) for m in gaps.get(day, [])}
        result["bars"]["AAPL"] = [
            r
            for r in result["bars"]["AAPL"]
            if datetime.fromisoformat(r["t"]) not in missing
        ]
        return result

    def trades(params):
        return gap_pages([trade(params["start"], conditions=conditions)])[0]

    report, _ = download(
        acquisition,
        tmp_path / "acquisition",
        client=AlpacaBarsClient(transport=bars),
        gap_client=AlpacaTradesClient(transport=trades),
    )
    config["objects"] = {r["date"]: r["object_id"] for r in report["datasets"]}
    return acquisition, config


def test_public_cache_preflight_backtest_keeps_trading_and_labels_estimates(
    tmp_path, monkeypatch
):
    acquisition, config = sparse_cache(tmp_path)

    def denied(*args, **kwargs):
        pytest.fail("network called during replay or cache reuse")

    monkeypatch.setattr(AlpacaBarsClient, "_request", denied)
    repeated, _ = download(acquisition, tmp_path / "repeat")
    assert {r["date"]: r["object_id"] for r in repeated["datasets"]} == config[
        "objects"
    ]
    checked, _ = check_data(config, tmp_path / "check")
    assert checked["can_execute"] and checked["history_coverage_complete"]
    assert all(r["coverage_status"] == "explained_gaps" for r in checked["datasets"])
    summary, run_id, trace, manifest = backtest(
        config, json.dumps(config).encode(), tmp_path / "runs", report=True
    )
    retained = json.loads(manifest.read_text())
    assert retained["status"] == "success"
    assert summary["result_quality"] == "estimate"
    assert summary["estimate_label"] == "IEX-based estimates"
    assert (
        summary["metrics"]["completed_trade_count"] > 0
    )  # genuine non-vacuous execution
    assert all(s["status"] == "success" for s in retained["sessions"])
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    gaps = [r for r in rows if r["kind"] == "missing_minute"]
    assert len(gaps) == 4  # warmup gaps are history, not replay events
    assert all(r["gap"]["volume"] == "7" for r in gaps)
    assert "IEX-based estimates" in (manifest.parent / "report.html").read_text()
    assert load_run_manifest(tmp_path / "runs", run_id)["result_quality"] == "estimate"
    # Scanner uses actual bars plus separately retained missing-minute volume once.
    decision = next(
        r
        for r in rows
        if r["kind"] == "decision"
        and r["session_date"] == "2024-11-29"
        and r["available_at_ms"] == (9 * 3600 + 46 * 60) * 1000 + 250
    )
    detail = decision["scanner_evidence"]["symbols"]["AAPL"]
    assert detail["current_volume"] == "1507"
    assert "current_gap_snapshot_id" in detail


def test_unknown_gaps_continue_but_results_are_inconclusive_and_not_scored(tmp_path):
    _, config = sparse_cache(tmp_path, gaps={"2024-11-29": [19]}, enrich=False)
    checked, _ = check_data(config, tmp_path / "check")
    assert checked["can_execute"]
    assert checked["result_quality"] == "inconclusive_missing_data"
    summary, run_id, trace, manifest = backtest(
        config, json.dumps(config).encode(), tmp_path / "runs", report=True
    )
    assert summary["metrics"]["completed_trade_count"] > 0
    assert summary["result_quality"] == "inconclusive_missing_data"
    from rsys.reporting.html import load_attempt

    assert (
        load_attempt(manifest.parent)[0]["result_quality"]
        == "inconclusive_missing_data"
    )
    assert (
        json.loads(manifest.read_text())["metrics_scope"] == "diagnostic_estimate_only"
    )
    assert "INCONCLUSIVE" in (manifest.parent / "report.html").read_text()
    with pytest.raises(CompareCliError):
        load_run_manifest(tmp_path / "runs", run_id)
    assert any(
        json.loads(r)["kind"] == "missing_minute"
        for r in trace.read_text().splitlines()
    )


def test_v1_cache_enrichment_preserves_old_pins_and_revalidates_gap_classification(
    tmp_path,
):
    acquisition, config = sparse_cache(tmp_path, enrich=False)
    old = deepcopy(config["objects"])
    acquisition["gap_evidence"] = "trades"
    report, _ = download(
        acquisition,
        tmp_path / "enriched",
        client=AlpacaBarsClient(transport=lambda _: pytest.fail("bar cache ignored")),
        gap_client=AlpacaTradesClient(
            transport=lambda p: gap_pages([trade(p["start"])])[0]
        ),
    )
    cache = BarCache(config["cache"])
    for row in report["datasets"]:
        day = date.fromisoformat(row["date"])
        request = request_identity("AAPL", day, "iex", "-")
        assert cache.load(old[str(day)], request)["schema_version"] == 1
        payload = cache.load(row["object_id"], request)
        assert payload["schema_version"] == 2
        assert old[str(day)] != row["object_id"]
        payload["gaps"][0]["volume"] = "0"
        identity = digest(payload)
        cache._write(cache._path("objects/" + digest(request), identity), payload)
        with pytest.raises(ValueError, match="classification mismatch"):
            cache.load(identity, request)


def test_strict_policy_is_still_available_and_bad_policy_fails_before_io(tmp_path):
    _, config = sparse_cache(tmp_path)
    config["missing_bar_policy"] = "strict"
    with pytest.raises(ValueError, match="Incomplete execution-day"):
        prepare_backtest(config, [])
    config["missing_bar_policy"] = "guess"
    config["cache"] = "does-not-exist"
    with pytest.raises(ValueError, match="missing_bar_policy"):
        prepare_backtest(config, [])


@pytest.mark.parametrize("timer", ["flatten", "max_hold"])
def test_timer_fires_inside_gap_without_inventing_fill_or_using_late_pre_request_price(
    timer,
):
    from test_standalone_run import _bar, _build_runner, _ms

    from rsys.execution.models import ExecutionCosts, ExecutionPolicy

    runner, session = _build_runner()
    # The actual entry is at 09:47:00.250 with positive feed delay.
    # No bars at 09:48 or 09:49; a timer requests exit at 09:48:30.
    # Price becomes available at 09:50:00.250; both market and delivery time are later.
    entry_bar = deepcopy(session["bars"]["AAA"][-1])
    from dataclasses import replace

    session["bars"]["AAA"][-1] = replace(
        entry_bar,
        bar_start_ms=_ms("2024-11-29T09:47:00"),
        available_at_ms=_ms("2024-11-29T09:48:00"),
    )
    session["bars"]["AAA"].append(
        _bar(
            {
                "bar_start": "2024-11-29T09:50:00",
                "available_at": "2024-11-29T09:51:00",
                "open": "103",
                "high": "103",
                "low": "103",
                "close": "103",
                "volume": "100",
            }
        )
    )
    # BBB is excluded, and there are no fresh scanner candidates during the gap.
    runner.costs = ExecutionCosts(feed_delay_ms=250)
    requested = _ms("2024-11-29T09:48:30")
    runner.execution = (
        ExecutionPolicy(flatten_at_ms=requested)
        if timer == "flatten"
        else ExecutionPolicy(max_hold_ms=89_750)
    )
    runner.gaps = {
        "AAA": [
            {
                "minute": f"2024-11-29T14:{m}:00+00:00",
                "status": "VOLUME_ONLY",
                "volume": "9",
            }
            for m in (48, 49)
        ]
    }
    runner.run_session(session["bars"], session["scanner"])
    entry, exit_fill = [f for f in runner.fills if f.status in ("FILLED", "EXIT")]
    assert entry.available_at_ms == _ms("2024-11-29T09:47:00") + 250
    assert exit_fill.available_at_ms == _ms("2024-11-29T09:50:00") + 250
    assert exit_fill.event_time_ms == _ms("2024-11-29T09:50:00")
    assert exit_fill.price == Decimal(103) and exit_fill.reason == "TIMER"
    event = next(r for r in runner.trace if r["kind"] == timer)
    assert event["available_at_ms"] == requested
    assert "AAA" in event["account"]["positions"]
    assert runner.portfolio.realized_pnl == Decimal(10)  # ten shares, 103-102, no costs


def test_stop_remains_active_across_a_gap_and_uses_real_next_open():
    from test_standalone_run import _bar, _build_runner

    runner, session = _build_runner()
    runner.gaps = {
        "AAA": [
            {
                "minute": "2024-11-29T14:47:00+00:00",
                "status": "VOLUME_ONLY",
                "volume": "7",
            }
        ]
    }
    session["bars"]["AAA"].append(
        _bar(
            {
                "bar_start": "2024-11-29T09:48:00",
                "available_at": "2024-11-29T09:49:00",
                "open": "90",
                "high": "90",
                "low": "90",
                "close": "90",
                "volume": "100",
            }
        )
    )
    runner.run_session(session["bars"], session["scanner"])
    fill = next(f for f in runner.fills if f.status == "EXIT")
    assert fill.price == Decimal(90) and fill.reason == "STOP_GAP"
    assert runner.portfolio.realized_pnl == Decimal(-120)


def test_vwap_gap_does_not_change_sums_or_count_as_a_bar_and_unknown_disables_signal():
    from rsys.strategies.models import CompletedBar, VwapConfig, VwapState
    from rsys.strategies.vwap import VwapStrategy

    def bar(minute, price):
        return CompletedBar(
            minute * 60_000, (minute + 1) * 60_000, *[Decimal(price)] * 4, Decimal(10)
        )

    strategy = VwapStrategy("AAPL", VwapConfig(warmup_bars=2))
    strategy.on_completed_bar(bar(0, 100), is_candidate=True)
    strategy.on_missing_minute(60_000, known=True)
    assert strategy.cumulative_volume == 10 and strategy.cumulative_pv == 1000
    assert strategy._bar_count == 1
    strategy.on_completed_bar(bar(2, 102), is_candidate=True)
    assert strategy.state == VwapState.IDLE
    assert strategy.cumulative_volume == 20 and strategy.cumulative_pv == 2020
    assert strategy._vwap_history[-1] == Decimal(101)
    strategy.on_missing_minute(180_000, known=False)
    assert strategy.state == VwapState.EXPIRED


def test_unknown_empty_session_is_retained_and_does_not_prevent_later_sessions(
    tmp_path,
):
    _, config = sparse_cache(
        tmp_path, gaps={"2024-11-29": list(range(210))}, enrich=False
    )
    summary, _, trace, _ = backtest(
        config, json.dumps(config).encode(), tmp_path / "runs"
    )
    assert [s["status"] for s in summary["sessions"]] == ["success", "success"]
    assert (
        len(
            [
                r
                for r in trace.read_text().splitlines()
                if json.loads(r)["kind"] == "missing_minute"
            ]
        )
        == 210
    )
    assert summary["result_quality"] == "inconclusive_missing_data"


def test_vwap_can_start_after_an_explained_opening_gap(tmp_path):
    _, config = sparse_cache(tmp_path, gaps={"2024-11-27": [0], "2024-11-29": [0]})
    config["strategy"] = {"strategy": "vwap_touch_hold_v1", "strategy_config": {}}
    summary, _, _, _ = backtest(config, json.dumps(config).encode(), tmp_path / "runs")
    assert all(s["status"] == "success" for s in summary["sessions"])
    assert summary["result_quality"] == "estimate"


def test_feed_and_complete_trade_pagination_are_enforced():
    request = request_identity("AAPL", date(2024, 11, 29), "iex", "-")
    minute = request["start"]
    with pytest.raises(ValueError, match="different exchange"):
        classify_gap(gap_pages([{**trade(minute), "x": "Q"}]), request, minute)
    # Independent 7+11=18; repeated boundary row is not counted twice.
    rows = [trade(minute), trade(minute, identity=2, size=11)]
    calls = []

    def response(params):
        calls.append(dict(params))
        if "page_token" in params:
            return gap_pages(rows)[0]
        return {"trades": {"AAPL": rows[:1]}, "next_page_token": "second"}

    client = AlpacaTradesClient(transport=response)
    pages = list(client.pages(trade_request(request, minute)))
    result = classify_gap(pages, request, minute)
    assert result["volume"] == "18" and result["trade_count"] == 2
    assert len(calls) == 2 and calls[1]["page_token"] == "second"
    assert all(p["feed"] == "iex" and p["symbols"] == "AAPL" for p in calls)


def test_future_gap_mutation_preserves_earlier_decisions_with_visible_volume_negative_control(
    tmp_path,
):
    from rsys.application.batch import SessionBatch

    _, config = sparse_cache(
        tmp_path, gaps={"2024-11-27": [5], "2024-11-29": [5, 19], "2024-12-02": [5]}
    )
    resolved = prepare_backtest(config, [])
    # Prefix includes a real position entry, before the 09:49 missing minute completes.
    cutoff = (9 * 3600 + 49 * 60) * 1000

    def prefix(value):
        batch = SessionBatch(value, allow_historical=True)
        batch.run()
        return [
            r
            for r in batch.trace
            if r["session_date"] == "2024-11-29" and r["available_at_ms"] <= cutoff
        ]

    original = prefix(resolved)
    assert any(r["kind"] == "fill" and r["status"] == "FILLED" for r in original)
    future = deepcopy(resolved)
    gap = future["historical_source"]["gaps_by_session"]["2024-11-29"]["AAPL"][-1]
    assert gap["minute"] == "2024-11-29T14:49:00+00:00"
    gap.update(status="UNKNOWN", volume=None)
    future["scanner"]["history"]["AAPL"] = [
        r
        for r in future["scanner"]["history"]["AAPL"]
        if not (r["session_date"] == "2024-11-29" and r["minute_index"] == 19)
    ]
    assert prefix(future) == original
    visible = deepcopy(resolved)
    observed = next(
        r
        for r in visible["scanner"]["history"]["AAPL"]
        if r["session_date"] == "2024-11-29" and r["minute_index"] == 5
    )
    observed["volume"] = "1007"
    changed = prefix(visible)
    assert changed != original
    before = next(
        r
        for r in original
        if r["kind"] == "decision"
        and r["available_at_ms"] == (9 * 3600 + 46 * 60) * 1000 + 250
    )
    after = next(
        r
        for r in changed
        if r["kind"] == "decision" and r["available_at_ms"] == before["available_at_ms"]
    )
    assert (
        Decimal(after["scanner_evidence"]["symbols"]["AAPL"]["current_volume"])
        - Decimal(before["scanner_evidence"]["symbols"]["AAPL"]["current_volume"])
        == 1000
    )


def test_public_cli_backtest_report_and_compare_label(tmp_path):
    import subprocess
    import sys

    from rsys.reporting.html import render_report_for_attempt
    from rsys.research.comparison import compare_run_pair

    _, config = sparse_cache(tmp_path)
    path = tmp_path / "backtest.json"
    path.write_text(json.dumps(config))
    out = tmp_path / "runs"
    command = [
        sys.executable,
        "-m",
        "rsys.cli",
        "backtest",
        "--config",
        str(path),
        "--out",
        str(out),
        "--report",
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "IEX-based estimates" in result.stdout
    manifest = next(out.glob("attempts/*/manifest.json"))
    run_id = manifest.parent.name
    assert (
        compare_run_pair(out, run_id, run_id)["estimate_label"] == "IEX-based estimates"
    )
    # Unknown gap reports remain viewable without making them scoreable.
    _, unknown = sparse_cache(tmp_path / "unknown", enrich=False)
    _, _, _, manifest = backtest(unknown, json.dumps(unknown).encode(), out)
    report = tmp_path / "diagnostic.html"
    assert render_report_for_attempt(manifest.parent, report) == report
    assert "INCONCLUSIVE" in report.read_text()


def test_gap_enrichment_obeys_holdout_before_either_transport(tmp_path):
    acquisition, _ = inputs(tmp_path)
    acquisition.update(gap_evidence="trades", start="2024-12-03", end="2024-12-03")

    def denied(params):
        pytest.fail("transport called before holdout authorization")

    with pytest.raises(ValueError):
        download(
            acquisition,
            tmp_path / "acquisition",
            client=AlpacaBarsClient(transport=denied),
            gap_client=AlpacaTradesClient(transport=denied),
        )
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("enrich", [True, False])
def test_experiment_scorecards_label_estimates_and_count_unscoreable_attempts(
    tmp_path, enrich
):
    from rsys.application.experiment import run_experiment

    _, config = sparse_cache(tmp_path, gaps={"2024-11-29": [19]}, enrich=enrich)
    config["sessions"] = ["2024-11-29"]
    del config["objects"]["2024-12-02"]
    (tmp_path / "base.json").write_text(json.dumps(config))
    plan = {
        "schema_version": 1,
        "study_id": "sparse",
        "hypothesis": "Synthetic sparse exit comparison",
        "study_kind": "exit",
        "base_config": "base.json",
        "variants": [
            {
                "name": "early",
                "changes": [
                    {
                        "path": ["strategy", "strategy_config", "target_r"],
                        "value": "0.1",
                    }
                ],
            }
        ],
        "permitted_axis": "strategies",
        "trial_limit": 2,
        "counting": "all_execution_attempts",
        "failure_policy": "continue",
        "external_attempts": [],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    result, manifest = run_experiment(path, tmp_path / "experiment")
    assert result["estimate_label"] == "IEX-based estimates"
    assert len(result["registry_trials"]) == 2
    assert all(a["state"] == "success" for a in result["arms"])
    assert all(
        a["scorecard"]["status"] == ("available" if enrich else "unavailable")
        for a in result["arms"]
    )
    assert result["status"] == ("success" if enrich else "failed")
    assert "IEX-based estimates" in (manifest.parent / "report.html").read_text()


def test_a_late_pre_timer_open_cannot_fill_the_requested_exit():
    from dataclasses import replace

    from test_standalone_run import _bar, _build_runner, _ms

    from rsys.execution.models import ExecutionCosts, ExecutionPolicy

    runner, session = _build_runner()
    session["bars"]["AAA"][-1] = replace(
        session["bars"]["AAA"][-1],
        bar_start_ms=_ms("2024-11-29T09:47:00"),
        available_at_ms=_ms("2024-11-29T09:48:00"),
    )
    for m in (50, 51):
        session["bars"]["AAA"].append(
            _bar(
                {
                    "bar_start": f"2024-11-29T09:{m}:00",
                    "available_at": f"2024-11-29T09:{m + 1}:00",
                    "open": "103",
                    "high": "103",
                    "low": "103",
                    "close": "103",
                    "volume": "100",
                }
            )
        )
    runner.costs = ExecutionCosts(feed_delay_ms=1000)
    runner.execution = ExecutionPolicy(flatten_at_ms=_ms("2024-11-29T09:50:00") + 500)
    runner.run_session(session["bars"], session["scanner"])
    fill = next(f for f in runner.fills if f.status == "EXIT")
    assert fill.event_time_ms == _ms("2024-11-29T09:51:00")
    assert fill.available_at_ms == _ms("2024-11-29T09:51:00") + 1000

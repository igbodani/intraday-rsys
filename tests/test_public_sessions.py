"""Public bar/trade sessions, retained failures, evidence and research-policy boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

from rsys.batch_config import validate_batch
from rsys.batch_run import SessionBatch
from rsys.compare_cli import CompareCliError, compare_run_pair
from rsys.evaluation import ComparisonError
from rsys.risk_policy import ExecutionCosts, RiskMonitor, RiskPolicy
from rsys.session_config import RunConfigError
from rsys.storage import RunStorage

ROOT = Path(__file__).resolve().parents[1]


def config(mode="trade"):
    return json.loads((ROOT / "fixtures" / f"sessions_{mode}_fixture.json").read_text())


def invoke(tmp_path, cfg, *, name="input"):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(cfg))
    result = subprocess.run(
        [sys.executable, "-m", "rsys.cli", "run", "--config", str(path), "--out", str(tmp_path / "runs"), "--report"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    attempts = sorted((tmp_path / "runs" / "attempts").iterdir(), key=lambda p: p.name)
    attempt = attempts[-1]
    return result, attempt, json.loads((attempt / "manifest.json").read_text())


@pytest.mark.parametrize("mode,ending,quantity,entry,exit_price", [
    ("bar", "1022.34800", "8", "102.55125", "103.9480"),
    ("trade", "1034.14420", "9", "102.15105", "104.04795"),
])
def test_public_two_session_runs_reconcile_costs_and_preserve_complete_evidence(
    tmp_path, mode, ending, quantity, entry, exit_price
):
    cfg = config(mode)
    result, attempt, manifest = invoke(tmp_path, cfg)
    assert result.returncode == 0, result.stderr
    assert manifest["status"] == "success"
    assert D(manifest["ending_equity"]) == D(ending)
    assert manifest["metrics"]["completed_trade_count"] == 2
    assert [s["status"] for s in manifest["sessions"]] == ["success", "success"]
    assert manifest["open_positions"] == {}
    for session in manifest["sessions"]:
        buy, sell = session["fills"]
        assert buy["quantity"] == sell["quantity"] == quantity
        assert D(buy["price"]) == D(entry)
        assert D(sell["price"]) == D(exit_price)
        assert buy["available_at_ms"] - buy["event_time_ms"] == 250
        assert sell["available_at_ms"] - sell["event_time_ms"] == 250
        assert D(session["metrics"]["realized_pnl"]) == D(sell["net_pnl"])
        assert buy["reference_id"] and sell["reference_id"]
        assert D(buy["execution_cost"]) > 0 and D(sell["execution_cost"]) > 0
        assert D(buy["estimated_stop_loss"]) <= D(buy["risk_budget"])
        assert D(buy["risk_budget"]) == D(session["starting_equity"]) * D('.03')
        assert (attempt / "sessions" / f"{session['session_date']}.json").exists()
    assert manifest["sessions"][1]["starting_equity"] == manifest["sessions"][0]["ending_equity"]
    assert json.loads((attempt / "input.json").read_text()) == cfg
    resolved = json.loads((attempt / "resolved_config.json").read_text())
    assert resolved["sessions"][0]["symbols"]["AAA"]["strategy_config"]["target_r"] == "2"
    assert json.loads((attempt / "fills.json").read_text()) == manifest["fills"]
    assert (attempt / "metrics.json").exists()
    report = (attempt / "report.html").read_text()
    assert all(day in report for day in ("2024-11-29", "2024-12-02"))
    assert "Decisions (40)" in report if mode == "bar" else "Decisions (38)" in report
    again, _, second = invoke(tmp_path, cfg, name="again")
    assert again.returncode == 0
    assert second["run_id"] != manifest["run_id"]
    assert second["semantic_trace_hash"] == manifest["semantic_trace_hash"]
    assert compare_run_pair(tmp_path / "runs", manifest["run_id"], second["run_id"])["ending_equity_delta"] == "0.00000"


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_later_runtime_failure_retains_prior_session_partial_fills_and_report(tmp_path, mode):
    cfg = config(mode)
    second = cfg["sessions"][1]
    if mode == "trade":
        next(t for t in second["symbols"]["AAA"]["trades"] if "09:47:15" in t["event_time"])["quality_ok"] = False
    else:
        second["symbols"]["AAA"]["bars"] = second["symbols"]["AAA"]["bars"][:-2]
    third = deepcopy(cfg["sessions"][0])
    third = json.loads(json.dumps(third).replace("2024-11-29", "2024-12-03"))
    cfg["sessions"].append(third)
    result, attempt, manifest = invoke(tmp_path, cfg)
    assert result.returncode == (4 if mode == "trade" else 3), result.stderr
    assert manifest["status"] == "failed"
    assert manifest["metrics_scope"] == "partial_attempt"
    assert manifest["run_id"] in result.stderr
    assert str(attempt / "manifest.json") in result.stderr
    assert [s["status"] for s in manifest["sessions"]] == ["success", "failed", "not_started"]
    assert manifest["sessions"][0]["metrics"]["completed_trade_count"] == 1
    assert any(f["status"] == "FILLED" for f in manifest["sessions"][1]["fills"])
    assert "partial diagnostics" in (attempt / "report.html").read_text()
    assert json.loads((attempt / "input.json").read_text()) == cfg
    with pytest.raises(CompareCliError, match="successful"):
        compare_run_pair(tmp_path / "runs", manifest["run_id"], manifest["run_id"])
    storage = RunStorage(tmp_path / "runs")
    try:
        record = storage.load_record(manifest["run_id"])
        assert record.status == "failed" and record.error
        assert str(attempt / "report.html") in record.artifacts
    finally:
        storage.close()


@pytest.mark.parametrize("change", [
    lambda c: c["risk"].pop("daily_loss_basis"),
    lambda c: c["costs"].pop("feed_delay_ms"),
    lambda c: c["risk"].update(per_trade_fraction=True),
    lambda c: c["risk"].update(maximum_drawdown_fraction="1"),
    lambda c: c["costs"].update(adverse_bps="NaN"),
    lambda c: c["costs"].update(feed_delay_ms=-1),
    lambda c: c["costs"].update(feed_delay_ms=86_400_000),
    lambda c: c["execution"].update(decision_latency_ms=True),
    lambda c: c["sessions"].reverse(),
    lambda c: c["sessions"].append(deepcopy(c["sessions"][0])),
    lambda c: c.update(evidence_type="historical"),
    lambda c: c["sessions"][0].update(date="2025-01-02"),
    lambda c: c["sessions"][0]["symbols"]["AAA"]["trades"][0].update(size="0"),
    lambda c: c["sessions"][0]["symbols"]["AAA"]["trades"][0].update(available_at="2024-11-29T09:30:00-04:00"),
])
def test_invalid_new_configuration_is_rejected_before_execution_and_retained(tmp_path, change):
    cfg = config()
    change(cfg)
    result, attempt, manifest = invoke(tmp_path, cfg)
    assert result.returncode == 2, result.stderr
    assert manifest["status"] == "failed"
    assert (attempt / "trace.jsonl").read_text() == ""
    assert (attempt / "input.json").exists() and (attempt / "report.html").exists()


def test_unknown_credentials_are_redacted_even_on_invalid_attempt(tmp_path):
    cfg = config()
    cfg["apiKey"] = "do-not-retain-this-value"
    result, attempt, _ = invoke(tmp_path, cfg)
    assert result.returncode == 2
    for artifact in attempt.rglob("*"):
        if artifact.is_file():
            assert "do-not-retain-this-value" not in artifact.read_text()


@pytest.mark.parametrize("artifact", ["fills.json", "resolved_config.json", "metrics.json", "sessions/2024-11-29.json", "report.html"])
def test_comparison_verifies_every_retained_artifact(tmp_path, artifact):
    result, attempt, manifest = invoke(tmp_path, config())
    assert result.returncode == 0
    (attempt / artifact).write_text("tampered")
    with pytest.raises(CompareCliError, match="integrity"):
        compare_run_pair(tmp_path / "runs", manifest["run_id"], manifest["run_id"])


def test_comparison_distinguishes_risk_cost_and_latency_policies(tmp_path):
    cfg = config()
    _, _, first = invoke(tmp_path, cfg)
    cfg["risk"]["per_trade_fraction"] = "0.02"
    _, _, second = invoke(tmp_path, cfg, name="risk")
    with pytest.raises(ComparisonError, match="risk_profile"):
        compare_run_pair(tmp_path / "runs", first["run_id"], second["run_id"])
    assert "risk_profile" in compare_run_pair(tmp_path / "runs", first["run_id"], second["run_id"], permitted_difference="risk_profile")["assumption_diffs"]
    cfg["costs"]["adverse_bps"] = "10"
    _, _, third = invoke(tmp_path, cfg, name="cost")
    with pytest.raises(ComparisonError, match="cost_profile"):
        compare_run_pair(tmp_path / "runs", first["run_id"], third["run_id"], permitted_difference="risk_profile")


def monitor():
    return RiskMonitor(RiskPolicy(D('.03'), D('.05'), D('.20'), 'session_start_equity', 'cancel_entries_keep_exits'), D(1000))


def test_marked_equity_daily_boundary_latches_until_new_session():
    risk = monitor()
    risk.begin_session("2024-11-29", D(1000))
    assert risk.observe(D('950.01')) is None
    assert risk.observe(D(950)) == "DAILY_LOSS_HALT"
    assert risk.observe(D(1100)) == "DAILY_LOSS_HALT"
    risk.begin_session("2024-12-02", D(1100))
    assert risk.halt_reason is None and risk.budget == 33
    assert risk.peak_equity == 1100


def test_account_drawdown_uses_all_time_peak_and_survives_new_sessions():
    risk = monitor()
    risk.begin_session("2024-11-29", D(1000))
    risk.observe(D(1200))
    assert risk.budget == 36
    assert risk.observe(D('960.01')) is None
    assert risk.observe(D(960)) == "ACCOUNT_DRAWDOWN_HALT"
    risk.begin_session("2024-12-02", D(960))
    assert risk.halt_reason == "ACCOUNT_DRAWDOWN_HALT"
    assert risk.peak_equity == 1200


@pytest.mark.parametrize("account_halt", [False, True])
def test_real_trade_runner_halts_entries_cancels_pending_and_preserves_exits(account_halt):
    from datetime import datetime, timedelta

    cfg = config()
    cfg["costs"].update(adverse_bps="0", feed_delay_ms=0)
    cfg["initial_cash"] = "1000" if account_halt else "10000"
    cfg["sizing"]["max_quantity"] = None if account_halt else "10"
    day = cfg["sessions"][0]
    if not account_halt:
        b = deepcopy(day["symbols"]["AAA"])
        for t in b["trades"]:
            if t["event_time"].split("T")[1] >= "09:46:00":
                t["available_at"] = (datetime.fromisoformat(t["available_at"]) + timedelta(seconds=90)).isoformat()
        day["symbols"]["BBB"] = b
        day["scanner_inputs"]["09:46:00"]["BBB"] = {"rvol": "1", "gain": ".03"}
    for t in day["symbols"]["AAA"]["trades"]:
        if t["event_time"].split("T")[1] >= "09:47:00":
            t["price"] = "70" if account_halt else "40"
    batch = SessionBatch(cfg)
    batch.run()
    first, second = batch.sessions
    reason = "ACCOUNT_DRAWDOWN_HALT" if account_halt else "DAILY_LOSS_HALT"
    assert first["risk_state"]["halt_reason"] == reason
    assert any(f["status"] == "EXIT" for f in first["fills"])
    assert batch.portfolio.positions == {}
    assert batch.portfolio.reserved_cash == 0
    if account_halt:
        assert not any(f["status"] == "FILLED" for f in second["fills"])
        assert any(reason in d["rejected"].values() for d in second["decisions"])
        assert second["risk_state"]["account_halted"]
    else:
        assert any(f["symbol"] == "BBB" and f["status"] == reason for f in first["fills"])
        assert not any(f["symbol"] == "BBB" and f["status"] == "FILLED" for f in first["fills"])
        assert second["risk_state"]["halt_reason"] is None
        assert any(f["status"] == "FILLED" for f in second["fills"])


def test_positive_decision_latency_and_feed_delay_are_distinct():
    cfg = config()
    cfg["execution"]["decision_latency_ms"] = 20000
    batch = SessionBatch(cfg)
    batch.run()
    fill = batch.sessions[0]["fills"][0]
    assert fill["event_time_ms"] == (9 * 3600 + 46 * 60 + 30) * 1000
    assert fill["available_at_ms"] == fill["event_time_ms"] + 250


def test_bar_feed_delay_cannot_retroactively_fill_the_decision_minutes_open():
    cfg = config("bar")
    delayed = SessionBatch(cfg)
    delayed.run()
    cfg["costs"]["feed_delay_ms"] = 0
    immediate = SessionBatch(cfg)
    immediate.run()
    assert delayed.sessions[0]["fills"][0]["event_time_ms"] == (9 * 3600 + 47 * 60) * 1000
    assert immediate.sessions[0]["fills"][0]["event_time_ms"] == (9 * 3600 + 46 * 60) * 1000


def test_fixed_fees_and_adverse_costs_reduce_equity_exactly_once():
    cfg = config("bar")
    cfg["sizing"]["max_quantity"] = "1"
    cfg["costs"].update(entry_fee="1", exit_fee="2")
    batch = SessionBatch(cfg)
    batch.run()
    # Each session: 103.948 - 102.55125 - 1 - 2 = -1.60325.
    assert batch.portfolio.total_cash == D('996.79350')
    assert batch.portfolio.realized_pnl == D('-3.20650')
    assert ExecutionCosts(D(5)).buy_price(D(100)) == D('100.05')
    assert ExecutionCosts(D(5)).sell_price(D(100)) == D('99.95')


def test_new_schema_never_accepts_an_undeclared_absolute_risk_override():
    cfg = config()
    cfg["sizing"]["risk_budget"] = "999"
    with pytest.raises(RunConfigError, match="unsupported fields"):
        validate_batch(cfg)


def test_zero_trade_batch_is_a_success_with_explicit_zero_metrics(tmp_path):
    cfg = config()
    for session in cfg["sessions"]:
        session["scanner_inputs"] = {}
    result, _, manifest = invoke(tmp_path, cfg)
    assert result.returncode == 0
    assert manifest["metrics"]["completed_trade_count"] == 0
    assert manifest["metrics"]["expectancy"] == "UNAVAILABLE_NO_TRADES"
    assert manifest["multi_session_metrics"]["exposure"]["fraction"] == "0"


def test_bar_loss_halt_cancels_same_time_acceptance_before_entry_phase():
    cfg = config("bar")
    cfg["costs"].update(adverse_bps="0", feed_delay_ms=0)
    cfg["initial_cash"] = "10000"
    cfg["sizing"]["max_quantity"] = "10"
    day = cfg["sessions"][0]
    second = deepcopy(day["symbols"]["AAA"])
    for b in second["bars"]:
        if "09:45:00" in b["bar_start"]:
            b.update(open="100", high="100", low="100", close="100")
        if "09:46:00" in b["bar_start"]:
            b.update(open="100", high="102", low="100", close="101.02")
    day["symbols"]["BBB"] = second
    day["scanner_inputs"]["09:47:00"] = {"BBB": {"rvol": "2", "gain": ".03"}}
    for b in day["symbols"]["AAA"]["bars"]:
        if b["bar_start"].split("T")[1] >= "09:47:00":
            b.update(open="40", high="40", low="40", close="40")
    batch = SessionBatch(cfg)
    batch.run()
    first = batch.sessions[0]
    assert any("BBB" in d["accepted"] for d in first["decisions"])
    assert any(f["symbol"] == "BBB" and f["status"] == "DAILY_LOSS_HALT" for f in first["fills"])
    assert not any(f["symbol"] == "BBB" and f["status"] == "FILLED" for f in first["fills"])
    assert first["risk_state"]["daily_halted"]
    assert batch.portfolio.reserved_cash == 0


def test_execution_cost_does_not_make_a_through_stop_reference_eligible():
    cfg = config("bar")
    cfg["sessions"] = cfg["sessions"][:1]
    for b in cfg["sessions"][0]["symbols"]["AAA"]["bars"]:
        if "09:47:00" in b["bar_start"]:
            b.update(open="99", high="99", low="99", close="99")
    batch = SessionBatch(cfg)
    batch.run()
    assert any(f["status"] == "ENTRY_THROUGH_STOP" for f in batch.sessions[0]["fills"])
    assert not any(f["status"] == "FILLED" for f in batch.sessions[0]["fills"])


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_future_session_changes_do_not_change_prior_session_evidence(mode):
    cfg = config(mode)
    first = SessionBatch(cfg)
    first.run()
    second = deepcopy(cfg)
    source = second["sessions"][1]["symbols"]["AAA"]
    if mode == "trade":
        for t in source["trades"]:
            t["price"] = str(D(t["price"]) * 2)
    else:
        for b in source["bars"]:
            for k in ("open", "high", "low", "close"):
                b[k] = str(D(b[k]) * 2)
    changed = SessionBatch(second)
    changed.run()
    assert first.sessions[0] == changed.sessions[0]
    assert [r for r in first.trace if r["session_date"] == "2024-11-29"] == [
        r for r in changed.trace if r["session_date"] == "2024-11-29"
    ]
    assert first.sessions[1] != changed.sessions[1]


def test_explicit_final_open_snapshot_is_diagnostic_and_cannot_carry_between_sessions():
    cfg = config()
    cfg["execution"]["max_hold_ms"] = None
    for session in cfg["sessions"]:
        session["flatten_at"] = None
    cfg["completion_policy"] = "allow_final_open_snapshot"
    with pytest.raises(ValueError, match="open positions"):
        SessionBatch(cfg).run()
    cfg["sessions"] = cfg["sessions"][:1]
    batch = SessionBatch(cfg)
    batch.run()
    assert batch.summary()["open_positions"]
    assert batch.summary()["metrics"]["completed_trade_count"] == 0


def test_batch_report_escapes_symbols_and_errors(tmp_path):
    cfg = config()
    label = '<script>alert("x")</script>'
    for session in cfg["sessions"]:
        session["symbols"][label] = session["symbols"].pop("AAA")
        session["scanner_inputs"]["09:46:00"][label] = session["scanner_inputs"]["09:46:00"].pop("AAA")
    result, attempt, _ = invoke(tmp_path, cfg)
    assert result.returncode == 0
    report = (attempt / "report.html").read_text()
    assert '<script>' not in report and '&lt;script&gt;' in report


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_vwap_composes_through_both_public_input_modes(mode):
    from datetime import datetime, timedelta

    cfg = config(mode)
    vwap = json.loads((ROOT / 'fixtures' / 'standalone_vwap_fixture.json').read_text())
    source_bars = vwap['symbols']['AAA']['bars']
    for minute in (55, 56, 57):
        source_bars.append({
            'bar_start': f'2024-11-29T09:{minute}:00', 'bar_end': f'2024-11-29T09:{minute+1}:00',
            'available_at': f'2024-11-29T09:{minute+1}:00', 'open': '103', 'high': '103.1',
            'low': '102.9', 'close': '103', 'volume': '100',
        })
    for session in cfg['sessions']:
        day = session['date']
        bars = json.loads(json.dumps(source_bars).replace('2024-11-29', day))
        spec = {'strategy': 'vwap_touch_hold_v1', 'session_open': '09:30:00'}
        if mode == 'bar':
            spec['bars'] = bars
        else:
            spec['trades'] = []
            for i, bar in enumerate(bars):
                for j, field in enumerate(('open', 'high', 'low', 'close')):
                    point = (datetime.fromisoformat(bar['bar_start']) + timedelta(seconds=15*j)).isoformat()
                    spec['trades'].append({
                        'trade_id': f'{i}:{j}', 'event_time': point, 'available_at': point,
                        'price': bar[field], 'size': str(D(bar['volume']) / 4), 'quality_ok': True,
                    })
        session['symbols'] = {'AAA': spec}
        session['scanner_inputs'] = deepcopy(vwap['scanner_inputs'])
        session['flatten_at'] = '09:56:05'
    batch = SessionBatch(cfg)
    batch.run()
    assert batch.summary()['metrics']['completed_trade_count'] == 2
    assert batch.portfolio.positions == {}
    assert all(s['status'] == 'success' for s in batch.sessions)


def test_handled_interruption_keeps_manifest_record_and_report_consistent(tmp_path, monkeypatch):
    from rsys.run_cli import run_cli_entry
    from rsys.trade_input_run import TradeInputRunner

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(TradeInputRunner, 'run_session', interrupt)
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(config()))
    with pytest.raises(KeyboardInterrupt):
        run_cli_entry(path, tmp_path / 'runs', report=True)
    attempt = next((tmp_path / 'runs' / 'attempts').iterdir())
    manifest = json.loads((attempt / 'manifest.json').read_text())
    assert manifest['status'] == 'interrupted'
    assert manifest['sessions'][0]['status'] == 'interrupted'
    assert manifest['sessions'][1]['status'] == 'not_started'
    storage = RunStorage(tmp_path / 'runs')
    try:
        assert storage.load_record(manifest['run_id']).status == 'interrupted'
    finally:
        storage.close()
    assert 'partial diagnostics' in (attempt / 'report.html').read_text()


def test_session_clock_cannot_rewind_after_an_oversized_timer():
    cfg = config()
    cfg['execution']['max_hold_ms'] = 10 * 24 * 3600 * 1000
    batch = SessionBatch(cfg)
    with pytest.raises(ValueError, match='clock overlaps'):
        batch.run()
    assert batch.sessions[0]['status'] == 'success'
    assert batch.sessions[1]['status'] == 'failed'
    assert batch.sessions[1].get('fills', []) == []


@pytest.mark.parametrize("error_type,status", [(RuntimeError, "failed"), (KeyboardInterrupt, "interrupted")])
def test_construction_failure_retains_input_and_rethrows(tmp_path, monkeypatch, error_type, status):
    from rsys import batch_run
    from rsys.run_cli import run_cli_entry

    def fail_construction(*args, **kwargs):
        raise error_type("construction stopped")

    monkeypatch.setattr(batch_run, "SessionBatch", fail_construction)
    cfg = config()
    path = tmp_path / "input.json"
    path.write_text(json.dumps(cfg))
    with pytest.raises(error_type, match="construction stopped"):
        run_cli_entry(path, tmp_path / "runs", report=True)
    attempt = next((tmp_path / "runs" / "attempts").iterdir())
    manifest = json.loads((attempt / "manifest.json").read_text())
    assert manifest["status"] == status
    assert manifest["error"]["type"] == error_type.__name__
    assert json.loads((attempt / "input.json").read_text()) == cfg
    assert json.loads((attempt / "fills.json").read_text()) == []
    assert "partial diagnostics" in (attempt / "report.html").read_text()
    storage = RunStorage(tmp_path / "runs")
    try:
        assert storage.load_record(manifest["run_id"]).status == status
    finally:
        storage.close()

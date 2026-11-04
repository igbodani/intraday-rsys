"""Finite studies: independent accounting, failure, isolation and budget checks."""

import json
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest
from test_alpaca_pipeline import ROOT, cached

from rsys.application import experiment as service
from rsys.application.experiment_config import patch_axis
from rsys.data.bar_cache import BarCache
from rsys.reporting.experiment import scorecard_metrics
from rsys.research.errors import InspectionError, StudyBudgetError
from rsys.research.inspection import inspect_attempt
from rsys.research.storage import RunStorage
from rsys.research.studies import StudyRegistry


@pytest.fixture
def prepared(tmp_path):
    _, base = cached(tmp_path)
    base["sessions"] = ["2024-11-29"]
    del base["objects"]["2024-12-02"]
    (tmp_path / "base.json").write_text(json.dumps(base))
    plan = {
        "schema_version": 1,
        "study_id": "fixture",
        "hypothesis": "Declared synthetic exit test",
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
        "trial_limit": 4,
        "counting": "all_execution_attempts",
        "failure_policy": "continue",
        "external_attempts": [],
    }
    return tmp_path, base, plan


def execute(prepared, plan=None, out="out"):
    root, _, original = prepared
    path = root / "plan.json"
    path.write_text(json.dumps(original if plan is None else plan))
    return service.run_experiment(path, root / out)


def retained(root):
    paths = list((root / "out/experiments").glob("*/experiment.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text()), paths[0].parent


def test_real_runner_exact_results_frozen_child_links_and_budget_across_reruns(
    prepared,
):
    root, _, _ = prepared
    result, manifest = execute(prepared)
    assert result["status"] == "success" and result["promotion_status"] == "unscored"
    assert [a["state"] for a in result["arms"]] == ["success", "success"]
    # Independent fixture: 8 shares, entry 102.5*1.0005, timed exit 104*.9995;
    # zero declared fees: 8*(103.9480-102.55125) = 11.17400.
    # Variant target 102.91*.9995 gives 8*(102.858545-102.55125) = 2.458360.
    assert Decimal(
        result["arms"][0]["scorecard"]["metrics"]["net_change_currency"]
    ) == Decimal("11.17400")
    assert Decimal(
        result["arms"][1]["scorecard"]["metrics"]["net_change_currency"]
    ) == Decimal("2.458360")
    storage = RunStorage(root / "out", read_only=True)
    try:
        assert len(storage.list_run_ids()) == 2
        for arm in result["arms"]:
            record = storage.load_record(arm["run_id"])
            context = record.resolved_config["experiment"]
            assert context["trial_id"] == arm["trial_id"]
            assert context["parent_id"] == record.parent_run_id == manifest.parent.name
            assert context["code_hash"] == record.code_hash == result["code_hash"]
            assert record.hypothesis == "Declared synthetic exit test"
            assert arm["invocation_id"] in Path(arm["manifest"]).read_text()
    finally:
        storage.close()
    assert inspect_attempt(root / "out", manifest.parent.name).outcome == "success"
    again, _ = execute(prepared)
    assert len(again["registry_trials"]) == 4
    exhausted, _ = execute(prepared)
    assert exhausted["status"] == "failed"
    assert [r["state"] for r in exhausted["arms"]] == [
        "blocked_budget",
        "blocked_budget",
    ]
    assert len(exhausted["registry_trials"]) == 4
    assert all(r["scorecard"]["status"] == "unavailable" for r in exhausted["arms"])
    (manifest.parent / "experiment.json").write_text("{}")
    with pytest.raises(InspectionError, match="integrity"):
        inspect_attempt(root / "out", manifest.parent.name)


@pytest.mark.parametrize(
    "defect",
    [
        "unknown",
        "duplicate",
        "axis",
        "path",
        "combined",
        "limit",
        "policy",
        "kind",
        "schema",
        "overlap",
        "late_invalid",
        "final",
        "history",
    ],
)
def test_entire_plan_rejected_before_any_cache_read_or_child(
    prepared, monkeypatch, defect
):
    root, base, plan = prepared
    change = plan["variants"][0]["changes"][0]
    if defect == "unknown":
        plan["unknown"] = 1
    elif defect == "duplicate":
        plan["variants"][0]["name"] = "baseline"
    elif defect == "axis":
        plan["permitted_axis"] = "cost_profile"
    elif defect == "path":
        change["path"] = ["partitions"]
    elif defect == "combined":
        plan["variants"][0]["changes"].append(
            {"path": ["account", "costs", "entry_fee"], "value": "1"}
        )
    elif defect == "limit":
        plan["trial_limit"] = 1
    elif defect == "policy":
        del plan["failure_policy"]
    elif defect == "kind":
        plan["study_kind"] = "auto_optimize"
    elif defect == "schema":
        plan["schema_version"] = True
    elif defect == "overlap":
        plan["variants"][0]["changes"].append(deepcopy(change))
    elif defect == "late_invalid":
        plan["variants"].append(
            {"name": "late_bad", "changes": [{"path": change["path"], "value": "-1"}]}
        )
    elif defect == "final":
        base["partitions"][0]["name"] = "final"
        (root / "base.json").write_text(json.dumps(base))
    else:
        plan.update(study_kind="scanner", permitted_axis="scanner_profile")
        change.update(path=["scanner", "max_lookback_calendar_sessions"], value=2)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid plan touched market data or executed an arm")

    monkeypatch.setattr(BarCache, "__init__", forbidden)
    monkeypatch.setattr(service, "backtest", forbidden)
    with pytest.raises((ValueError, TypeError)):
        execute(prepared)
    result, _ = retained(root)
    assert result["status"] == "failed" and result["plan"]["config"] == plan
    assert not (root / "out/runs.db").exists()


@pytest.mark.parametrize(
    "policy,states",
    [("stop", ["failed", "not_started"]), ("continue", ["failed", "success"])],
)
def test_failures_count_and_are_not_hidden(prepared, monkeypatch, policy, states):
    _root, _, plan = prepared
    plan["failure_policy"] = policy
    actual = service.backtest
    calls = []

    def fail_first(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("synthetic preparation failure")
        return actual(*args, **kwargs)

    monkeypatch.setattr(service, "backtest", fail_first)
    result, _ = execute(prepared)
    assert result["status"] == "failed"
    assert [row["state"] for row in result["arms"]] == states
    assert result["registry_trials"][0]["state"] == "failed"
    assert result["arms"][0]["scorecard"]["status"] == "unavailable"
    assert "metrics" not in result["arms"][0]["scorecard"]
    if policy == "continue":
        assert result["arms"][1]["scorecard"]["metrics"]["completed_trades"] == 1
        assert result["arms"][1]["scorecard"]["comparison"]["status"] == "unavailable"
    monkeypatch.setattr(service, "backtest", actual)
    retry, _ = execute(prepared)
    assert len(retry["registry_trials"]) == (3 if policy == "stop" else 4)
    assert retry["registry_trials"][0]["state"] == "failed"


def test_real_failed_preparation_has_one_trial_linked_to_its_run(prepared, monkeypatch):
    original = BarCache.load
    calls = 0

    def fail_once(self, *args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("corrupt cache fixture")
        return original(self, *args)

    monkeypatch.setattr(BarCache, "load", fail_once)
    result, _ = execute(prepared)
    first = result["arms"][0]
    assert first["state"] == "failed" and first["run_id"]
    assert result["registry_trials"][0]["run_id"] == first["run_id"]
    assert (
        inspect_attempt(prepared[0] / "out", first["invocation_id"]).outcome == "failed"
    )


def test_interrupt_retains_remaining_unstarted_and_reservation(prepared, monkeypatch):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(service, "backtest", interrupt)
    with pytest.raises(KeyboardInterrupt):
        execute(prepared)
    result, attempt = retained(prepared[0])
    assert result["status"] == "interrupted"
    assert [r["state"] for r in result["arms"]] == ["interrupted", "not_started"]
    assert len(result["registry_trials"]) == 1
    assert result["registry_trials"][0]["state"] == "interrupted"
    assert inspect_attempt(prepared[0] / "out", attempt.name).outcome == "interrupted"


def test_network_leak_fails_and_restores_python_socket(prepared, monkeypatch):
    before = socket.getaddrinfo

    def leak(*args, **kwargs):
        socket.getaddrinfo("invalid.test", 443)

    monkeypatch.setattr(service, "backtest", leak)
    result, _ = execute(prepared)
    assert result["status"] == "failed"
    assert all(r["state"] == "failed" for r in result["arms"])
    assert socket.getaddrinfo is before


def test_reservations_race_for_last_slot_and_contract_cannot_reset_budget(tmp_path):
    registry = StudyRegistry(tmp_path)
    registry.register("race", {"fixed": True}, 1, 0)
    registry.close()
    barrier = Barrier(2)

    def reserve(name):
        db = StudyRegistry(tmp_path)
        try:
            barrier.wait()
            try:
                return db.reserve("race", name, "baseline", {}, name)
            except StudyBudgetError:
                return "blocked"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ["a", "b"]))
    assert results.count("blocked") == 1
    db = StudyRegistry(tmp_path)
    try:
        assert len(db.trials("race")) == 1
        with pytest.raises(ValueError, match="frozen"):
            db.register("race", {"fixed": True}, 2, 0)
        with pytest.raises(ValueError, match="frozen"):
            db.register("race", {"fixed": False}, 1, 0)
    finally:
        db.close()


def test_declared_external_attempts_consume_budget(tmp_path):
    db = StudyRegistry(tmp_path)
    try:
        db.register("s", {"external": ["local/manual-1"]}, 2, 1)
        db.reserve("s", "parent", "base", {}, "child")
        with pytest.raises(StudyBudgetError):
            db.reserve("s", "parent", "variant", {}, "other")
    finally:
        db.close()


@pytest.mark.parametrize(
    "kind,path,axis",
    [
        ("entry", ["strategy", "strategy_config", "range_minutes"], "strategies"),
        ("exit", ["strategy", "strategy_config", "target_r"], "strategies"),
        ("exit", ["account", "execution", "max_hold_ms"], "execution_mode"),
        ("costs", ["account", "costs", "feed_delay_ms"], "cost_profile"),
        ("account", ["account", "initial_cash"], "initial_cash"),
        ("complete_system", ["strategy", "strategy"], "strategies"),
    ],
)
def test_declared_parameter_categories(kind, path, axis):
    assert patch_axis(kind, path) == axis
    if kind in ("entry", "exit"):
        with pytest.raises(ValueError):
            patch_axis("exit" if kind == "entry" else "entry", path)


def test_session_end_drawdown_fraction_is_displayed_as_percentage():
    manifest = {
        "metrics": {
            "net_change": "-100",
            "completed_trade_count": 2,
            "win_rate": "0.5",
            "expectancy": "-50",
            "profit_factor": "0.6666666667",
        }
    }
    stored = {
        "multi_session_metrics": {
            "drawdown": {"max_drawdown": "300", "max_drawdown_pct": "0.25"},
            "exposure": {"fraction": "0.5"},
        }
    }
    score = scorecard_metrics(manifest, stored)
    assert score["session_end_drawdown_currency"] == "300"
    assert score["session_end_drawdown_fraction"] == "0.25"
    assert score["session_end_drawdown_percent"] == "25.00"
    assert stored["multi_session_metrics"]["drawdown"]["max_drawdown_pct"] == "0.25"


def test_zero_trade_arm_and_escaped_hypothesis(prepared):
    _, _, plan = prepared
    plan.update(study_kind="entry", hypothesis="<script>fixture</script>")
    plan["variants"][0]["changes"] = [
        {
            "path": ["strategy", "strategy_config", "entry_deadline_ms"],
            "value": 34260000,
        }
    ]
    result, manifest = execute(prepared)
    metrics = result["arms"][1]["scorecard"]["metrics"]
    assert (
        metrics["completed_trades"] == 0
        and Decimal(metrics["net_change_currency"]) == 0
    )
    assert metrics["win_rate_fraction"] == "UNAVAILABLE_NO_TRADES"
    assert metrics["profit_factor_ratio"] == "UNAVAILABLE_NO_TRADES"
    html = (manifest.parent / "report.html").read_text()
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_tampered_arm_and_undeclared_cost_difference_remain_unavailable(
    prepared, monkeypatch
):
    original = service.backtest

    def change(*args, **kwargs):
        out = original(*args, **kwargs)
        if kwargs["experiment_context"].arm == "early":
            out[3].write_text("{}")
        return out

    monkeypatch.setattr(service, "backtest", change)
    result, _ = execute(prepared)
    assert result["status"] == "failed"
    assert result["arms"][1]["state"] == "success"
    assert result["arms"][1]["scorecard"]["status"] == "unavailable"


def test_code_drift_stops_later_arms(prepared, monkeypatch):
    actual = service.provenance
    calls = 0

    def drifting():
        nonlocal calls
        calls += 1
        return actual() if calls < 3 else ("different", "different")

    monkeypatch.setattr(service, "provenance", drifting)
    with pytest.raises(ValueError, match="identity changed"):
        execute(prepared)
    result, _ = retained(prepared[0])
    assert result["status"] == "failed"
    assert result["arms"][0]["state"] == "failed"
    assert result["arms"][1]["state"] == "not_started"


def test_public_cli_runs_plan_and_status(prepared):
    root, _, plan = prepared
    (root / "plan.json").write_text(json.dumps(plan))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "rsys.cli",
            "experiment",
            "--plan",
            str(root / "plan.json"),
            "--out",
            str(root / "out"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "promotion_status: unscored" in completed.stdout
    result, attempt = retained(root)
    assert result["status"] == "success"
    assert inspect_attempt(root / "out", attempt.name).outcome == "success"


def test_scorecard_keeps_loss_zero_trade_and_failure_with_cost_negative_control(
    prepared, monkeypatch
):
    _, _, plan = prepared
    plan.update(study_kind="costs", permitted_axis="cost_profile", trial_limit=4)
    plan["variants"] = [
        {
            "name": "loss",
            "changes": [{"path": ["account", "costs", "exit_fee"], "value": "20"}],
        },
        {
            "name": "none",
            "changes": [{"path": ["account", "costs", "entry_fee"], "value": "1000"}],
        },
        {
            "name": "failure",
            "changes": [{"path": ["account", "costs", "exit_fee"], "value": "21"}],
        },
    ]
    actual = service.backtest

    def failed_arm(*args, **kwargs):
        if kwargs["experiment_context"].arm == "failure":
            raise OSError("synthetic failed attempt")
        return actual(*args, **kwargs)

    monkeypatch.setattr(service, "backtest", failed_arm)
    result, _ = execute(prepared)
    assert result["status"] == "failed" and len(result["registry_trials"]) == 4
    baseline, loss, none, failure = result["arms"]
    assert Decimal(baseline["scorecard"]["metrics"]["net_change_currency"]) == Decimal(
        "11.17400"
    )
    # Fees leave room for two shares within the 30 risk budget:
    # 2*(104*.9995 - 102.5*1.0005) - 20 = -17.20650.
    assert Decimal(loss["scorecard"]["metrics"]["net_change_currency"]) == Decimal(
        "-17.20650"
    )
    assert loss["scorecard"]["metrics"]["win_rate_fraction"] == "0"
    assert none["scorecard"]["metrics"]["completed_trades"] == 0
    assert (
        none["scorecard"]["metrics"]["expectancy_currency_per_trade"]
        == "UNAVAILABLE_NO_TRADES"
    )
    assert (
        failure["scorecard"]["status"] == "unavailable"
        and "metrics" not in failure["scorecard"]
    )
    assert set(loss["scorecard"]["comparison"]["result"]["assumption_diffs"]) == {
        "cost_profile"
    }
    # Same successful evidence, wrong declared axis: actual comparator rejects it.
    service.build_scorecards(result, prepared[0] / "out", "strategies")
    assert loss["scorecard"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "kind,path,value,axis",
    [
        ("costs", ["account", "costs", "feed_delay_ms"], 500, "cost_profile"),
        (
            "complete_system",
            ["strategy", "strategy"],
            "vwap_touch_hold_v1",
            "strategies",
        ),
    ],
)
def test_feed_delay_and_full_system_comparisons(prepared, kind, path, value, axis):
    _, _, plan = prepared
    plan.update(study_kind=kind, permitted_axis=axis)
    plan["variants"][0]["changes"] = [{"path": path, "value": value}]
    result, _ = execute(prepared)
    assert result["status"] == "success"
    assert set(
        result["arms"][1]["scorecard"]["comparison"]["result"]["assumption_diffs"]
    ) == {axis}
    if kind == "complete_system":
        assert result["interpretation"] == "complete-system account comparison"


def test_child_creation_rechecks_frozen_code_identity(prepared, monkeypatch):
    from rsys.research import experiments

    monkeypatch.setattr(
        experiments, "compute_source_fingerprint", lambda _root: ("changed", [])
    )
    result, _ = execute(prepared)
    assert result["status"] == "failed"
    assert all(r["state"] == "failed" and r["run_id"] is None for r in result["arms"])
    assert len(result["registry_trials"]) == 2


def test_parent_manifest_write_failure_cannot_look_successful(prepared, monkeypatch):
    actual = service.atomic_write_json
    calls = 0

    def fail_final(path, value):
        nonlocal calls
        if path.name == "manifest.json":
            calls += 1
            if calls == 2:
                raise OSError("final experiment manifest failure")
        return actual(path, value)

    monkeypatch.setattr(service, "atomic_write_json", fail_final)
    with pytest.raises(OSError, match="final experiment") as caught:
        execute(prepared)
    manifest = caught.value.manifest_path
    assert json.loads(manifest.read_text())["status"] == "failed"
    assert (
        inspect_attempt(prepared[0] / "out", manifest.parent.name).outcome == "failed"
    )


def test_bad_base_retains_original_hash_and_redacted_content(prepared):
    root, base, _ = prepared
    base["account"]["initial_cash"] = "-1"
    (root / "base.json").write_text(json.dumps(base))
    with pytest.raises(ValueError):
        execute(prepared)
    result, _ = retained(root)
    assert result["base"]["sha256"]
    assert result["base"]["config"] == base
    assert not (root / "out/runs.db").exists()

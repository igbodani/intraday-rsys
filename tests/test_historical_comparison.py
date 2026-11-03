"""A declared experiment axis must not be counted again as source provenance."""

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from test_alpaca_pipeline import cached

from rsys.application.historical import backtest
from rsys.research.comparison import assumptions_from_manifest, compare_run_pair
from rsys.research.errors import CompareCliError, ComparisonError
from rsys.research.evaluation import compare_runs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def retained_pair(tmp_path_factory):
    root = tmp_path_factory.mktemp("historical-comparison")
    _, config = cached(root)
    config["sessions"] = ["2024-11-29"]
    del config["objects"]["2024-12-02"]
    first = backtest(config, json.dumps(config).encode(), root / "runs")
    variant = deepcopy(config)
    variant["strategy"]["strategy_config"]["target_r"] = "0.1"
    second = backtest(variant, json.dumps(variant).encode(), root / "runs")
    return root, first, second


def test_public_historical_comparison_accepts_only_the_declared_strategy_change(
    retained_pair,
):
    root, first, second = retained_pair
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rsys.cli",
            "compare",
            "--storage-root",
            str(root / "runs"),
            "--runs",
            first[1],
            second[1],
            "--permitted-difference",
            "strategies",
            "--out",
            str(root / "comparison"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((root / "comparison/comparison.json").read_text())
    assert set(report["assumption_diffs"]) == {"strategies"}
    assert report["metrics_a"]["completed_trade_count"] == 1
    assert report["metrics_b"]["completed_trade_count"] == 1
    assert report["ending_equity_a"] != report["ending_equity_b"]
    with pytest.raises(ComparisonError, match="strategies"):
        compare_run_pair(root / "runs", first[1], second[1])


@pytest.mark.parametrize(
    "axis",
    [
        "scanner_profile",
        "cost_profile",
        "risk_profile",
        "initial_cash",
        "execution_mode",
    ],
)
def test_duplicate_request_settings_are_owned_by_their_comparison_axis(
    retained_pair,
    axis,
):
    _, first, _ = retained_pair
    original = json.loads(first[3].read_text())
    changed = deepcopy(original)
    request = changed["historical_source"]["request"]
    if axis == "scanner_profile":
        changed["top_n"] = request["scanner"]["top_n"] = 2
    elif axis == "cost_profile":
        changed["costs"]["adverse_bps"] = request["account"]["costs"]["adverse_bps"] = (
            "10"
        )
    elif axis == "risk_profile":
        changed["risk"]["per_trade_fraction"] = request["account"]["risk"][
            "per_trade_fraction"
        ] = "0.02"
    elif axis == "initial_cash":
        changed["initial_cash"] = request["account"]["initial_cash"] = "2000"
    else:
        changed["execution"]["flatten_times"]["2024-11-29"] = request["account"][
            "flatten_at"
        ] = "09:49:00"
    differences = compare_runs(
        assumptions_from_manifest(original),
        assumptions_from_manifest(changed),
        permitted_difference=axis,
    )
    assert set(differences) == {axis}


@pytest.mark.parametrize(
    "path,value",
    [
        (("provider",), "other"),
        (("feed",), "iex"),
        (("timing_basis",), "other"),
        (("dataset_hashes", "2024-11-27"), "0" * 64),
        (("request", "dataset", "asof"), "2024-11-29"),
        (("request", "purpose"), "validation"),
        (("request", "partitions"), []),
        (("request", "action_review", "source"), "different action review"),
        (("request", "account", "future_setting"), "unclassified"),
        (("request", "strategy", "future_setting"), "unclassified"),
        (("request", "scanner", "future_setting"), "unclassified"),
        (("future_source_setting",), "unclassified"),
    ],
)
def test_permitted_strategy_change_does_not_hide_source_or_unknown_settings(
    retained_pair,
    path,
    value,
):
    _, first, second = retained_pair
    original = json.loads(first[3].read_text())
    changed = json.loads(second[3].read_text())
    target = changed["historical_source"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ComparisonError, match="evidence_basis"):
        compare_runs(
            assumptions_from_manifest(original),
            assumptions_from_manifest(changed),
            permitted_difference="strategies",
        )


def test_strategy_permission_still_rejects_a_simultaneous_cost_change(retained_pair):
    _, first, second = retained_pair
    original = json.loads(first[3].read_text())
    changed = json.loads(second[3].read_text())
    changed["costs"]["adverse_bps"] = "10"
    changed["historical_source"]["request"]["account"]["costs"]["adverse_bps"] = "10"
    with pytest.raises(ComparisonError, match="cost_profile"):
        compare_runs(
            assumptions_from_manifest(original),
            assumptions_from_manifest(changed),
            permitted_difference="strategies",
        )


def test_feed_delay_is_a_cost_axis_even_when_derived_history_timing_changes(
    retained_pair,
):
    root, first, _ = retained_pair
    original = json.loads(first[3].read_text())
    config = deepcopy(original["historical_source"]["request"])
    config["account"]["costs"]["feed_delay_ms"] = 500
    delayed = backtest(config, json.dumps(config).encode(), root / "runs")
    changed = json.loads(delayed[3].read_text())
    assert changed["scanner_hash"] != original["scanner_hash"]
    result = compare_run_pair(
        root / "runs", first[1], delayed[1], permitted_difference="cost_profile"
    )
    assert set(result["assumption_diffs"]) == {"cost_profile"}
    assert changed["scanner_profile_hash"] == original["scanner_profile_hash"]
    changed["top_n"] = 2
    with pytest.raises(ComparisonError, match="scanner_profile"):
        compare_runs(
            assumptions_from_manifest(original),
            assumptions_from_manifest(changed),
            permitted_difference="cost_profile",
        )


def test_legacy_scanner_fingerprint_and_new_version_fail_closed(retained_pair):
    _, first, _ = retained_pair
    original = json.loads(first[3].read_text())
    original.pop("scanner_profile_version", None)
    original.pop("scanner_profile_hash", None)
    changed = deepcopy(original)
    changed["scanner_hash"] = "different-derived-history"
    with pytest.raises(ComparisonError, match="scanner_profile"):
        compare_runs(
            assumptions_from_manifest(original),
            assumptions_from_manifest(changed),
            permitted_difference="cost_profile",
        )


@pytest.mark.parametrize(
    "version,profile",
    [
        ("future_version", "0" * 64),
        ("historical_scanner_settings_v1", None),
        (None, "0" * 64),
        ("historical_scanner_settings_v1", "invalid"),
    ],
)
def test_unknown_or_incomplete_scanner_profile_is_rejected(
    retained_pair, version, profile
):
    _, first, _ = retained_pair
    manifest = json.loads(first[3].read_text())
    manifest["scanner_profile_version"] = version
    manifest["scanner_profile_hash"] = profile
    with pytest.raises(CompareCliError, match="scanner comparison profile"):
        assumptions_from_manifest(manifest)

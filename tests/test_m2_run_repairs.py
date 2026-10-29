import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

from rsys.run_cli import build_runner, run_synthetic_session


def config():
    return json.loads(
        (Path(__file__).parents[1] / "fixtures/standalone_run_fixture.json").read_text()
    )


def execute(c):
    r, b, s = build_runner(c)
    r.run_session(b, s)
    return r


def test_gap_cannot_create_negative_cash():
    c = config()
    c["initial_cash"] = "1000"
    c["symbols"]["AAA"]["bars"] = c["symbols"]["AAA"]["bars"][:17]
    c["symbols"]["AAA"]["bars"][-1].update(
        open="200", high="201", low="199", close="200"
    )
    r = execute(c)
    assert r.portfolio.total_cash >= 0
    assert r.portfolio.reserved_cash == 0


def test_entry_interval_protection_is_active():
    c = config()
    c["symbols"]["AAA"]["bars"] = c["symbols"]["AAA"]["bars"][:17]
    c["symbols"]["AAA"]["bars"][-1].update(
        open="102", high="110", low="98", close="102"
    )
    r = execute(c)
    assert "AAA" not in r.portfolio.positions
    assert r.fills[-1].status == "EXIT"
    assert r.fills[-1].price == D(99)


def test_equity_uses_last_published_mark():
    c = config()
    c["symbols"]["AAA"]["bars"][-1].update(
        open="103", high="105", low="102", close="104"
    )
    r = execute(c)
    assert r.portfolio.equity({}) == D(10020)


def test_metrics_use_net_trade_pnl_including_both_fees():
    c = config()
    c["sizing"].update(entry_fee="10", exit_fee="10")
    # Sell at the 09:47 open (a timer cannot use the preceding open).
    c["execution"]["flatten_at"] = "09:47:00"
    c["symbols"]["AAA"]["bars"][-1].update(
        open="103", high="103", low="103", close="103"
    )
    m = run_synthetic_session(c)["metrics"]
    assert m["realized_pnl"] == D(-10)
    assert m["win_count"] == 0 and m["loss_count"] == 1
    assert m["expectancy"] == D(-10)


def test_expired_intent_cannot_fill_after_gap_in_input():
    c = config()
    c["symbols"]["AAA"]["bars"] = c["symbols"]["AAA"]["bars"][:17]
    c["symbols"]["AAA"]["bars"][-1].update(
        bar_start="2024-11-29T10:00:00",
        bar_end="2024-11-29T10:01:00",
        available_at="2024-11-29T10:01:00",
    )
    r = execute(c)
    assert not any(f.status == "FILLED" for f in r.fills)
    assert r.portfolio.reserved_cash == 0


def test_delayed_signal_cannot_use_an_earlier_open():
    c = config()
    c["symbols"]["AAA"]["bars"] = c["symbols"]["AAA"]["bars"][:17]
    c["symbols"]["AAA"]["bars"][-2]["available_at"] = "2024-11-29T09:46:30"
    c["scanner_inputs"] = {"09:46:30": {"AAA": {"rvol": "2", "gain": ".03"}}}
    r = execute(c)
    assert not any(f.status == "FILLED" for f in r.fills)


def test_completed_bar_before_end_rejected():
    c = config()
    c["symbols"]["AAA"]["bars"][-3]["available_at"] = "2024-11-29T09:45:10"
    with pytest.raises(ValueError):
        execute(c)


def test_missing_opening_range_never_signals():
    c = config()
    bars = c["symbols"]["AAA"]["bars"]
    c["symbols"]["AAA"]["bars"] = [bars[0], *bars[15:]]
    r = execute(c)
    assert not any(d.proposed for d in r.decisions)


@pytest.mark.parametrize(
    "field,value", [("schema_version", 999), ("evidence_type", "research")]
)
def test_run_schema_is_enforced(field, value):
    c = config()
    c[field] = value
    with pytest.raises(ValueError):
        execute(c)


def test_required_flatten_without_execution_data_is_incomplete():
    c = config()
    c["symbols"]["AAA"]["bars"] = c["symbols"]["AAA"]["bars"][:17]
    c["execution"]["flatten_at"] = "09:48:00"
    with pytest.raises(ValueError):
        run_synthetic_session(c)


def test_complete_runner_prefix_is_invariant_to_future_prices():
    a = config()
    b = deepcopy(a)
    b["symbols"]["AAA"]["bars"][-1].update(
        open="104", high="105", low="103", close="104"
    )
    ra, rb = execute(a), execute(b)
    cutoff = (9 * 3600 + 47 * 60) * 1000 - 1
    assert [d for d in ra.decisions if d.available_at_ms <= cutoff] == [
        d for d in rb.decisions if d.available_at_ms <= cutoff
    ]
    assert [f for f in ra.fills if f.available_at_ms <= cutoff] == [
        f for f in rb.fills if f.available_at_ms <= cutoff
    ]


def test_fill_rechecks_risk_and_never_upsizes():
    c = config()
    c["sizing"].update(risk_budget="20", max_quantity="100")
    c["symbols"]["AAA"]["bars"] = c["symbols"]["AAA"]["bars"][:17]
    c["symbols"]["AAA"]["bars"][-1].update(
        open="104", high="105", low="103", close="104"
    )
    r = execute(c)
    entry = next(f for f in r.fills if f.status == "FILLED")
    assert entry.quantity == D(4)
    assert entry.quantity * (entry.price - D(99)) == D(20)
    assert r.portfolio.reserved_cash == 0


def test_real_replay_context_and_trace_prefix_survive_future_changes():
    from rsys.replay_runtime import ReplayRunner

    a, b = config(), config()
    b["symbols"]["AAA"]["bars"][-1].update(
        open="104", high="105", low="103", close="104"
    )
    ra, rb = execute(a), execute(b)
    assert isinstance(ra.replay, ReplayRunner)
    cutoff = (9 * 60 + 47) * 60_000 - 1
    assert [row for row in ra.trace if row["available_at_ms"] <= cutoff] == [
        row for row in rb.trace if row["available_at_ms"] <= cutoff
    ]
    snapshot = ra.contexts[15]
    bars = snapshot.closed_bars("AAA")
    assert len(bars) == 16 and bars[-1].close == D("101.02")
    assert all(b.available_at_ns <= ra.origin_ns + cutoff * 1_000_000 for b in bars)
    # Negative control: an already-published change must affect the trace/decision.
    changed = deepcopy(a)
    changed["symbols"]["AAA"]["bars"][0]["high"] = "103"
    rc = execute(changed)
    assert [row for row in ra.trace if row["available_at_ms"] <= cutoff] != [
        row for row in rc.trace if row["available_at_ms"] <= cutoff
    ]
    assert not any(d.proposed for d in rc.decisions)


def test_acceptance_and_fill_order_follow_rank_with_shared_cash():
    c = config()
    c["symbols"]["ZZZ"] = deepcopy(c["symbols"]["AAA"])
    c["initial_cash"] = "1000"
    c["sizing"]["max_quantity"] = "6"
    c["scanner_inputs"]["09:46:00"]["ZZZ"] = {"rvol": "3", "gain": ".03"}
    a = execute(c)
    reversed_config = deepcopy(c)
    reversed_config["symbols"] = dict(reversed(list(c["symbols"].items())))
    b = execute(reversed_config)
    decision = next(d for d in a.decisions if d.accepted)
    assert decision.accepted == ["ZZZ", "AAA"]
    assert [f.symbol for f in a.fills if f.status == "FILLED"] == ["ZZZ", "AAA"]
    assert a.trace == b.trace
    assert a.portfolio.total_cash >= 0


def test_late_overlapping_position_intervals_fail_explicitly():
    c = config()
    c["symbols"]["AAA"]["bars"][-2]["available_at"] = "2024-11-29T09:47:30"
    with pytest.raises(ValueError, match="overlapping"):
        execute(c)


def test_vwap_runs_through_public_config_and_preserves_pending_observations():
    cases = json.loads(
        (Path(__file__).parents[1] / "fixtures/strategy_cases.json").read_text()
    )["cases"]
    bars = deepcopy(next(c["bars"] for c in cases if c["id"] == "S03"))
    bars.extend(
        [
            {
                "bar_start": "2024-11-29T09:53:00",
                "bar_end": "2024-11-29T09:54:00",
                "available_at": "2024-11-29T09:54:00",
                "open": "102",
                "high": "103",
                "low": "101",
                "close": "102",
                "volume": "100",
            },
            {
                "bar_start": "2024-11-29T09:54:00",
                "bar_end": "2024-11-29T09:55:00",
                "available_at": "2024-11-29T09:55:00",
                "open": "102",
                "high": "103",
                "low": "101",
                "close": "102",
                "volume": "100",
            },
        ]
    )
    c = config()
    c["symbols"]["AAA"].update(strategy="vwap_touch_hold_v1", bars=bars)
    c["scanner_inputs"] = {"09:53:00": {"AAA": {"rvol": "2", "gain": ".02"}}}
    c["execution"]["decision_latency_ms"] = 100
    runner = execute(c)
    fill = next(f for f in runner.fills if f.status == "FILLED")
    assert fill.available_at_ms == (9 * 60 + 54) * 60_000
    assert runner.strategies["AAA"].cumulative_volume == D(2500)
    assert runner.portfolio.positions["AAA"].stop == D("99.99")


@pytest.mark.parametrize(
    "mutation",
    [
        "bool_cash",
        "nan_volume",
        "bad_ohlc",
        "date",
        "unknown_sizing",
        "unknown_strategy_option",
        "bad_hold",
        "unknown_scanner_symbol",
    ],
)
def test_strict_run_input_boundary(mutation):
    c = config()
    if mutation == "bool_cash":
        c["initial_cash"] = True
    elif mutation == "nan_volume":
        c["symbols"]["AAA"]["bars"][0]["volume"] = "NaN"
    elif mutation == "bad_ohlc":
        c["symbols"]["AAA"]["bars"][0]["high"] = "90"
    elif mutation == "date":
        c["symbols"]["AAA"]["bars"][-1]["bar_start"] = "2024-12-02T09:47:00"
    elif mutation == "unknown_sizing":
        c["sizing"]["risk_fraaction"] = ".01"
    elif mutation == "unknown_strategy_option":
        c["symbols"]["AAA"]["strategy_config"] = {"range_minutez": 15}
    elif mutation == "bad_hold":
        c["execution"]["max_hold_ms"] = -1
    else:
        c["scanner_inputs"]["09:46:00"]["UNKNOWN"] = {"rvol": "2", "gain": ".02"}
    with pytest.raises(ValueError):
        execute(c)


def test_missing_scanner_score_excludes_with_reason():
    c = config()
    c["scanner_inputs"]["09:46:00"]["AAA"]["rvol"] = None
    r = execute(c)
    d = next(d for d in r.decisions if d.available_at_ms == (9 * 60 + 46) * 60_000)
    assert d.excluded == {"AAA": "MISSING_RVOL"}
    assert not d.proposed


def test_flatten_date_cannot_be_discarded():
    c = config()
    c["execution"]["flatten_at"] = "2024-12-02T09:47:00"
    with pytest.raises(ValueError, match="date differs"):
        execute(c)


def test_breakeven_completed_trade_is_not_labeled_no_trades():
    c = config()
    c["symbols"]["AAA"]["bars"][-1].update(
        open="102", high="102", low="102", close="102"
    )
    c["execution"]["flatten_at"] = "09:47:00"
    metrics = run_synthetic_session(c)["metrics"]
    assert metrics["completed_trade_count"] == 1
    assert metrics["expectancy"] == D(0)
    assert metrics["profit_factor"] == "NOT_DEFINED_NO_LOSSES"

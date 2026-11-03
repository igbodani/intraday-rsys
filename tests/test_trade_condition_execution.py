"""Fixture conditions constrain account prices as well as bar aggregation."""

import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest
from test_public_sessions import invoke

from rsys.account.models import AcceptedIntent, Position, Proposal
from rsys.application.batch import SessionBatch
from rsys.data.errors import UnknownConditionError
from rsys.execution.models import Trade
from rsys.execution.trade_adapter import TradeExecutionAdapter

ROOT = Path(__file__).resolve().parents[1]


def intent():
    return AcceptedIntent(
        Proposal("AAA", D(5), D(100), stop=D(98), target=D(104), decision_time_ms=1000),
        reserved_cash=D(500),
    )


def position():
    return Position("AAA", D(5), D(100), D(500), stop=D(98), target=D(104), mark=D(100))


def candidate(price, conditions):
    return Trade("candidate", 2000, 2000, D(price), conditions=conditions)


@pytest.mark.parametrize(
    "conditions",
    [
        ("VOLUME_ONLY",),
        ("LATE_REPORT",),
        ("ALL", "VOLUME_ONLY"),
        ("ALL", "LATE_REPORT"),
    ],
)
@pytest.mark.parametrize(
    "action,price",
    [
        ("entry", "101"),
        ("stop", "1"),
        ("target", "10000"),
        ("timer", "101"),
    ],
)
def test_ineligible_condition_never_consumes_entry_exit_or_timer_reference(
    conditions, action, price
):
    adapter = TradeExecutionAdapter()
    if action != "entry":
        adapter.activate_seeded_position("entry", 1000, 1000)

    def consider(trade):
        if action == "entry":
            return adapter.consider_entry(intent(), frozenset(), trade)
        if action == "timer":
            return adapter.force_exit(trade, "FLATTEN", requested_at_ms=1500)
        return adapter.consider_exit(position(), trade)

    assert consider(candidate(price, conditions)).status == "REJECTED_CONDITION"
    assert adapter.entry_trade_id == (None if action == "entry" else "entry")
    assert adapter.exit_trade_id is None
    # A later ordinary print is still usable: rejecting one cannot consume the order.
    ordinary = Trade("ordinary", 3000, 3000, D(price), conditions=("ALL",))
    assert consider(ordinary).status == "FILLED"


@pytest.mark.parametrize("action", ["entry", "exit", "timer"])
def test_direct_adapter_does_not_silently_accept_unknown_condition_codes(action):
    adapter = TradeExecutionAdapter()
    trade = candidate("101", ("UNREGISTERED",))
    with pytest.raises(UnknownConditionError):
        if action == "entry":
            adapter.consider_entry(intent(), frozenset(), trade)
        else:
            adapter.activate_seeded_position("entry", 1000, 1000)
            if action == "exit":
                adapter.consider_exit(position(), trade)
            else:
                adapter.force_exit(trade, "FLATTEN", requested_at_ms=1500)
    assert adapter.exit_trade_id is None


def fixture(price, conditions):
    config = json.loads((ROOT / "fixtures/sessions_trade_fixture.json").read_text())
    row = next(
        t
        for t in config["sessions"][0]["symbols"]["AAA"]["trades"]
        if t["event_time"] == "2024-11-29T09:47:00"
    )
    row["price"], row["conditions"] = price, conditions
    return config


@pytest.mark.parametrize("price", ["1", "10000"])
def test_volume_only_price_cannot_change_two_day_fills_equity_or_risk(price):
    neutral = SessionBatch(fixture("102", ["VOLUME_ONLY"]))
    corrupt = SessionBatch(fixture(price, ["VOLUME_ONLY"]))
    neutral.run()
    corrupt.run()
    assert corrupt.summary() == neutral.summary()
    assert corrupt.summary()["ending_equity"] == D("1034.14420")
    assert [r.get("risk") for r in corrupt.trace] == [
        r.get("risk") for r in neutral.trace
    ]
    assert corrupt.portfolio.reserved_cash == 0
    assert corrupt.portfolio.positions == {}
    # Negative control: an ALL print IS an eligible mark and fresh fill reference.
    eligible = SessionBatch(fixture(price, ["ALL"]))
    eligible.run()
    assert eligible.summary()["ending_equity"] != neutral.summary()["ending_equity"]


def test_public_volume_only_trade_preserves_inputs_and_two_session_account(tmp_path):
    config = fixture("10000", ["VOLUME_ONLY"])
    result, attempt, manifest = invoke(tmp_path, deepcopy(config))
    assert result.returncode == 0, result.stdout + result.stderr
    assert manifest["status"] == "success"
    assert D(manifest["ending_equity"]) == D("1034.14420")
    assert manifest["metrics"]["completed_trade_count"] == 2
    assert manifest["open_positions"] == {}
    retained = json.loads((attempt / "input.json").read_text())
    assert retained == config
    trace = [
        json.loads(row) for row in (attempt / "trace.jsonl").read_text().splitlines()
    ]
    assert all(not r.get("risk", {}).get("account_halted", False) for r in trace)


@pytest.mark.parametrize(
    "condition,mark", [("VOLUME_ONLY", "102"), ("LATE_REPORT", "10000")]
)
def test_marks_follow_declared_close_eligibility_separately_from_execution(
    condition, mark
):
    batch = SessionBatch(fixture("10000", [condition]))
    batch.run()
    point = ((9 * 60 + 47) * 60) * 1000 + 250
    (publication,) = [
        r
        for r in batch.runners[0].trace
        if r["kind"] == "replay_source" and r["available_at_ms"] == point
    ]
    assert publication["account"]["positions"]["AAA"]["mark"] == D(mark)
    assert not any(f.event_time_ms == point - 250 for f in batch.runners[0].fills)


def test_unknown_condition_fails_public_run_before_execution_with_input_retained(tmp_path):
    config = fixture("10000", ["UNREGISTERED"])
    result, attempt, manifest = invoke(tmp_path, config)
    assert result.returncode == 2
    assert manifest["status"] == "failed"
    assert manifest["error"]["type"] == "RunConfigError"
    assert "condition codes" in manifest["error"]["message"]
    assert json.loads((attempt / "fills.json").read_text()) == []
    assert (attempt / "trace.jsonl").read_text() == ""
    assert json.loads((attempt / "input.json").read_text()) == config

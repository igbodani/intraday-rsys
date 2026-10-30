"""Independent regressions for the October M2 review, not fixture-count checks."""

from copy import deepcopy
from decimal import Decimal as D

import pytest

from rsys.account.models import AcceptedIntent, Position, Proposal
from rsys.account.portfolio import Portfolio
from rsys.account.sizing import size_quantity
from rsys.execution.models import Trade
from rsys.execution.trade_adapter import TradeExecutionAdapter
from rsys.strategies.orb import _floor_tick


def account():
    return Portfolio(D(1000), D(1000), D(0))


def test_overspending_buy_is_atomic():
    p = account()
    p.reserve(D(900))
    before = deepcopy(p.__dict__)
    with pytest.raises(ValueError):
        p.buy("AAA", D(9), D(200), D(0), D(900), D(99), None)
    assert p.__dict__ == before


def test_invalid_settlement_is_atomic():
    p = Portfolio(D(1000), D(500), D(500))
    with pytest.raises(ValueError):
        p.apply_settlement_release(D(600))
    assert (p.spendable_cash, p.unsettled_cash) == (D(500), D(500))


def test_negative_reservation_rejected():
    p = account()
    with pytest.raises(ValueError):
        p.reserve(D(-1))
    assert p.reserved_cash == 0


def test_duplicate_position_cannot_overwrite_paid_basis():
    p = account()
    p.buy("AAA", D(1), D(100), D(0), D(0), D(98), None)
    before = deepcopy(p.__dict__)
    with pytest.raises(ValueError):
        p.buy("AAA", D(1), D(100), D(0), D(0), D(98), None)
    assert p.__dict__ == before


def test_size_floors_maximum_to_quantity_increment():
    q, _ = size_quantity(
        entry=D(100),
        stop=D(98),
        entry_fee=D(0),
        exit_fee=D(0),
        risk_budget=D(100),
        available_cash=D(1000),
        max_quantity=D("2.5"),
        quantity_step=D(1),
    )
    assert q == 2


def test_non_power_of_ten_tick():
    assert _floor_tick(D("99.03"), D(".05")) == D("99.00")


def intent():
    return AcceptedIntent(
        Proposal(
            "AAA",
            D(5),
            D(100),
            D(98),
            None,
            1000,
            decision_latency_ms=500,
            expiry_ms=1000,
        ),
        D(500),
    )


def test_late_trade_before_ready_is_not_executable():
    a = TradeExecutionAdapter()
    assert (
        a.consider_entry(
            intent(), frozenset(), Trade("late", 1200, 1600, D(101))
        ).status
        != "FILLED"
    )


def test_expired_trade_intent_is_not_executable():
    a = TradeExecutionAdapter()
    assert (
        a.consider_entry(
            intent(), frozenset(), Trade("expired", 3000, 3000, D(101))
        ).status
        != "FILLED"
    )


def test_late_pre_entry_print_cannot_exit_and_exit_is_idempotent():
    a = TradeExecutionAdapter()
    a.consider_entry(intent(), frozenset(), Trade("entry", 1600, 1600, D(101)))
    p = Position("AAA", D(5), D(101), D(505), D(98), D(107), D(101))
    assert a.consider_exit(p, Trade("old", 900, 1800, D(95))).status != "FILLED"
    t = Trade("exit", 1900, 1900, D(95))
    assert a.consider_exit(p, t).status == "FILLED"
    assert a.consider_exit(p, t).status != "FILLED"


def test_equal_reservations_remain_owned_on_repeated_cancellation():
    p = account()
    a, b = p.reserve(D(100), "a"), p.reserve(D(100), "b")
    p.release_reservation(D(100), a)
    p.release_reservation(D(100), a)
    assert p.reserved_cash == D(100) and p.has_reservation(b)
    with pytest.raises(ValueError):
        p.buy("AAA", D(1), D(100), D(0), D(100), None, None, reservation_id=a)
    assert p.reserved_cash == D(100) and p.total_cash == D(1000)


def test_transaction_redelivery_does_not_charge_fees_or_release_twice():
    p = account()
    r = p.reserve(D(101), "entry")
    for _ in range(2):
        p.buy(
            "AAA",
            D(1),
            D(100),
            D(1),
            D(101),
            None,
            None,
            reservation_id=r,
            transaction_id="buy",
        )
    for _ in range(2):
        p.sell("AAA", D(104), D(1), settles_immediately=False, transaction_id="sell")
    for _ in range(2):
        p.apply_settlement_release(D(103), transaction_id="settled")
    assert p.total_cash == D(1002) == p.spendable_cash
    assert p.realized_pnl == D(2) and p.unsettled_cash == 0
    with pytest.raises(ValueError, match="conflicting"):
        p.apply_settlement_release(D(102), transaction_id="settled")


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D(-1), True])
def test_invalid_quantities_fail_before_any_mutation(bad):
    p = account()
    before = deepcopy(p.__dict__)
    with pytest.raises(ValueError):
        p.buy("AAA", bad, D(100), D(0), D(0), None, None)
    assert p.__dict__ == before


def test_pending_symbol_rejected_even_with_a_different_proposal_id():
    from dataclasses import replace

    from rsys.account.acceptance import AcceptanceBook

    p, book = account(), AcceptanceBook()
    proposal = replace(intent().proposal, proposal_id="first", scanner_rank=1)
    book.accept(p, proposal, D(500))
    with pytest.raises(ValueError, match="DUPLICATE_PENDING"):
        book.accept(p, replace(proposal, proposal_id="second"), D(500))
    assert p.reserved_cash == D(500)


def test_phase_engine_copies_inputs_and_freezes_nested_decision_views():
    from rsys.replay.engine import PhaseEngine
    from rsys.replay.models import PhaseEvent

    captured = []
    engine = PhaseEngine(on_decision=lambda _e, v: captured.append(v))
    payload = {"close": {"samples": [100]}}
    engine.enqueue(PhaseEvent("pub", 100, 20, "publish", "AAA", payload))
    payload["close"]["samples"].append(999)
    engine.enqueue(PhaseEvent("decision", 100, 40, "decision"))
    engine.run()
    assert captured[0]["AAA"]["samples"] == (100,)
    with pytest.raises(TypeError):
        captured[0]["AAA"]["samples"] = (999,)
    assert engine.closes["AAA"]["samples"] == [100]


def test_phase_duplicate_identity_includes_timing_and_phase():
    from rsys.replay.engine import PhaseEngine
    from rsys.replay.errors import PhaseOrderError
    from rsys.replay.models import PhaseEvent

    engine = PhaseEngine()
    engine.enqueue(PhaseEvent("a", 100, 20, "publish", "AAA", {"close": 100}))
    engine.run()
    engine.enqueue(PhaseEvent("a", 200, 20, "publish", "AAA", {"close": 100}))
    with pytest.raises(PhaseOrderError):
        engine.run()


def test_callback_cannot_enqueue_into_its_own_phase():
    from rsys.replay.engine import PhaseEngine
    from rsys.replay.errors import PhaseOrderError
    from rsys.replay.models import PhaseEvent

    def callback(_):
        engine.enqueue(PhaseEvent("z", 100, 20, "publish"))

    engine = PhaseEngine(on_publish=callback)
    engine.enqueue(PhaseEvent("a", 100, 20, "publish"))
    with pytest.raises(PhaseOrderError):
        engine.run()

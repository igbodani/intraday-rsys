"""Step 1 regressions: the shared fill/account transition contract
(`execution_contract.py`) consolidating `orders.py`, `standalone_run.py`,
`fixture_engine.py` and `trade_execution.py` execution timing.

These are integration tests against the real `FixtureEngine`/`BarSessionRunner`/
`Portfolio` paths, not only unit tests of the adapter functions in isolation.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from rsys.account.models import ENTRY_THROUGH_STOP, AcceptedIntent, Position, Proposal
from rsys.account.portfolio import Portfolio
from rsys.execution.contracts import (
    ENTRY_PHASE,
    EXIT_PHASE,
    commit_entry,
    commit_exit,
    decide_bar_open_entry,
    decide_bar_open_gap_exit,
    from_trade_outcome,
)
from rsys.execution.models import FillOutcome, FillTransition, RejectedEntry, Trade
from rsys.execution.trade_adapter import TradeExecutionAdapter


def account():
    return Portfolio(D(1000), D(1000), D(0))


# -- shared phase ordering ---------------------------------------------------


def test_exit_phase_precedes_entry_phase():
    assert EXIT_PHASE < ENTRY_PHASE


def test_fixture_engine_exits_precede_entries_at_an_equal_timestamp():
    """A due exit and a new entry for the SAME symbol, scheduled at the identical
    bar-open instant: the shared EXIT_PHASE/ENTRY_PHASE ordering must free the symbol
    before a re-entry can fill at that same open. The ordinary accept-time
    DUPLICATE_PENDING_OR_HELD rule only blocks a *new* `proposal` event while held, so
    this white-box regression injects an already-accepted intent directly -- exactly
    what the shared contract's phase ordering must still arbitrate correctly."""
    from rsys.execution.fixtures import FixtureEngine
    from rsys.execution.models import PendingExit
    from rsys.replay.models import PhaseEvent

    case = {
        "id": "same_time_exit_then_entry",
        "initial_cash": "1000.00",
        "initial_position": {
            "symbol": "AAA",
            "quantity": "5",
            "entry": "100",
            "stop": "98",
            "target": None,
        },
    }
    engine = FixtureEngine(case)
    reservation_id = engine.portfolio.reserve(D(206))
    engine.pending_intents["AAA"] = AcceptedIntent(
        proposal=_proposal(
            quantity=D(2), signal_price=D(103), stop=D(101), decision_time_ms=0
        ),
        reserved_cash=D(206),
        reservation_id=reservation_id,
    )
    point_ms = 35 * 60_000
    engine.pending_exits["AAA"] = PendingExit(
        scheduled_time_ms=point_ms, reason="time_exit"
    )
    point_ns = point_ms * 1_000_000
    payload = {"type": "bar_open", "symbol": "AAA", "open": "103"}
    engine.engine.enqueue(PhaseEvent("open:60", point_ns, 60, "open", payload=payload))
    engine.engine.enqueue(PhaseEvent("open:70", point_ns, 70, "open", payload=payload))
    engine.engine.run()
    engine._finalize()
    result = engine.result

    labels = [c.label for c in result.checkpoints]
    assert "time_exit" in labels and "entry" in labels
    assert labels.index("time_exit") < labels.index("entry")
    assert result.final_quantity == D(2)
    assert result.entry_fill == D(103)


# -- resizing / rejection decisions ------------------------------------------


def _proposal(**overrides):
    base = {
        "symbol": "AAA",
        "quantity": D(10),
        "signal_price": D(100),
        "stop": D(98),
        "target": None,
        "decision_time_ms": 0,
    }
    base.update(overrides)
    return Proposal(**base)


def test_decide_bar_open_entry_rejects_through_stop():
    decision = decide_bar_open_entry(
        _proposal(stop=D(99)),
        0,
        D(98),  # open at/under the stop
        D(0),
        D(10_000),
        entry_fee=D(0),
        exit_fee=D(0),
        risk_budget=None,
        quantity_step=D(1),
    )
    assert isinstance(decision, RejectedEntry)
    assert decision.reason == ENTRY_THROUGH_STOP


def test_decide_bar_open_entry_never_upsizes_past_proposal_quantity():
    price, qty = decide_bar_open_entry(
        _proposal(quantity=D(3)),
        0,
        D(100),
        D(0),
        D(100_000),  # ample cash; the cap must still be the proposal's own quantity
        entry_fee=D(0),
        exit_fee=D(0),
        risk_budget=None,
        quantity_step=D(1),
    )
    assert qty == D(3)
    assert price == D(100)


def test_decide_bar_open_entry_rejects_on_insufficient_fill_time_cash():
    decision = decide_bar_open_entry(
        _proposal(),
        0,
        D(100),
        D(0),
        D(1),  # far too little cash at the modeled fill price
        entry_fee=D(0),
        exit_fee=D(0),
        risk_budget=None,
        quantity_step=D(1),
    )
    assert isinstance(decision, RejectedEntry)


def test_decide_bar_open_gap_exit_priority_pending_over_stop_over_target():
    position = Position("AAA", D(5), D(100), D(500), stop=D(99), target=D(110), mark=D(100))
    pending = decide_bar_open_gap_exit(position, D(98), D(0), True)
    assert pending.kind == "pending"
    stop = decide_bar_open_gap_exit(position, D(98), D(0), False)
    assert stop.kind == "stop"
    target = decide_bar_open_gap_exit(position, D(111), D(0), False)
    assert target.kind == "target"
    none = decide_bar_open_gap_exit(position, D(105), D(0), False)
    assert none is None


# -- account-commit boundary: exactly-once, atomic, conflict-detecting ------


def _entry_transition(**overrides):
    base = {
        "instrument": "AAA",
        "intent_identity": "reservation:1",
        "activation_key": "AAA:1",
        "execution_reference_id": "entry:reservation:1",
        "availability_key": 0,
        "quantity": D(5),
        "price": D(100),
        "fee": D(0),
        "reason": "entry",
    }
    base.update(overrides)
    return FillTransition(**base)


def test_commit_entry_is_atomic_on_a_duplicated_dispatch():
    p = account()
    p.reserve(D(500), reservation_id="reservation:1")
    transition = _entry_transition()
    commit_entry(p, transition, stop=D(98), target=None, reserved_cash=D(500))
    before = (p.total_cash, p.spendable_cash, p.positions["AAA"].quantity)
    # A repeated dispatch of the identical fill event must not double-charge.
    commit_entry(p, transition, stop=D(98), target=None, reserved_cash=D(500))
    after = (p.total_cash, p.spendable_cash, p.positions["AAA"].quantity)
    assert before == after


def test_commit_entry_raises_on_a_conflicting_duplicate_reference():
    p = account()
    p.reserve(D(500), reservation_id="reservation:1")
    commit_entry(
        p, _entry_transition(), stop=D(98), target=None, reserved_cash=D(500)
    )
    with pytest.raises(ValueError):
        commit_entry(
            p,
            _entry_transition(quantity=D(4)),  # same reference id, different fill
            stop=D(98),
            target=None,
            reserved_cash=D(500),
        )


def test_commit_exit_is_atomic_on_a_duplicated_dispatch():
    p = account()
    p.buy("AAA", D(5), D(100), D(0), D(0), D(98), None)
    transition = FillTransition(
        instrument="AAA",
        intent_identity="position:AAA",
        activation_key="AAA:1",
        execution_reference_id="exit:AAA:1",
        availability_key=1,
        quantity=D(5),
        price=D(105),
        fee=D(0),
        reason="gap_target",
    )
    commit_exit(p, transition)
    before = (p.total_cash, p.realized_pnl)
    commit_exit(p, transition)
    assert (p.total_cash, p.realized_pnl) == before


def test_equal_value_reservations_are_owned_independently():
    """Two intents reserving the identical amount: releasing one by its own id must
    never clamp or release the other's cash."""
    p = account()
    r1 = p.reserve(D(300))
    r2 = p.reserve(D(300))
    p.release_reservation(D(300), r1)
    assert p.has_reservation(r2) and not p.has_reservation(r1)
    assert p.reserved_cash == D(300)


def test_conflicting_reservation_identity_is_rejected():
    p = account()
    p.reserve(D(100), reservation_id="r1")
    with pytest.raises(ValueError):
        p.reserve(D(200), reservation_id="r1")


# -- subsequent-trade adapter output shares the same contract ---------------


def test_trade_outcome_converts_to_the_shared_fill_transition():
    adapter = TradeExecutionAdapter()
    intent_proposal = _proposal(decision_time_ms=0)
    intent = AcceptedIntent(
        proposal=intent_proposal, reserved_cash=D(1000), reservation_id="reservation:1"
    )
    trade = Trade("t1", event_time_ms=1, available_at_ms=1, price=D(100))
    outcome = adapter.consider_entry(intent, frozenset(), trade)
    assert outcome.status == "FILLED"
    transition = from_trade_outcome(
        outcome,
        instrument="AAA",
        intent_identity="reservation:1",
        activation_key="AAA:1",
        availability_key=1,
        market_time_ms=trade.event_time_ms,
        quantity=D(10),
    )
    assert transition.market_time_ms == 1
    assert transition.execution_reference_id == "t1"


def test_trade_outcome_conversion_rejects_a_non_filled_outcome():
    with pytest.raises(ValueError):
        from_trade_outcome(
            FillOutcome("REJECTED_STALE", trade_id="t1"),
            instrument="AAA",
            intent_identity="reservation:1",
            activation_key="AAA:1",
            availability_key=1,
            market_time_ms=1,
            quantity=D(10),
        )


def test_a_correction_never_rewrites_an_already_committed_fill():
    """Duplicate/corrected reference delivery: a correction invalidates a trade
    identity causally but must never undo a fill that already used it as its
    reference -- it can only block a *future* use of that trade id."""
    adapter = TradeExecutionAdapter()
    intent_proposal = _proposal(decision_time_ms=0)
    intent = AcceptedIntent(
        proposal=intent_proposal, reserved_cash=D(1000), reservation_id="reservation:1"
    )
    trade = Trade("t1", event_time_ms=1, available_at_ms=1, price=D(100))
    filled = adapter.consider_entry(intent, frozenset(), trade)
    assert filled.status == "FILLED"
    p = account()
    p.reserve(D(1000), reservation_id="reservation:1")
    transition = from_trade_outcome(
        filled,
        instrument="AAA",
        intent_identity="reservation:1",
        activation_key="AAA:1",
        availability_key=1,
        market_time_ms=1,
        quantity=D(5),
    )
    commit_entry(p, transition, stop=D(98), target=None, reserved_cash=D(1000))
    adapter.apply_correction("t1")  # a later correction references the same trade id
    assert p.positions["AAA"].quantity == D(5)  # the committed fill is unchanged
    assert "t1" in adapter.invalidated_trade_ids

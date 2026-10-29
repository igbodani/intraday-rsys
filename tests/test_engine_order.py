"""Ordering-invariant tests for `PhaseEngine` (docs/M2_IMPLEMENTATION_HANDOFF.md step 1,
M2-01a). Proves phase-bounded dispatch, frozen same-time decision views, settlement-before-
decision, the closed-phase invariant and idempotent/conflicting event redelivery -- using the
already-tested `rsys.acceptance`/`rsys.portfolio` modules for the account side, not hand-
copied expected values.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from rsys.acceptance import AcceptanceBook, sort_proposals
from rsys.orders import Proposal
from rsys.phase_engine import PhaseEngine, PhaseEvent, PhaseOrderError
from rsys.portfolio import Portfolio


def _noop(_event):
    return None


def test_two_symbols_reversed_publish_order_yield_identical_acceptance_and_cash():
    """A frozen decision view and the acceptance sort are independent of delivery order."""

    def build_engine(publish_order: list[str]) -> tuple[PhaseEngine, Portfolio, list[str]]:
        portfolio = Portfolio(total_cash=Decimal(1000), spendable_cash=Decimal(1000), unsettled_cash=Decimal(0))
        book = AcceptanceBook()
        accepted_order: list[str] = []
        rank_by_symbol = {"ABC": 1, "XYZ": 2}  # deterministic, independent of delivery order

        def on_decision(_event, frozen_view):
            proposals = [
                Proposal(
                    symbol=symbol, quantity=Decimal(1), signal_price=Decimal(frozen_view[symbol]),
                    stop=Decimal(frozen_view[symbol]) - 2, target=None, decision_time_ms=0,
                    scanner_rank=rank_by_symbol[symbol],
                )
                for symbol in frozen_view
            ]
            for proposal in sort_proposals(proposals):
                intent = book.accept(portfolio, proposal, proposal.signal_price * proposal.quantity)
                accepted_order.append(intent.proposal.symbol)

        engine = PhaseEngine(on_settle=_noop, on_publish=_noop, on_decision=on_decision, on_fill=_noop)
        for symbol in publish_order:
            engine.enqueue(PhaseEvent(f"pub-{symbol}", 100, 20, "publish", symbol, {"close": 100}))
        engine.enqueue(PhaseEvent("decide-100", 100, 40, "decision"))
        engine.run()
        return engine, portfolio, accepted_order

    _, portfolio_forward, order_forward = build_engine(["ABC", "XYZ"])
    _, portfolio_reverse, order_reverse = build_engine(["XYZ", "ABC"])

    assert order_forward == order_reverse == ["ABC", "XYZ"]
    assert portfolio_forward.reserved_cash == portfolio_reverse.reserved_cash == Decimal(200)


def test_decision_cannot_see_a_later_publish_for_the_same_symbol():
    captured_views: list[dict] = []

    def on_decision(_event, frozen_view):
        captured_views.append(frozen_view)

    engine = PhaseEngine(on_settle=_noop, on_publish=_noop, on_decision=on_decision, on_fill=_noop)
    engine.enqueue(PhaseEvent("pub-A-t0", 100, 20, "publish", "A", {"close": 100}))
    engine.enqueue(PhaseEvent("decide-t0", 100, 40, "decision"))
    engine.enqueue(PhaseEvent("pub-A-t1", 200, 20, "publish", "A", {"close": 105}))
    engine.run()

    assert captured_views == [{"A": 100}]  # the decision's own frozen copy, forever
    assert engine.closes["A"] == 105  # later state has since moved on


def test_settlement_precedes_a_same_time_decision_and_a_stop_change_only_affects_later_settles():
    stop_value = [98]
    settle_log: list[tuple[int, int]] = []

    def on_settle(event):
        settle_log.append((event.available_at_ns, stop_value[0]))

    def on_decision(event, _frozen_view):
        if event.available_at_ns == 100:
            stop_value[0] = 100  # requested at this decision; must not affect settle(100)

    engine = PhaseEngine(on_settle=on_settle, on_publish=_noop, on_decision=on_decision, on_fill=_noop)
    engine.enqueue(PhaseEvent("settle-100", 100, 10, "settle"))
    engine.enqueue(PhaseEvent("decide-100", 100, 40, "decision"))
    engine.enqueue(PhaseEvent("settle-200", 200, 10, "settle"))
    engine.run()

    assert settle_log == [(100, 98), (200, 100)]


def test_derived_event_into_a_closed_phase_is_rejected_without_mutation():
    engine = PhaseEngine(on_settle=_noop, on_publish=_noop, on_decision=lambda *_: None, on_fill=_noop)
    engine.enqueue(PhaseEvent("pub-100", 100, 20, "publish", "A", {"close": 100}))
    engine.enqueue(PhaseEvent("decide-100", 100, 40, "decision"))
    engine.run()
    transitions_before = list(engine.transitions)

    with pytest.raises(PhaseOrderError):
        engine.enqueue(PhaseEvent("late-settle", 100, 10, "settle"))  # phase 10 at t=100 is already closed

    assert engine.transitions == transitions_before  # the failed attempt mutated nothing


def test_identical_redelivery_is_idempotent_but_conflicting_payload_fails():
    applied: list[dict] = []

    def on_fill(event):
        applied.append(event.payload)

    engine = PhaseEngine(on_settle=_noop, on_publish=_noop, on_decision=lambda *_: None, on_fill=on_fill)
    engine.enqueue(PhaseEvent("fill-1", 100, 70, "fill", "A", {"price": 100}))
    engine.run()
    assert len(applied) == 1

    engine.enqueue(PhaseEvent("fill-1", 100, 70, "fill", "A", {"price": 100}))  # identical redelivery
    engine.run()
    assert len(applied) == 1  # no second effect

    engine.enqueue(PhaseEvent("fill-1", 100, 70, "fill", "A", {"price": 999}))  # conflicting payload
    with pytest.raises(PhaseOrderError):
        engine.run()
    assert len(applied) == 1


def test_ready_at_and_event_key_both_constrain_execution():
    """Equal clock readings alone do not establish eligibility: a fill observation at the
    same timestamp as the decision is only eligible once `ready_at_ns` has actually passed."""

    def on_fill(event):
        if event.available_at_ns < event.payload["ready_at_ns"]:
            return "skipped_not_ready"
        return "filled"

    engine = PhaseEngine(on_settle=_noop, on_publish=_noop, on_decision=lambda *_: None, on_fill=on_fill)
    engine.enqueue(PhaseEvent("fill-early", 100, 70, "fill", "A", {"ready_at_ns": 150}))
    engine.enqueue(PhaseEvent("fill-late", 200, 70, "fill", "A", {"ready_at_ns": 150}))
    engine.run()

    results = {record.event_id: record.result for record in engine.transitions}
    assert results["fill-early"] == "skipped_not_ready"
    assert results["fill-late"] == "filled"

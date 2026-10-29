"""Portfolio ledger invariant tests (docs/M2_IMPLEMENTATION_HANDOFF.md step 2)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from rsys.portfolio import AcceptanceError, Portfolio


def _p(total="1000", spendable=None, unsettled="0") -> Portfolio:
    total_d = Decimal(total)
    return Portfolio(
        total_cash=total_d, spendable_cash=Decimal(spendable) if spendable else total_d,
        unsettled_cash=Decimal(unsettled),
    )


def test_buy_then_sell_exact_money_identity():
    pf = _p()
    pf.reserve(Decimal(500))
    pf.buy("AAA", Decimal(5), Decimal(100), Decimal(0), Decimal(500), Decimal(98), Decimal(104))
    assert pf.total_cash == Decimal(500)
    assert pf.reserved_cash == Decimal(0)
    assert pf.equity({"AAA": Decimal(100)}) == Decimal(1000)
    trade_pnl = pf.sell("AAA", Decimal(104), Decimal(0))
    assert trade_pnl == Decimal(20)
    assert pf.total_cash == Decimal(1020)
    assert pf.equity({}) == Decimal(1020)


def test_fees_charged_once_on_open_position():
    pf = _p()
    pf.reserve(Decimal("500.50"))
    pf.buy("AAA", Decimal(5), Decimal("100.10"), Decimal("0.50"), Decimal("500.50"), None, None)
    assert pf.total_cash == Decimal(1000) - Decimal("501.00")
    assert pf.equity({"AAA": Decimal("100.10")}) == Decimal(1000) - Decimal("0.50")  # fee is the only open cost


def test_insufficient_cash_vs_insufficient_spendable_cash():
    pf = _p(total="100")
    with pytest.raises(AcceptanceError) as exc:
        pf.reserve(Decimal(102))
    assert exc.value.reason == "INSUFFICIENT_CASH"

    pf2 = _p(total="900", spendable="400", unsettled="500")
    with pytest.raises(AcceptanceError) as exc2:
        pf2.reserve(Decimal(500))
    assert exc2.value.reason == "INSUFFICIENT_SPENDABLE_CASH"


def test_settlement_release_does_not_change_total_cash_or_equity():
    pf = _p(total="900", spendable="400", unsettled="500")
    equity_before = pf.equity({})
    pf.apply_settlement_release(Decimal(500))
    assert pf.total_cash == Decimal(900)
    assert pf.spendable_cash == Decimal(900)
    assert pf.unsettled_cash == Decimal(0)
    assert pf.equity({}) == equity_before


def test_two_reservations_cannot_overcommit_shared_cash():
    pf = _p(total="1000")
    pf.reserve(Decimal(600))
    with pytest.raises(AcceptanceError):
        pf.reserve(Decimal(500))  # only 400 remains available
    pf.reserve(Decimal(400))  # exactly the remainder is fine
    assert pf.available_cash == Decimal(0)


def test_release_reservation_never_goes_negative():
    pf = _p(total="1000")
    pf.reserve(Decimal(100))
    with pytest.raises(ValueError):
        pf.release_reservation(Decimal(500))
    assert pf.reserved_cash == Decimal(100)

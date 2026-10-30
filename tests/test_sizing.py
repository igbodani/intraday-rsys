"""Cost-aware risk sizing tests (docs/M2_IMPLEMENTATION_HANDOFF.md step 2, F13/F14)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from rsys.portfolio import AcceptanceError
from rsys.sizing import size_quantity


def test_f13_risk_budget_includes_fixed_fees():
    quantity, intended_risk = size_quantity(
        entry=Decimal(100), stop=Decimal(98), entry_fee=Decimal(1), exit_fee=Decimal(1),
        risk_budget=Decimal(10), available_cash=Decimal(1000), quantity_step=Decimal(1),
        max_quantity=Decimal(100),
    )
    assert quantity == Decimal(4)
    assert intended_risk == Decimal(10)


def test_f14_one_share_exceeds_cost_aware_risk_budget():
    with pytest.raises(AcceptanceError) as exc:
        size_quantity(
            entry=Decimal(100), stop=Decimal(98), entry_fee=Decimal(1), exit_fee=Decimal(1),
            risk_budget=Decimal(3), available_cash=Decimal(1000), quantity_step=Decimal(1),
        )
    assert exc.value.reason == "RISK_BUDGET"


def test_invalid_stop_distance_rejected():
    with pytest.raises(AcceptanceError) as exc:
        size_quantity(
            entry=Decimal(100), stop=Decimal(100), entry_fee=Decimal(0), exit_fee=Decimal(0),
            risk_budget=Decimal(10), available_cash=Decimal(1000),
        )
    assert exc.value.reason == "INVALID_STOP_DISTANCE"


def test_cash_constraint_reduces_quantity_below_risk_bound():
    # Risk budget alone would allow 6 shares, but only 400 of cash remains available.
    quantity, _ = size_quantity(
        entry=Decimal(100), stop=Decimal(98), entry_fee=Decimal(0), exit_fee=Decimal(0),
        risk_budget=Decimal(12), available_cash=Decimal(400), quantity_step=Decimal(1),
        max_quantity=Decimal(6),
    )
    assert quantity == Decimal(4)


def test_quantity_never_rounds_up_past_the_step():
    quantity, _ = size_quantity(
        entry=Decimal(100), stop=Decimal(95), entry_fee=Decimal(0), exit_fee=Decimal(0),
        risk_budget=Decimal(11), available_cash=Decimal(1000), quantity_step=Decimal(1),
    )
    assert quantity == Decimal(2)  # 11 / 5 = 2.2 -> floors to 2, never 3

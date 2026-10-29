"""Cost-aware risk sizing (docs/M2_IMPLEMENTATION_HANDOFF.md step 2, F13/F14).

Chooses the largest permitted quantity satisfying the risk budget, the fixed per-trade costs
and currently available cash, rounding down to the declared quantity step. Never rounds up.
"""
from __future__ import annotations

from decimal import Decimal

from rsys.orders import INVALID_STOP_DISTANCE, RISK_BUDGET
from rsys.portfolio import AcceptanceError


def size_quantity(
    *,
    entry: Decimal,
    stop: Decimal,
    entry_fee: Decimal,
    exit_fee: Decimal,
    risk_budget: Decimal | None,
    available_cash: Decimal,
    quantity_step: Decimal = Decimal(1),
    max_quantity: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Returns (quantity, intended_stop_risk). Raises AcceptanceError(RISK_BUDGET) or
    AcceptanceError(INVALID_STOP_DISTANCE) if no positive quantity is feasible."""
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        raise AcceptanceError(INVALID_STOP_DISTANCE)

    if risk_budget is not None:
        steps = int(((risk_budget - entry_fee - exit_fee) / risk_per_share) / quantity_step)
        quantity = max(Decimal(0), Decimal(steps) * quantity_step)
    else:
        quantity = max_quantity if max_quantity is not None else Decimal(1_000_000)

    if max_quantity is not None:
        quantity = min(quantity, max_quantity)

    while quantity > 0 and quantity * entry + entry_fee > available_cash:
        quantity -= quantity_step

    if quantity <= 0:
        reason = RISK_BUDGET if risk_budget is not None else "INSUFFICIENT_CASH"
        raise AcceptanceError(reason)

    intended_risk = quantity * risk_per_share + entry_fee + exit_fee
    return quantity, intended_risk

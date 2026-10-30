"""Largest permitted quantity under explicit fixed-cost, risk, cash and quantity limits."""

from decimal import Decimal

from rsys.account.errors import AcceptanceError
from rsys.account.models import INVALID_STOP_DISTANCE, RISK_BUDGET
from rsys.account.money import amount, floor_step


def size_quantity(
    *,
    entry,
    stop,
    entry_fee,
    exit_fee,
    risk_budget,
    available_cash,
    quantity_step=Decimal(1),
    max_quantity=None,
):
    for name, value in [
        ("entry", entry),
        ("stop", stop),
        ("quantity_step", quantity_step),
    ]:
        amount(value, name, positive=True)
    for name, value in [
        ("entry_fee", entry_fee),
        ("exit_fee", exit_fee),
        ("available_cash", available_cash),
    ]:
        amount(value, name)
    distance = entry - stop
    if distance <= 0:
        raise AcceptanceError(INVALID_STOP_DISTANCE)
    bounds = [max(Decimal(0), (available_cash - entry_fee) / entry)]
    if risk_budget is not None:
        amount(risk_budget, "risk_budget", positive=True)
        bounds.append(max(Decimal(0), (risk_budget - entry_fee - exit_fee) / distance))
    if max_quantity is not None:
        amount(max_quantity, "max_quantity", positive=True)
        bounds.append(max_quantity)
    quantity = floor_step(min(bounds), quantity_step)
    if quantity <= 0:
        risk_too_small = (
            risk_budget is not None
            and quantity_step * distance + entry_fee + exit_fee > risk_budget
        )
        raise AcceptanceError(RISK_BUDGET if risk_too_small else "INSUFFICIENT_CASH")
    return quantity, quantity * distance + entry_fee + exit_fee

"""Exact validation and increment rounding shared by account and strategies."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


def amount(value: Decimal, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return value


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    amount(value, "value")
    amount(step, "step", positive=True)
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def ceil_step(value: Decimal, step: Decimal) -> Decimal:
    amount(value, "value")
    amount(step, "step", positive=True)
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step

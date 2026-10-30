"""Reconciled account metrics and run-comparison assumptions (docs/M2_IMPLEMENTATION_HANDOFF.md
step 9, metrics subset).

Completed-trade statistics use net ledger P&L including both fees. Run orchestration
persists these diagnostics; this module makes no market-performance or promotion claim.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any

UNAVAILABLE_NO_TRADES = "UNAVAILABLE_NO_TRADES"
NOT_DEFINED_NO_LOSSES = "NOT_DEFINED_NO_LOSSES"


@dataclass(frozen=True)
class CompletedTrade:
    symbol: str
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    pnl: Decimal


@dataclass(frozen=True)
class Metrics:
    starting_equity: Decimal
    ending_equity: Decimal
    net_change: Decimal
    realized_pnl: Decimal
    completed_trade_count: int
    win_count: int
    loss_count: int
    win_rate: Decimal | str
    expectancy: Decimal | str
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal | str


def reconstruct_completed_trades(fills: list[Any]) -> list[CompletedTrade]:
    """Pairs each `EXIT` fill with the most recent prior `FILLED` entry for the same symbol.
    An unmatched exit (no recorded entry, e.g. a seeded position) is skipped, never silently
    scored as a zero-P&L trade."""
    open_entries: dict[str, Any] = {}
    trades: list[CompletedTrade] = []
    for record in fills:
        if record.status == "FILLED":
            open_entries[record.symbol] = record
        elif record.status == "EXIT":
            entry = open_entries.pop(record.symbol, None)
            if entry is None:
                continue
            pnl = getattr(record, "net_pnl", None)
            if pnl is None:
                pnl = (
                    (record.price - entry.price) * record.quantity
                    - getattr(entry, "fee", Decimal(0))
                    - getattr(record, "fee", Decimal(0))
                )
            trades.append(
                CompletedTrade(
                    record.symbol, entry.price, record.price, record.quantity, pnl
                )
            )
    return trades


def compute_metrics(
    starting_equity: Decimal,
    ending_equity: Decimal,
    realized_pnl: Decimal,
    fills: list[Any],
) -> Metrics:
    trades = reconstruct_completed_trades(fills)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_profit = sum((t.pnl for t in wins), Decimal(0))
    gross_loss = sum((-t.pnl for t in losses), Decimal(0))
    count = len(trades)

    win_rate: Decimal | str = (
        UNAVAILABLE_NO_TRADES if count == 0 else Decimal(len(wins)) / Decimal(count)
    )
    expectancy: Decimal | str = (
        UNAVAILABLE_NO_TRADES
        if count == 0
        else sum((t.pnl for t in trades), Decimal(0)) / Decimal(count)
    )
    if gross_loss > 0:
        profit_factor: Decimal | str = gross_profit / gross_loss
    elif count > 0:
        profit_factor = (
            NOT_DEFINED_NO_LOSSES  # no losses to divide by; never a fabricated Infinity
        )
    else:
        profit_factor = UNAVAILABLE_NO_TRADES

    return Metrics(
        starting_equity=starting_equity,
        ending_equity=ending_equity,
        net_change=ending_equity - starting_equity,
        realized_pnl=realized_pnl,
        completed_trade_count=count,
        win_count=len(wins),
        loss_count=len(losses),
        win_rate=win_rate,
        expectancy=expectancy,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
    )


@dataclass(frozen=True)
class RunAssumptions:
    """The shared assumptions two runs must match before a diagnostic comparison is
    meaningful (docs/M2_IMPLEMENTATION_HANDOFF.md step 9 "Comparison must reject
    unexplained differences")."""

    universe: tuple[str, ...]
    cost_profile: str
    risk_profile: str
    execution_mode: str
    date_window: tuple[str, str]


class ComparisonError(Exception):
    """Raised when two runs differ in an assumption other than the one being deliberately
    isolated; a comparison must never silently attribute P&L change to the wrong cause."""


def compare_runs(
    a: RunAssumptions,
    b: RunAssumptions,
    *,
    permitted_difference: str | None = None,
) -> dict[str, tuple[Any, Any]]:
    """Works for any two same-type frozen assumptions dataclass instances, not only
    `RunAssumptions` -- e.g. a synthetic schema with its own assumption fields."""
    if type(a) is not type(b):
        raise ComparisonError("assumption types differ")
    if permitted_difference is not None and permitted_difference not in {
        f.name for f in fields(a)
    }:
        raise ComparisonError("unknown permitted difference")
    diffs = {}
    for field in fields(a):
        va, vb = getattr(a, field.name), getattr(b, field.name)
        if va != vb:
            diffs[field.name] = (va, vb)
    unexpected = set(diffs) - (
        {permitted_difference} if permitted_difference else set()
    )
    if unexpected:
        raise ComparisonError(
            f"unexplained assumption differences: {sorted(unexpected)}"
        )
    return diffs

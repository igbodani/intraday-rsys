"""Decimal ledger: total/spendable/unsettled/reserved cash, open-position basis and realized
P&L. See docs/M2_IMPLEMENTATION_HANDOFF.md step 2 (M2-01b) for the account identities this
implements: `equity = total_cash + marked_holdings`; reservation is an allocation of spendable
cash, not an extra expense; a settlement release moves cash between buckets without changing
total cash or equity.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rsys.orders import INSUFFICIENT_CASH, INSUFFICIENT_SPENDABLE_CASH, Position


class AcceptanceError(Exception):
    """Raised with a declared rejection reason; callers must not invent a different one."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Portfolio:
    total_cash: Decimal
    spendable_cash: Decimal
    unsettled_cash: Decimal
    reserved_cash: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        self.positions: dict[str, Position] = {}

    @property
    def available_cash(self) -> Decimal:
        """Spendable cash not already committed to a pending reservation."""
        return self.spendable_cash - self.reserved_cash

    def check_affordable(self, needed: Decimal) -> None:
        """Raise the declared reason without mutating state (F04 vs F15)."""
        if self.total_cash < needed:
            raise AcceptanceError(INSUFFICIENT_CASH)
        if self.available_cash < needed:
            raise AcceptanceError(INSUFFICIENT_SPENDABLE_CASH)

    def reserve(self, amount: Decimal) -> None:
        self.check_affordable(amount)
        self.reserved_cash += amount

    def release_reservation(self, amount: Decimal) -> None:
        self.reserved_cash = max(Decimal(0), self.reserved_cash - amount)

    def apply_settlement_release(self, amount: Decimal) -> None:
        """Move already-reconciled proceeds from unsettled to spendable; total cash/equity
        are unaffected because this is a bucket transfer, not a flow."""
        self.unsettled_cash -= amount
        self.spendable_cash += amount

    def buy(
        self, symbol: str, quantity: Decimal, price: Decimal, fee: Decimal,
        released_reservation: Decimal, stop: Decimal | None, target: Decimal | None,
    ) -> Position:
        basis = quantity * price + fee
        self.total_cash -= basis
        self.spendable_cash -= basis
        self.release_reservation(released_reservation)
        position = Position(
            symbol=symbol, quantity=quantity, entry_price=price, basis=basis,
            stop=stop, target=target, mark=price,
        )
        self.positions[symbol] = position
        return position

    def sell(self, symbol: str, price: Decimal, fee: Decimal, settles_immediately: bool = True) -> Decimal:
        """Close the entire position; returns trade-level realized P&L."""
        position = self.positions.pop(symbol)
        proceeds = position.quantity * price - fee
        self.total_cash += proceeds
        if settles_immediately:
            self.spendable_cash += proceeds
        else:
            self.unsettled_cash += proceeds
        trade_pnl = proceeds - position.basis
        self.realized_pnl += trade_pnl
        return trade_pnl

    def equity(self, marks: dict[str, Decimal]) -> Decimal:
        holdings = sum(
            (position.quantity * marks.get(symbol, position.mark) for symbol, position in self.positions.items()),
            Decimal(0),
        )
        return self.total_cash + holdings

    def mark_position(self, symbol: str, price: Decimal) -> None:
        if symbol in self.positions:
            self.positions[symbol].mark = price

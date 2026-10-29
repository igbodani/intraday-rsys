"""Validated Decimal account transitions; reservations never create or destroy money."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal

from rsys.money import amount
from rsys.orders import INSUFFICIENT_CASH, INSUFFICIENT_SPENDABLE_CASH, Position


class AcceptanceError(ValueError):
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

    def __post_init__(self):
        self.positions: dict[str, Position] = {}
        self._reservations: dict[str, Decimal] = {}
        self._reservation_counter = 0
        self._reservation_history = {}
        self._transactions: dict[str, tuple[tuple, object]] = {}
        if self.reserved_cash:
            raise ValueError("initial reservations require owned intents")
        self.assert_invariants()

    def assert_invariants(self):
        for name in ("total_cash", "spendable_cash", "unsettled_cash", "reserved_cash"):
            amount(getattr(self, name), name)
        if (
            not isinstance(self.realized_pnl, Decimal)
            or not self.realized_pnl.is_finite()
        ):
            raise ValueError("realized P&L must be finite Decimal")
        if self.total_cash != self.spendable_cash + self.unsettled_cash:
            raise ValueError("cash buckets do not reconcile")
        if self.reserved_cash != sum(self._reservations.values(), Decimal(0)):
            raise ValueError("reservation ownership does not reconcile")
        if self.available_cash < 0:
            raise ValueError("reservations exceed spendable cash")
        for p in self.positions.values():
            amount(p.quantity, "position quantity", positive=True)
            amount(p.mark, "position mark", positive=True)
            amount(p.basis, "position basis")

    @property
    def available_cash(self):
        return self.spendable_cash - self.reserved_cash

    def check_affordable(self, needed):
        amount(needed, "needed cash")
        if self.total_cash < needed:
            raise AcceptanceError(INSUFFICIENT_CASH)
        if self.available_cash < needed:
            raise AcceptanceError(INSUFFICIENT_SPENDABLE_CASH)

    def reserve(self, value, reservation_id=None):
        amount(value, "reservation", positive=True)
        if reservation_id in self._reservations:
            if self._reservations[reservation_id] != value:
                raise ValueError("conflicting reservation identity")
            return reservation_id
        if reservation_id in self._reservation_history:
            raise ValueError("cannot reuse a retired reservation identity")
        self.check_affordable(value)
        if reservation_id is None:
            while (
                f"reservation:{self._reservation_counter}" in self._reservation_history
            ):
                self._reservation_counter += 1
            reservation_id = f"reservation:{self._reservation_counter}"
            self._reservation_counter += 1
        self._reservation_history[reservation_id] = value
        self._reservations[reservation_id] = value
        self.reserved_cash += value
        return reservation_id

    def has_reservation(self, reservation_id):
        return reservation_id in self._reservations

    def _release_key(self, value, reservation_id=None):
        amount(value, "released reservation")
        if value == 0:
            return None
        if reservation_id is not None:
            if (
                reservation_id in self._reservation_history
                and self._reservation_history[reservation_id] != value
            ):
                raise ValueError("reservation amount mismatch")
            if reservation_id not in self._reservations:
                return None  # already released: cannot release another intent's funds
            if self._reservations[reservation_id] != value:
                raise ValueError("reservation amount mismatch")
            return reservation_id
        matches = [k for k, v in self._reservations.items() if v == value]
        if not matches and not self._reservations:
            return None
        if len(matches) != 1:
            raise ValueError("release requires a matching owned reservation")
        return matches[0]

    def release_reservation(self, value, reservation_id=None):
        key = self._release_key(value, reservation_id)
        if key is not None:
            self.reserved_cash -= self._reservations.pop(key)

    def apply_settlement_release(self, value, *, transaction_id=None):
        payload = ("settlement", value)
        repeated, _ = self._repeated(transaction_id, payload)
        if repeated:
            return
        amount(value, "settlement release", positive=True)
        if value > self.unsettled_cash:
            raise ValueError("release exceeds unsettled proceeds")
        self.unsettled_cash -= value
        self.spendable_cash += value
        if transaction_id is not None:
            self._transactions[transaction_id] = (payload, None)
        self.assert_invariants()

    def _repeated(self, transaction_id, payload):
        if transaction_id is not None and transaction_id in self._transactions:
            old, result = self._transactions[transaction_id]
            if old != payload:
                raise ValueError("conflicting transaction identity")
            return True, deepcopy(result)
        return False, None

    def buy(
        self,
        symbol,
        quantity,
        price,
        fee,
        released_reservation,
        stop,
        target,
        *,
        reservation_id=None,
        transaction_id=None,
    ):
        payload = (
            "buy",
            symbol,
            quantity,
            price,
            fee,
            released_reservation,
            stop,
            target,
            reservation_id,
        )
        repeated, result = self._repeated(transaction_id, payload)
        if repeated:
            return result
        amount(quantity, "quantity", positive=True)
        amount(price, "price", positive=True)
        amount(fee, "fee")
        if symbol in self.positions:
            raise AcceptanceError("DUPLICATE_PENDING_OR_HELD")
        if stop is not None:
            amount(stop, "stop", positive=True)
            if price <= stop:
                raise AcceptanceError("ENTRY_THROUGH_STOP")
        if target is not None:
            amount(target, "target", positive=True)
        if reservation_id is not None and not self.has_reservation(reservation_id):
            raise ValueError("entry requires its active reservation")
        key = self._release_key(released_reservation, reservation_id)
        owned = self._reservations[key] if key is not None else Decimal(0)
        basis = quantity * price + fee
        if basis > self.total_cash:
            raise AcceptanceError(INSUFFICIENT_CASH)
        if basis > self.available_cash + owned:
            raise AcceptanceError(INSUFFICIENT_SPENDABLE_CASH)
        position = Position(symbol, quantity, price, basis, stop, target, price)
        self.total_cash -= basis
        self.spendable_cash -= basis
        if key is not None:
            self.reserved_cash -= self._reservations.pop(key)
        self.positions[symbol] = position
        if transaction_id is not None:
            self._transactions[transaction_id] = (payload, deepcopy(position))
        self.assert_invariants()
        return position

    def sell(
        self, symbol, price, fee, settles_immediately=True, *, transaction_id=None
    ):
        payload = ("sell", symbol, price, fee, settles_immediately)
        repeated, result = self._repeated(transaction_id, payload)
        if repeated:
            return result
        amount(price, "price", positive=True)
        amount(fee, "fee")
        if symbol not in self.positions:
            raise ValueError("no position to sell")
        position = self.positions[symbol]
        proceeds = position.quantity * price - fee
        if proceeds < 0:
            raise ValueError("exit costs exceed proceeds: unsupported account debit")
        pnl = proceeds - position.basis
        del self.positions[symbol]
        self.total_cash += proceeds
        if settles_immediately:
            self.spendable_cash += proceeds
        else:
            self.unsettled_cash += proceeds
        self.realized_pnl += pnl
        if transaction_id is not None:
            self._transactions[transaction_id] = (payload, pnl)
        self.assert_invariants()
        return pnl

    def equity(self, marks):
        for value in marks.values():
            amount(value, "mark", positive=True)
        return self.total_cash + sum(
            (p.quantity * marks.get(s, p.mark) for s, p in self.positions.items()),
            Decimal(0),
        )

    def mark_position(self, symbol, price):
        amount(price, "mark", positive=True)
        if symbol in self.positions:
            self.positions[symbol].mark = price

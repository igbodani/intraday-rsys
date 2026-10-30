"""Account data models and their intrinsic validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from rsys.account.money import amount


@dataclass(frozen=True)
class Proposal:
    """A sized, not-yet-accepted trade intent for one instrument."""

    symbol: str
    quantity: Decimal
    signal_price: Decimal
    stop: Decimal | None
    target: Decimal | None
    decision_time_ms: int
    decision_latency_ms: int = 0
    scanner_rank: int | None = None
    strategy_id: str = "fixture"
    proposal_id: str | None = None
    expiry_ms: int | None = None
    max_hold_ms: int | None = None

    def __post_init__(self):
        amount(self.quantity, "proposal quantity")
        amount(self.signal_price, "signal price", positive=True)
        for name in ("stop", "target"):
            if getattr(self, name) is not None:
                amount(getattr(self, name), name, positive=True)
        for name in (
            "decision_time_ms",
            "decision_latency_ms",
            "expiry_ms",
            "max_hold_ms",
        ):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.scanner_rank is not None and (
            type(self.scanner_rank) is not int or self.scanner_rank < 1
        ):
            raise ValueError("scanner rank must be positive")

    @property
    def ready_at_ms(self) -> int:
        return self.decision_time_ms + self.decision_latency_ms

    @property
    def expires_at_ms(self) -> int | None:
        return (
            None if self.expiry_ms is None else self.decision_time_ms + self.expiry_ms
        )


@dataclass(frozen=True)
class AcceptedIntent:
    """A proposal that has reserved cash and is waiting for an eligible open."""

    proposal: Proposal
    reserved_cash: Decimal
    reservation_id: str | None = None
    acceptance_sequence: int = 0


@dataclass
class Position:
    """One open long position. Mutable in place (stop/target changes, mark updates)."""

    symbol: str
    quantity: Decimal
    entry_price: Decimal
    basis: Decimal  # total cash paid to enter, including entry fee
    stop: Decimal | None
    target: Decimal | None
    mark: Decimal


@dataclass(frozen=True)
class Fill:
    """A deterministic record of one entry or exit fill."""

    symbol: str
    side: str  # "buy" or "sell"
    quantity: Decimal
    price: Decimal
    fee: Decimal
    reason: str
    time_ms: int


@dataclass
class Checkpoint:
    """One observable ledger snapshot, recorded after a notable event (fill, exit, rejection or
    a completed-bar mark update). The ordered sequence of checkpoints is compared against a
    fixture case's `expected` list."""

    cash: Decimal
    quantity: Decimal
    mark: Decimal
    equity: Decimal
    realized_pnl: Decimal
    label: str = field(default="")


@dataclass(frozen=True)
class RiskPolicy:
    per_trade_fraction: Decimal
    daily_loss_fraction: Decimal
    maximum_drawdown_fraction: Decimal
    daily_loss_basis: str
    halt_action: str

    def __post_init__(self):
        for field_name in (
            "per_trade_fraction",
            "daily_loss_fraction",
            "maximum_drawdown_fraction",
        ):
            value = getattr(self, field_name)
            amount(value, field_name, positive=True)
            if value >= 1:
                raise ValueError(f"{field_name} must be less than one")
        if self.daily_loss_basis != "session_start_equity":
            raise ValueError("daily_loss_basis must be session_start_equity")
        if self.halt_action != "cancel_entries_keep_exits":
            raise ValueError("halt_action must be cancel_entries_keep_exits")


@dataclass
class SizingPolicy:
    risk_budget: Decimal | None = None
    max_quantity: Decimal | None = None
    quantity_step: Decimal = Decimal(1)
    entry_fee: Decimal = Decimal(0)
    exit_fee: Decimal = Decimal(0)

    def __post_init__(self):
        for name in (
            "entry_fee",
            "exit_fee",
            "risk_budget",
            "max_quantity",
            "quantity_step",
        ):
            value = getattr(self, name)
            if value is not None:
                amount(value, name, positive=name in ("max_quantity", "quantity_step"))


RejectionReason = (
    str  # one of the string constants below; kept as str for easy JSON round-trip.
)


INVALID_STOP_DISTANCE = "INVALID_STOP_DISTANCE"


INSUFFICIENT_CASH = "INSUFFICIENT_CASH"


INSUFFICIENT_SPENDABLE_CASH = "INSUFFICIENT_SPENDABLE_CASH"


ENTRY_THROUGH_STOP = "ENTRY_THROUGH_STOP"


RISK_BUDGET = "RISK_BUDGET"


NO_CURRENT_RANK = "NO_CURRENT_RANK"

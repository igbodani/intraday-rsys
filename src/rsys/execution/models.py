"""Execution data models and their intrinsic validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from rsys.account.models import Checkpoint
from rsys.account.money import amount


@dataclass(frozen=True)
class FillTransition:
    """One fully-formed entry or exit fill, independent of which adapter produced it."""

    instrument: str
    intent_identity: (
        str  # the owning reservation id (entry) or the exited position's own
    )
    activation_key: (
        str  # stable for one open position's lifetime (e.g. "symbol:fill_count")
    )
    execution_reference_id: str  # bar-open event id or subsequent-trade trade_id
    availability_key: int  # available_at_ms: when this fill is knowable to the account
    quantity: Decimal
    price: Decimal
    fee: Decimal
    reason: str
    market_time_ms: int | None = (
        None  # None for a modeled OHLC touch; set for a real print
    )

    def __post_init__(self) -> None:
        amount(self.quantity, "transition quantity", positive=True)
        amount(self.price, "transition price", positive=True)
        amount(self.fee, "transition fee")
        if (
            not self.instrument
            or not self.intent_identity
            or not self.execution_reference_id
        ):
            raise ValueError(
                "transition requires instrument, intent and reference identity"
            )
        if not self.reason:
            raise ValueError("transition requires a reason")


@dataclass(frozen=True)
class RejectedEntry:
    """A fill-time entry decision that must release its reservation, never a fill."""

    reason: str


@dataclass(frozen=True)
class GapExitDecision:
    """A bar-open exit priority outcome: `kind` is "pending" (an existing timer/flatten
    exit), "stop" or "target". Each engine maps `kind` to its own retained label."""

    kind: str
    price: Decimal


@dataclass
class PendingExit:
    scheduled_time_ms: int
    reason: str  # "flatten" or "time_exit"


@dataclass
class FixtureCaseResult:
    case_id: str
    checkpoints: list[Checkpoint] = field(default_factory=list)
    rejection: str | None = None
    final_quantity: Decimal = Decimal(0)
    final_cash: Decimal = Decimal(0)
    final_equity: Decimal = Decimal(0)
    final_unsettled_cash: Decimal = Decimal(0)
    reserved_cash: Decimal = Decimal(0)
    sized_quantity: Decimal | None = None
    sized_reservation: Decimal | None = None
    sized_initial_risk: Decimal | None = None
    entry_fill: Decimal | None = None
    exit_price: Decimal | None = None
    total_fees: Decimal = Decimal(0)
    first_rejection: str | None = None
    entry_time_ms: int | None = None
    flatten_time_ms: int | None = None
    positions: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    acceptance_order: list[str] = field(default_factory=list)
    features: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionCosts:
    adverse_bps: Decimal = Decimal(0)
    feed_delay_ms: int = 0

    def __post_init__(self):
        amount(self.adverse_bps, "adverse_bps")
        if self.adverse_bps >= 10000:
            raise ValueError("adverse_bps must be less than 10000")
        if type(self.feed_delay_ms) is not int or self.feed_delay_ms < 0:
            raise ValueError("feed_delay_ms must be a nonnegative integer")

    def buy_price(self, reference: Decimal) -> Decimal:
        return reference * (1 + self.adverse_bps / 10000)

    def sell_price(self, reference: Decimal) -> Decimal:
        return reference * (1 - self.adverse_bps / 10000)


@dataclass
class DecisionRecord:
    available_at_ms: int
    candidates: list[str]
    proposed: list[str]
    accepted: list[str]
    rejected: dict[str, str]
    input_versions: tuple[str, ...] = ()
    ranked: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)
    scanner_evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FillRecord:
    available_at_ms: int
    symbol: str
    status: str
    price: Decimal | None = None
    quantity: Decimal | None = None
    target: Decimal | None = None
    fee: Decimal = Decimal(0)
    net_pnl: Decimal | None = None
    reason: str | None = None
    # An OHLC touch has no exact market timestamp; availability is its bar publication.
    event_time_ms: int | None = None
    reference_id: str | None = None
    reference_price: Decimal | None = None
    execution_cost: Decimal = Decimal(0)
    risk_budget: Decimal | None = None
    estimated_stop_loss: Decimal | None = None


@dataclass
class ExecutionPolicy:
    max_hold_ms: int | None = None
    flatten_at_ms: int | None = None
    decision_latency_ms: int = 0

    def __post_init__(self):
        for name in ("max_hold_ms", "flatten_at_ms", "decision_latency_ms"):
            value = getattr(self, name)
            if value is not None and (
                type(value) is not int or value < (1 if name == "max_hold_ms" else 0)
            ):
                raise ValueError(f"invalid {name}")


@dataclass(frozen=True)
class Trade:
    trade_id: str
    event_time_ms: int
    available_at_ms: int
    price: Decimal
    quality_ok: bool = True
    is_correction: bool = False
    size: Decimal = Decimal(
        1
    )  # explicit unit-size default for legacy synthetic fixtures
    conditions: tuple[str, ...] = ()
    tape: str | None = None

    def __post_init__(self):
        amount(self.price, "trade price", positive=True)
        amount(self.size, "trade size", positive=True)
        if type(self.quality_ok) is not bool or type(self.is_correction) is not bool:
            raise ValueError("trade quality/correction flags must be booleans")
        if not self.trade_id or any(
            type(v) is not int or v < 0
            for v in (self.event_time_ms, self.available_at_ms)
        ):
            raise ValueError(
                "trade requires identity and nonnegative integer timestamps"
            )
        if self.available_at_ms < self.event_time_ms:
            raise ValueError("trade availability precedes market time")


@dataclass(frozen=True)
class FillOutcome:
    status: str
    price: Decimal | None = None
    trade_id: str | None = None
    reason: str | None = None
    fee: Decimal = Decimal(0)

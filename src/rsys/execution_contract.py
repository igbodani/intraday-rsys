"""Shared fill/account transition contract (Step 1 of docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md).

Both the fixture account interpreter (`fixture_engine.py`) and the synthetic session
runner (`standalone_run.py`) independently implemented a bar-open entry/exit execution
timing. This module extracts the genuinely shared pieces -- fill-time cash/risk resizing,
the pending-exit/stop/target priority at one open, and the account-commit boundary -- behind
one explicit `FillTransition` contract, without merging the two engines' declared execution
differences (fixture models adverse slippage; the session runner does not; their expiry/
ready-at ordering stays each engine's own, since it is not a documented shared rule).

`decide_bar_open_entry`/`decide_bar_open_gap_exit` are adapters: they select an eligible
reference/price and raise nothing on a cash/risk failure (it becomes a `RejectedEntry`).
They never touch `Portfolio` themselves. `commit_entry`/`commit_exit` are the account
service: the one place either engine actually calls `Portfolio.buy`/`sell`, keyed by a
`transaction_id` so a duplicated dispatch can never double-charge (see
`Portfolio._repeated`). A modeled OHLC touch has no exact market time, so
`FillTransition.market_time_ms` stays `None` for every bar-open fill; only a subsequent-trade
fill (`trade_execution.py`) can set it, via `from_trade_outcome`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from rsys.money import amount
from rsys.orders import ENTRY_THROUGH_STOP
from rsys.portfolio import AcceptanceError
from rsys.sizing import size_quantity

if TYPE_CHECKING:
    from rsys.orders import Position, Proposal
    from rsys.portfolio import Portfolio
    from rsys.trade_execution import FillOutcome

# Same-time phase ordering shared by every bar-open engine: a due exit is dispatched
# strictly before a new entry at an identical availability point, so a symbol that both
# exits and would re-enter at the same open never double-books cash in one instant.
EXIT_PHASE = 60
ENTRY_PHASE = 70


@dataclass(frozen=True)
class FillTransition:
    """One fully-formed entry or exit fill, independent of which adapter produced it."""

    instrument: str
    intent_identity: str  # the owning reservation id (entry) or the exited position's own
    activation_key: str  # stable for one open position's lifetime (e.g. "symbol:fill_count")
    execution_reference_id: str  # bar-open event id or subsequent-trade trade_id
    availability_key: int  # available_at_ms: when this fill is knowable to the account
    quantity: Decimal
    price: Decimal
    fee: Decimal
    reason: str
    market_time_ms: int | None = None  # None for a modeled OHLC touch; set for a real print

    def __post_init__(self) -> None:
        amount(self.quantity, "transition quantity", positive=True)
        amount(self.price, "transition price", positive=True)
        amount(self.fee, "transition fee")
        if not self.instrument or not self.intent_identity or not self.execution_reference_id:
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


def resize_at_fill(
    proposal: Proposal,
    modeled_price: Decimal,
    available_cash_with_reservation: Decimal,
    *,
    entry_fee: Decimal,
    exit_fee: Decimal,
    risk_budget: Decimal | None,
    quantity_step: Decimal,
    exit_slippage_bps: Decimal = Decimal(0),
) -> tuple[Decimal, Decimal]:
    """Shared fill-time resize: the real engine must recheck cash/risk against the actual
    modeled price and current cash, never upsizing past the originally accepted quantity.
    Raises `AcceptanceError` (from `size_quantity`) on an unaffordable or over-risk gap."""
    return size_quantity(
        entry=modeled_price,
        stop=proposal.stop * (1 - exit_slippage_bps / 10000),
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        risk_budget=risk_budget,
        available_cash=available_cash_with_reservation,
        quantity_step=quantity_step,
        max_quantity=proposal.quantity,
    )


def decide_bar_open_entry(
    proposal: Proposal,
    point_ms: int,
    open_price: Decimal,
    slippage: Decimal,
    available_cash_with_reservation: Decimal,
    *,
    entry_fee: Decimal,
    exit_fee: Decimal,
    risk_budget: Decimal | None,
    quantity_step: Decimal,
    exit_slippage_bps: Decimal = Decimal(0),
) -> tuple[Decimal, Decimal] | RejectedEntry:
    """Models the fill at `open_price + slippage` (slippage is 0 for an engine that does
    not declare an adverse-slippage cost). Returns `(modeled_price, quantity)` on a fill or
    a `RejectedEntry` naming the reason. Does not decide expiry or activation-readiness --
    those stay in each engine's own declared control flow."""
    modeled_price = open_price + slippage
    if proposal.stop is not None and (open_price <= proposal.stop or modeled_price <= proposal.stop):
        return RejectedEntry(ENTRY_THROUGH_STOP)
    try:
        quantity, _ = resize_at_fill(
            proposal,
            modeled_price,
            available_cash_with_reservation,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            risk_budget=risk_budget,
            quantity_step=quantity_step,
            exit_slippage_bps=exit_slippage_bps,
        )
    except AcceptanceError as exc:
        return RejectedEntry(exc.reason)
    return modeled_price, quantity


def decide_bar_open_gap_exit(
    position: Position, open_price: Decimal, slippage: Decimal, pending_due: bool
) -> GapExitDecision | None:
    """Shared terminal-order priority at one bar open: an existing due exit (flatten/
    max-hold/time-exit) outranks a stop, which outranks a target. Returns `None` when
    nothing triggers (a mark-only update)."""
    if pending_due:
        return GapExitDecision("pending", open_price - slippage)
    if position.stop is not None and open_price <= position.stop:
        return GapExitDecision("stop", open_price - slippage)
    if position.target is not None and open_price >= position.target:
        return GapExitDecision("target", position.target)
    return None


def commit_entry(
    portfolio: Portfolio,
    transition: FillTransition,
    *,
    stop: Decimal | None,
    target: Decimal | None,
    reserved_cash: Decimal,
) -> Position:
    """The one place either bar-open engine calls `Portfolio.buy`. Keyed by
    `transition.execution_reference_id` so a repeated dispatch of the same fill event
    commits exactly once."""
    return portfolio.buy(
        transition.instrument,
        transition.quantity,
        transition.price,
        transition.fee,
        reserved_cash,
        stop,
        target,
        reservation_id=transition.intent_identity,
        transaction_id=transition.execution_reference_id,
    )


def commit_exit(
    portfolio: Portfolio, transition: FillTransition, *, settles_immediately: bool = True
) -> Decimal:
    """The one place either bar-open engine calls `Portfolio.sell`. Keyed the same way as
    `commit_entry` for exactly-once commit."""
    return portfolio.sell(
        transition.instrument,
        transition.price,
        transition.fee,
        settles_immediately,
        transaction_id=transition.execution_reference_id,
    )


def from_trade_outcome(
    outcome: FillOutcome,
    *,
    instrument: str,
    intent_identity: str,
    activation_key: str,
    availability_key: int,
    market_time_ms: int,
    quantity: Decimal,
) -> FillTransition:
    """Converts a subsequent-trade adapter's `FILLED` outcome into the same
    `FillTransition` contract a bar-open adapter produces, so the account-commit boundary
    is identical regardless of adapter. `TradeInputRunner` supplies session/instrument
    scoped transaction references before committing. `FillOutcome` carries no quantity -- the caller's own proposal/
    position already owns it -- so it is supplied explicitly."""
    if outcome.status != "FILLED":
        raise ValueError(f"cannot convert a non-FILLED outcome: {outcome.status!r}")
    if outcome.price is None or outcome.trade_id is None:
        raise ValueError("a FILLED outcome requires a price and trade_id")
    return FillTransition(
        instrument=instrument,
        intent_identity=intent_identity,
        activation_key=activation_key,
        execution_reference_id=outcome.trade_id,
        availability_key=availability_key,
        quantity=quantity,
        price=outcome.price,
        fee=outcome.fee,
        reason=outcome.reason or "FILLED",
        market_time_ms=market_time_ms,
    )

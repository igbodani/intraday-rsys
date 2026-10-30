"""Shared execution decisions and exactly-once account commits.

Bar and trade runners share cost-aware quantity revalidation and the FillTransition
commit boundary. Each runner owns eligibility and timing for its execution references.
Modeled OHLC touches have no exact market time; subsequent prints retain market time.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from rsys.account.errors import AcceptanceError
from rsys.account.models import ENTRY_THROUGH_STOP
from rsys.account.sizing import size_quantity
from rsys.execution.models import FillTransition, GapExitDecision, RejectedEntry

if TYPE_CHECKING:
    from rsys.account.models import Position, Proposal
    from rsys.account.portfolio import Portfolio
    from rsys.execution.models import FillOutcome

# Same-time phase ordering shared by every bar-open engine: a due exit is dispatched
# strictly before a new entry at an identical availability point, so a symbol that both
# exits and would re-enter at the same open never double-books cash in one instant.
EXIT_PHASE = 60
ENTRY_PHASE = 70


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
    if proposal.stop is not None and (
        open_price <= proposal.stop or modeled_price <= proposal.stop
    ):
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
    portfolio: Portfolio,
    transition: FillTransition,
    *,
    settles_immediately: bool = True,
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

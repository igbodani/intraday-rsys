"""Subsequent-trade execution adapter `subsequent_trade_estimate_v1` (M2-01T,
docs/M2_IMPLEMENTATION_HANDOFF.md step 5).

Fills on a later ELIGIBLE trade print instead of the next bar open. A trade already
consumed by the decision's own same-time batch, a trade whose own event time precedes the
decision (a late-delivered stale print), a correction/cancellation, or a trade failing the
declared quality policy can never represent a new execution opportunity -- regardless of
when it happens to arrive. Exits check stops before targets in the trades' own observed
order (not the bar-mode "stop always wins" rule, since a real trade sequence is known).

Honesty note: standalone module, not wired into `PhaseEngine`/`ReplayRunner`/real market
data; quote state is not modeled at all -- see docs/M2_IMPLEMENTATION_STATUS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rsys.money import amount
from rsys.orders import AcceptedIntent, Position

ENTRY_ADAPTER_ID = "subsequent_trade_estimate_v1"

REJECTED_STALE = "REJECTED_STALE"
REJECTED_SELF_CONSUMED = "REJECTED_SELF_CONSUMED"
REJECTED_NOT_READY = "REJECTED_NOT_READY"
REJECTED_BAD_QUALITY = "REJECTED_BAD_QUALITY"
REJECTED_ENTRY_THROUGH_STOP = "REJECTED_ENTRY_THROUGH_STOP"
ALREADY_FILLED = "ALREADY_FILLED"
FILLED = "FILLED"
SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class Trade:
    trade_id: str
    event_time_ms: int
    available_at_ms: int
    price: Decimal
    quality_ok: bool = True
    is_correction: bool = False

    def __post_init__(self):
        amount(self.price, "trade price", positive=True)
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


class TradeExecutionAdapter:
    """One instrument. Call `consider_entry` with every candidate trade in delivery order
    until it returns `FILLED`; then call `consider_exit` for every later trade. Corrections
    /cancellations are applied via `apply_correction`, never treated as a fresh trade."""

    def __init__(self, entry_fee: Decimal = Decimal(0), exit_fee: Decimal = Decimal(0)):
        amount(entry_fee, "entry fee")
        amount(exit_fee, "exit fee")
        self.entry_time_ms: int | None = None
        self.entry_available_ms: int | None = None
        self.exit_trade_id: str | None = None
        self._seen_trades: dict[str, Trade] = {}
        self.entry_fee = entry_fee
        self.exit_fee = exit_fee
        self.entry_trade_id: str | None = None
        self.invalidated_trade_ids: set[str] = set()

    def consider_entry(
        self,
        intent: AcceptedIntent,
        consumed_trade_ids: frozenset[str],
        trade: Trade,
    ) -> FillOutcome:
        self._validate_identity(trade)
        if self.entry_trade_id is not None:
            return FillOutcome(ALREADY_FILLED, trade_id=trade.trade_id)
        proposal = intent.proposal
        if (
            proposal.expires_at_ms is not None
            and trade.available_at_ms >= proposal.expires_at_ms
        ):
            return FillOutcome("REJECTED_EXPIRED", trade_id=trade.trade_id)
        if trade.is_correction or trade.trade_id in self.invalidated_trade_ids:
            return FillOutcome(
                REJECTED_STALE,
                trade_id=trade.trade_id,
                reason="correction, not a fresh trade",
            )
        if not trade.quality_ok:
            return FillOutcome(REJECTED_BAD_QUALITY, trade_id=trade.trade_id)
        if trade.trade_id in consumed_trade_ids:
            return FillOutcome(REJECTED_SELF_CONSUMED, trade_id=trade.trade_id)
        if trade.available_at_ms < proposal.ready_at_ms:
            return FillOutcome(REJECTED_NOT_READY, trade_id=trade.trade_id)
        if (
            trade.event_time_ms <= proposal.decision_time_ms
            or trade.event_time_ms < proposal.ready_at_ms
        ):
            return FillOutcome(
                REJECTED_STALE, trade_id=trade.trade_id, reason="pre-activation print"
            )
        if proposal.stop is not None and trade.price <= proposal.stop:
            return FillOutcome(REJECTED_ENTRY_THROUGH_STOP, trade_id=trade.trade_id)
        self.entry_trade_id = trade.trade_id
        self.entry_time_ms = trade.event_time_ms
        self.entry_available_ms = trade.available_at_ms
        return FillOutcome(
            FILLED, price=trade.price, trade_id=trade.trade_id, fee=self.entry_fee
        )

    def _validate_identity(self, trade):
        old = self._seen_trades.get(trade.trade_id)
        if old is not None and old != trade:
            raise ValueError("conflicting trade identity")
        self._seen_trades[trade.trade_id] = trade

    def activate_seeded_position(self, trade_id, event_time_ms, available_at_ms):
        if available_at_ms < event_time_ms:
            raise ValueError("invalid position activation")
        self.entry_trade_id = trade_id
        self.entry_time_ms = event_time_ms
        self.entry_available_ms = available_at_ms

    def consider_exit(self, position: Position, trade: Trade) -> FillOutcome:
        self._validate_identity(trade)
        if self.exit_trade_id is not None:
            return FillOutcome(ALREADY_FILLED, trade_id=trade.trade_id)
        if trade.is_correction or trade.trade_id in self.invalidated_trade_ids:
            return FillOutcome(REJECTED_STALE, trade_id=trade.trade_id)
        if not trade.quality_ok:
            return FillOutcome(REJECTED_BAD_QUALITY, trade_id=trade.trade_id)
        if trade.trade_id == self.entry_trade_id:
            return FillOutcome(
                REJECTED_SELF_CONSUMED,
                trade_id=trade.trade_id,
                reason="entry trade cannot also exit",
            )
        if self.entry_time_ms is None or self.entry_available_ms is None:
            raise ValueError("position activation timing is required")
        if (
            trade.event_time_ms <= self.entry_time_ms
            or trade.available_at_ms <= self.entry_available_ms
        ):
            return FillOutcome(REJECTED_STALE, trade_id=trade.trade_id)
        if position.stop is not None and trade.price <= position.stop:
            self.exit_trade_id = trade.trade_id
            return FillOutcome(
                FILLED,
                price=trade.price,
                trade_id=trade.trade_id,
                reason="stop",
                fee=self.exit_fee,
            )
        if position.target is not None and trade.price >= position.target:
            self.exit_trade_id = trade.trade_id
            return FillOutcome(
                FILLED,
                price=position.target,
                trade_id=trade.trade_id,
                reason="target",
                fee=self.exit_fee,
            )
        return FillOutcome(SKIPPED, trade_id=trade.trade_id)

    def apply_correction(self, corrected_trade_id: str) -> None:
        """Corrections/cancellations invalidate the referenced trade causally; they never
        undo a fill that already used it as its reference."""
        self.invalidated_trade_ids.add(corrected_trade_id)

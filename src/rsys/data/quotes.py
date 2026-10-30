"""Optional quote state (R3-B / docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md 7.4).

Quotes update quote state only -- they never contribute to a trade bar's OHLC,
volume or VWAP (that remains exclusively `TradeBarBuilder`'s concern). Each
accepted `QuoteEvent` produces a new, immutable `QuoteSnapshot` capturing
bid/ask prices and sizes, source identity, event/availability time and an
explicit condition classification (locked/crossed/missing-side/normal). A
displayed quote size is not a guaranteed fill; nothing here claims otherwise.

Freshness is computed against the *decision clock* (the cutoff a strategy
context is frozen at), never against wall-clock "now" -- so freshness
evaluation itself stays replay-deterministic.
"""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from rsys.data.events import QuoteEvent


class QuoteCondition(StrEnum):
    NORMAL = "normal"  # both sides present, bid < ask
    LOCKED = "locked"  # both sides present, bid == ask
    CROSSED = "crossed"  # both sides present, bid > ask
    MISSING_BID = "missing_bid"
    MISSING_ASK = "missing_ask"
    MISSING_BOTH = "missing_both"


class FreshnessState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"


def classify_quote_condition(bid_price: Decimal | None, ask_price: Decimal | None) -> QuoteCondition:
    missing_bid = bid_price is None
    missing_ask = ask_price is None
    if missing_bid and missing_ask:
        return QuoteCondition.MISSING_BOTH
    if missing_bid:
        return QuoteCondition.MISSING_BID
    if missing_ask:
        return QuoteCondition.MISSING_ASK
    if bid_price == ask_price:
        return QuoteCondition.LOCKED
    if bid_price > ask_price:
        return QuoteCondition.CROSSED
    return QuoteCondition.NORMAL


class QuoteSnapshot(BaseModel):
    """One immutable, frozen quote version -- never mutated after construction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    feed: str
    venue: str
    source_event_id: str
    event_time_ns: int
    available_at_ns: int
    bid_price: Decimal | None
    bid_size: Decimal | None
    ask_price: Decimal | None
    ask_size: Decimal | None
    # Explicit size units: this fixture/replay slice carries round-lot share counts as-is;
    # a real adapter must convert/declare units rather than silently assume round lots.
    size_unit: str
    condition: QuoteCondition

    @classmethod
    def from_event(cls, event: QuoteEvent, *, size_unit: str = "shares") -> QuoteSnapshot:
        return cls(
            instrument_id=event.instrument_id, feed=event.feed, venue=event.venue,
            source_event_id=event.event_id, event_time_ns=event.event_time_ns,
            available_at_ns=event.available_at_ns, bid_price=event.bid_price, bid_size=event.bid_size,
            ask_price=event.ask_price, ask_size=event.ask_size, size_unit=size_unit,
            condition=classify_quote_condition(event.bid_price, event.ask_price),
        )


class QuoteFreshnessPolicy(BaseModel):
    """An explicit, declared freshness rule; required whenever quotes are enabled (7.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    freshness_limit_ms: int

    @model_validator(mode="after")
    def _check_positive_limit(self) -> QuoteFreshnessPolicy:
        if self.freshness_limit_ms <= 0:
            raise ValueError("freshness_limit_ms must be a positive integer")
        return self

    def classify(self, quote: QuoteSnapshot, decision_clock_ns: int) -> FreshnessState:
        if decision_clock_ns < quote.available_at_ns:
            raise ValueError("decision clock cannot precede the quote's own availability")
        age_ns = decision_clock_ns - quote.available_at_ns
        limit_ns = self.freshness_limit_ms * 1_000_000
        return FreshnessState.FRESH if age_ns <= limit_ns else FreshnessState.STALE


class MismatchedQuoteSeriesError(Exception):
    """A quote for a different instrument/feed was routed at this series' quote state."""


class QuoteState:
    """Latest quote for an instrument/feed, in nondecreasing availability order.

    Equal-time events follow the runner's source sequence and stable tie break.
    """

    def __init__(self, instrument_id: str, feed: str, freshness_policy: QuoteFreshnessPolicy):
        self.instrument_id = instrument_id
        self.feed = feed
        self._freshness_policy = freshness_policy
        self._latest: QuoteSnapshot | None = None

    def apply(self, event: QuoteEvent) -> QuoteSnapshot:
        if event.instrument_id != self.instrument_id or event.feed != self.feed:
            raise MismatchedQuoteSeriesError(
                f"quote for ({event.instrument_id!r}, {event.feed!r}) cannot update quote state "
                f"for ({self.instrument_id!r}, {self.feed!r})"
            )
        if self._latest is not None and event.available_at_ns < self._latest.available_at_ns:
            raise ValueError("quote availability cannot move backward")
        snapshot = QuoteSnapshot.from_event(event)
        self._latest = snapshot
        return snapshot

    def latest(self) -> QuoteSnapshot | None:
        return self._latest

    def latest_with_freshness(self, decision_clock_ns: int) -> tuple[QuoteSnapshot, FreshnessState] | None:
        """The current quote paired with its freshness as of `decision_clock_ns`, or `None` if
        no quote has ever been applied -- never a fabricated default quote."""
        if self._latest is None:
            return None
        return self._latest, self._freshness_policy.classify(self._latest, decision_clock_ns)

"""Immutable market-event envelope (docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md section 6).

Every event -- trade, correction/cancel, quote, provider bar, corporate action,
timer -- shares a common, frozen envelope: stable identity, provider/feed,
nanosecond-precise event time and its declared meaning, replay availability,
source sequence, revision, a raw-payload reference, the session/unit contract
version in force, a quality state, and whether its timing is *recorded*
(observed arrival) or *modeled* (an assumed/declared delay) -- these are a
different evidence class and must never be conflated.

This module defines the typed envelope only; building bars/replay from a
stream of these events is R1-R3's concern.
"""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from rsys.data.timestamps import INTERVAL_DURATIONS_NS, parse_utc_timestamp_ns


def _positive_finite(v: Decimal) -> bool:
    if isinstance(v, bool):
        return False
    try:
        return v.is_finite() and v > 0
    except (TypeError, AttributeError):
        return False


def _reject_bool_int(v: object) -> object:
    if isinstance(v, bool):
        raise ValueError("boolean values are not accepted for this numeric field")  # noqa: TRY004 (pydantic only converts ValueError/AssertionError)
    return v


class EventKind(StrEnum):
    TRADE = "trade"
    TRADE_CORRECTION = "trade_correction"
    QUOTE = "quote"
    PROVIDER_BAR = "provider_bar"
    CORPORATE_ACTION = "corporate_action"
    TIMER = "timer"


class EventTimeMeaning(StrEnum):
    """What `event_time_ns` represents; distinct fields exist because they serve different purposes."""

    PROVIDER_MARKET_TIME = "provider_market_time"  # the provider's assigned trade/quote/bar time
    SCHEDULED_BOUNDARY = "scheduled_boundary"  # a timer's declared calendar boundary


class TimingLabel(StrEnum):
    """Recorded-arrival replay and historical-reconstruction-with-a-modeled-delay are different
    evidence classes; this label must travel with the event, not be inferred after the fact."""

    RECORDED = "recorded"
    MODELED = "modeled"


class QualityState(StrEnum):
    OK = "ok"
    SUSPECT = "suspect"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class MarketEvent(BaseModel):
    """Common envelope. Frozen: a received event's own identity/timing never mutates in place;
    a correction/cancel/revision is a new event referencing the original, not an edit to it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    instrument_id: str
    provider: str
    feed: str
    kind: EventKind
    event_time_ns: int
    event_time_meaning: EventTimeMeaning
    received_at_ns: int | None = None
    available_at_ns: int
    source_sequence: int | None = None
    revision: int = 1
    raw_payload_ref: str | None = None
    session_version: str
    quality_state: QualityState
    timing_label: TimingLabel

    @model_validator(mode="after")
    def _check_availability_not_before_event(self) -> MarketEvent:
        if self.available_at_ns < self.event_time_ns:
            raise ValueError("available_at_ns cannot precede event_time_ns (no future value exposed early)")
        if self.received_at_ns is not None:
            if self.timing_label != TimingLabel.RECORDED:
                raise ValueError("received_at_ns requires actual recorded-receipt evidence (timing_label=RECORDED)")
            if self.available_at_ns < self.received_at_ns:
                raise ValueError("available_at_ns cannot precede received_at_ns (a recorded event is not"
                                 " visible before its own receipt)")
        return self

    @field_validator("event_time_ns", "received_at_ns", "available_at_ns", "source_sequence", "revision",
                      mode="before")
    @classmethod
    def _reject_bool_timing_fields(cls, v: object) -> object:
        return _reject_bool_int(v)


class TradeEvent(MarketEvent):
    kind: Literal[EventKind.TRADE] = EventKind.TRADE
    price: Decimal
    size: Decimal
    trade_id: str
    venue: str
    conditions: tuple[str, ...] = ()
    tape: str | None = None  # preserved verbatim (C10); never stripped or treated as blank

    @field_validator("price", "size", mode="before")
    @classmethod
    def _reject_bool_price_size(cls, v: object) -> object:
        return _reject_bool_int(v)

    @model_validator(mode="after")
    def _check_price_and_size_positive(self) -> TradeEvent:
        if not _positive_finite(self.price):
            raise ValueError("trade price must be a finite positive value")
        if not _positive_finite(self.size):
            raise ValueError("trade size must be a finite positive value (a cancellation is a distinct event)")
        return self


class TradeCorrectionEvent(MarketEvent):
    """A correction or cancel referencing an earlier trade by ID; never edits it in place."""

    kind: Literal[EventKind.TRADE_CORRECTION] = EventKind.TRADE_CORRECTION
    original_trade_id: str
    action: Literal["correct", "cancel"]
    corrected_price: Decimal | None = None
    corrected_size: Decimal | None = None
    conditions: tuple[str, ...] = ()
    tape: str | None = None  # preserved verbatim (C10); never stripped or treated as blank

    @model_validator(mode="after")
    def _check_correction_payload(self) -> TradeCorrectionEvent:
        if self.action == "correct":
            if self.corrected_price is None or self.corrected_size is None:
                raise ValueError("a 'correct' action requires corrected_price and corrected_size")
            if not _positive_finite(self.corrected_price) or not _positive_finite(self.corrected_size):
                raise ValueError("a replacement price/size must be a finite positive value")
        if self.action == "cancel" and (self.corrected_price is not None or self.corrected_size is not None):
            raise ValueError("a 'cancel' action must not carry corrected_price/corrected_size")
        return self


class QuoteEvent(MarketEvent):
    kind: Literal[EventKind.QUOTE] = EventKind.QUOTE
    bid_price: Decimal | None = None
    bid_size: Decimal | None = None
    ask_price: Decimal | None = None
    ask_size: Decimal | None = None
    venue: str
    conditions: tuple[str, ...] = ()


class ProviderBarEvent(MarketEvent):
    kind: Literal[EventKind.PROVIDER_BAR] = EventKind.PROVIDER_BAR
    bar_start_ns: int
    bar_end_ns: int
    interval: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int | None = None
    vwap: Decimal | None = None
    adjustment_mode: str
    bar_revision: int = 1

    @field_validator("bar_start_ns", "bar_end_ns", "trade_count", "bar_revision", mode="before")
    @classmethod
    def _reject_bool_bar_fields(cls, v: object) -> object:
        return _reject_bool_int(v)

    @model_validator(mode="after")
    def _check_bar_bounds(self) -> ProviderBarEvent:
        if self.bar_end_ns <= self.bar_start_ns:
            raise ValueError("bar_end_ns must be after bar_start_ns")
        expected_duration = INTERVAL_DURATIONS_NS.get(self.interval)
        if expected_duration is not None and (self.bar_end_ns - self.bar_start_ns) != expected_duration:
            raise ValueError(f"bar duration does not match the declared interval {self.interval!r}")
        # A completed bar cannot be visible before its own interval ends (C05): the base
        # envelope only checks availability against event_time_ns, which a provider could set
        # to bar_start -- that would let an in-progress update masquerade as a completed bar.
        if self.available_at_ns < self.bar_end_ns:
            raise ValueError("a completed provider bar cannot be available before bar_end_ns")
        prices = (self.open, self.high, self.low, self.close)
        if any(not _positive_finite(p) for p in prices):
            raise ValueError("OHLC prices must be finite and positive")
        lo, hi = min(self.open, self.close), max(self.open, self.close)
        if not (self.low <= lo <= hi <= self.high):
            raise ValueError("OHLC values are inconsistent (low <= min(o,c) <= max(o,c) <= high required)")
        if isinstance(self.volume, bool) or self.volume < 0:
            raise ValueError("volume must be nonnegative")
        if self.trade_count is not None and self.trade_count < 0:
            raise ValueError("trade_count must be nonnegative")
        return self


class CorporateActionEvent(MarketEvent):
    kind: Literal[EventKind.CORPORATE_ACTION] = EventKind.CORPORATE_ACTION
    action_type: Literal["split"]
    ratio: Decimal
    effective_at_ns: int
    known_at_ns: int

    @field_validator("effective_at_ns", "known_at_ns", mode="before")
    @classmethod
    def _reject_bool_action_fields(cls, v: object) -> object:
        return _reject_bool_int(v)

    @model_validator(mode="after")
    def _check_action_validity(self) -> CorporateActionEvent:
        if not _positive_finite(self.ratio):
            raise ValueError("ratio must be a finite positive value")
        dispatch_at_ns = max(self.effective_at_ns, self.known_at_ns)
        if self.available_at_ns < dispatch_at_ns:
            raise ValueError("an action cannot be available before max(effective_at_ns, known_at_ns)")
        return self


class TimerEvent(MarketEvent):
    kind: Literal[EventKind.TIMER] = EventKind.TIMER
    boundary_name: str
    calendar_version: str


def event_timestamp_from_iso(raw: str) -> int:
    """Convenience wrapper so callers building fixtures don't import timestamps.py separately."""
    return parse_utc_timestamp_ns(raw)

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

from pydantic import BaseModel, ConfigDict, model_validator

from rsys.data.timestamps import parse_utc_timestamp_ns


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
        return self


class TradeEvent(MarketEvent):
    kind: Literal[EventKind.TRADE] = EventKind.TRADE
    price: Decimal
    size: Decimal
    trade_id: str
    venue: str
    conditions: tuple[str, ...] = ()


class TradeCorrectionEvent(MarketEvent):
    """A correction or cancel referencing an earlier trade by ID; never edits it in place."""

    kind: Literal[EventKind.TRADE_CORRECTION] = EventKind.TRADE_CORRECTION
    original_trade_id: str
    action: Literal["correct", "cancel"]
    corrected_price: Decimal | None = None
    corrected_size: Decimal | None = None

    @model_validator(mode="after")
    def _check_correction_payload(self) -> TradeCorrectionEvent:
        if self.action == "correct" and (self.corrected_price is None or self.corrected_size is None):
            raise ValueError("a 'correct' action requires corrected_price and corrected_size")
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

    @model_validator(mode="after")
    def _check_bar_bounds(self) -> ProviderBarEvent:
        if self.bar_end_ns <= self.bar_start_ns:
            raise ValueError("bar_end_ns must be after bar_start_ns")
        return self


class CorporateActionEvent(MarketEvent):
    kind: Literal[EventKind.CORPORATE_ACTION] = EventKind.CORPORATE_ACTION
    action_type: Literal["split"]
    ratio: Decimal
    effective_at_ns: int
    known_at_ns: int


class TimerEvent(MarketEvent):
    kind: Literal[EventKind.TIMER] = EventKind.TIMER
    boundary_name: str
    calendar_version: str


def event_timestamp_from_iso(raw: str) -> int:
    """Convenience wrapper so callers building fixtures don't import timestamps.py separately."""
    return parse_utc_timestamp_ns(raw)

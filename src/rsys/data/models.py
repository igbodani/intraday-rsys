"""Data data models and their intrinsic validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rsys.data.timestamps import INTERVAL_DURATIONS_NS


class CorporateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    action_type: Literal["split"]
    ratio: Decimal  # new_shares / old_shares, e.g. 2-for-1 -> 2
    effective_at: datetime  # UTC
    known_at: datetime  # UTC
    source: str
    revision: int = 1

    @field_validator("ratio")
    @classmethod
    def _check_ratio_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("ratio must be a positive value")
        return v

    @field_validator("effective_at", "known_at")
    @classmethod
    def _check_tz_aware_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "corporate-action timestamps must be explicit tz-aware UTC, not naive"
            )
        if v.utcoffset().total_seconds() != 0:
            raise ValueError("corporate-action timestamps must be UTC (zero offset)")
        return v

    @property
    def dispatch_at(self) -> datetime:
        return max(self.effective_at, self.known_at)


class ConvertedPosition(NamedTuple):
    quantity: Decimal
    stop: Decimal
    target: Decimal


class SnapshotStatus(StrEnum):
    FORMING = "forming"
    CLOSED_AS_OF = "closed_as_of"
    REVISED = "revised"
    # IR-03/R3I-07: a bucket whose interval straddled a corporate action's dispatch moment --
    # never a normal-looking mixed-unit bar, explicitly invalidated instead.
    INVALIDATED = "invalidated"


class BarSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    feed: str
    interval: str
    bar_start_ns: int
    bar_end_ns: int
    available_at_ns: int
    status: SnapshotStatus
    revision: (
        int  # number of post-closure revisions applied; 0 while FORMING or just closed
    )
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal
    trade_count: int
    vwap_numerator: Decimal
    vwap_denominator: Decimal
    rule_set_id: str
    # D20: this builder only ever operates on raw trade prices (never unit-adjusts); recorded
    # explicitly so a parity comparator can check unit-basis compatibility (IR-06/R3I-14)
    # instead of assuming agreement.
    adjustment_mode: str = "raw"
    publication_phase: int = 37
    publication_sequence: int | None = None
    provider: str | None = None
    session_version: str | None = None


class FieldEligibility(NamedTuple):
    open: bool  # contributes to the bucket's open price
    high: bool  # contributes to the bucket's high extreme
    low: bool  # contributes to the bucket's low extreme
    close: bool  # contributes to the bucket's close price
    volume: bool  # contributes to volume
    count: bool  # contributes to trade_count (not inferred from volume without a declared rule)
    vwap: bool  # contributes to the VWAP numerator/denominator


class _Contributor:
    __slots__ = ("active", "eligibility", "event_time_ns", "price", "size", "trade_id")

    def __init__(
        self,
        trade_id: str,
        event_time_ns: int,
        price: Decimal,
        size: Decimal,
        eligibility: FieldEligibility,
        active: bool = True,
    ):
        self.trade_id = trade_id
        self.event_time_ns = event_time_ns
        self.price = price
        self.size = size
        self.eligibility = eligibility
        self.active = active


class TradeClassificationContext(NamedTuple):
    """Everything a real classification rule may need, not just the bare conditions tuple.

    `conditions` and `tape` preserve the provider's raw codes verbatim -- including a code that
    is itself a meaningful space -- never stripped or treated as a blank identifier.
    """

    conditions: tuple[str, ...] = ()
    tape: str | None = None
    interval: str | None = None


@dataclass(frozen=True, order=True)
class ReplayKey:
    available_at_ns: int
    phase: int
    sequence: int = -1
    tie: tuple[str, ...] = ()

    @classmethod
    def end_of_time(cls, ns: int):
        return cls(ns, 1000, 2**63 - 1, ("\uffff",))


class BarVersion(BaseModel):
    """One immutable, published state of a bucket. `publication_key` orders versions for the
    same bucket identity; `sequence` breaks ties between versions published at the same
    `available_at_ns` (e.g. a trade and a same-instant correction), preserving publication
    order without claiming any particular wall-clock meaning for the tie-break itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    feed: str
    interval: str
    bar_start_ns: int
    bar_end_ns: int
    available_at_ns: int
    publication_phase: int = 37
    sequence: int
    status: str
    revision: int
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal
    trade_count: int
    vwap_numerator: Decimal
    vwap_denominator: Decimal
    rule_set_id: str
    source_event_ids: tuple[str, ...]
    adjustment_mode: str = "raw"
    provider: str | None = None
    session_version: str | None = None

    @property
    def publication_key(self) -> ReplayKey:
        return ReplayKey(self.available_at_ns, self.publication_phase, self.sequence)

    @classmethod
    def from_snapshot(
        cls, snapshot: BarSnapshot, *, sequence: int, source_event_ids: tuple[str, ...]
    ) -> BarVersion:
        return cls(
            instrument_id=snapshot.instrument_id,
            feed=snapshot.feed,
            interval=snapshot.interval,
            bar_start_ns=snapshot.bar_start_ns,
            bar_end_ns=snapshot.bar_end_ns,
            available_at_ns=snapshot.available_at_ns,
            sequence=sequence,
            status=snapshot.status.value,
            revision=snapshot.revision,
            open=snapshot.open,
            high=snapshot.high,
            low=snapshot.low,
            close=snapshot.close,
            volume=snapshot.volume,
            trade_count=snapshot.trade_count,
            vwap_numerator=snapshot.vwap_numerator,
            vwap_denominator=snapshot.vwap_denominator,
            rule_set_id=snapshot.rule_set_id,
            source_event_ids=source_event_ids,
            adjustment_mode=snapshot.adjustment_mode,
            publication_phase=snapshot.publication_phase,
            provider=snapshot.provider,
            session_version=snapshot.session_version,
        )


class DataQualityCode(StrEnum):
    INVALID_OHLC = "INVALID_OHLC"
    INVALID_VOLUME = "INVALID_VOLUME"
    DEDUPLICATE_EXACT = "DEDUPLICATE_EXACT"
    CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"
    MISSING_REQUIRED_INTERVAL = "MISSING_REQUIRED_INTERVAL"
    TIMEZONE_REQUIRED = "TIMEZONE_REQUIRED"
    ADJUSTMENT_MODE_MISMATCH = "ADJUSTMENT_MODE_MISMATCH"


class SetupStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class BarIdentity(NamedTuple):
    """Canonical bar identity: `(instrument_id, feed, interval, bar_start, adjustment_mode)`."""

    instrument_id: str
    feed: str
    interval: str
    bar_start: str  # UTC ISO-8601
    adjustment_mode: str


class RawBar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class DuplicateResolution(NamedTuple):
    kept: RawBar
    code: DataQualityCode
    duplicate_count: int


class CoverageCheck(NamedTuple):
    code: DataQualityCode | None
    setup_status: SetupStatus
    missing_starts: list[str]


class NormalizedBarIdentity(NamedTuple):
    """Canonical identity keyed on integer nanoseconds, so `...Z` and `...+00:00` bar_start
    strings that mean the same instant always compare identical."""

    instrument_id: str
    feed: str
    interval: str
    bar_start_ns: int
    adjustment_mode: str


class QualityState(StrEnum):
    OK = "ok"
    SUSPECT = "suspect"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class NormalizedBar(BaseModel):
    """The D1 bar contract: identity, interval bounds, availability/revision, OHLCV, the price-unit
    (corporate-action) version it is expressed in, source, and quality metadata. A bare `RawBar`
    (payload sanity only, no identity/availability/provenance) is not this contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    feed: str
    interval: str
    bar_start_ns: int
    bar_end_ns: int
    available_at_ns: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    adjustment_mode: str
    price_unit_version: (
        str  # which corporate-action conversion state the OHLCV is expressed in
    )
    source: str
    revision: int = 1
    quality_state: QualityState

    @model_validator(mode="after")
    def _check_bounds_and_alignment(self) -> NormalizedBar:
        if self.bar_end_ns <= self.bar_start_ns:
            raise ValueError("bar_end_ns must be after bar_start_ns")
        expected_duration = INTERVAL_DURATIONS_NS.get(self.interval)
        if (
            expected_duration is not None
            and (self.bar_end_ns - self.bar_start_ns) != expected_duration
        ):
            raise ValueError(
                f"bar duration does not match the declared interval {self.interval!r}"
            )
        if self.available_at_ns < self.bar_end_ns:
            raise ValueError(
                "available_at_ns cannot precede bar_end_ns (no future value exposed early)"
            )
        return self

    def identity(self) -> NormalizedBarIdentity:
        return NormalizedBarIdentity(
            self.instrument_id,
            self.feed,
            self.interval,
            self.bar_start_ns,
            self.adjustment_mode,
        )

    def is_available_at(self, as_of_ns: int) -> bool:
        """Public-view gate: forbid exposing a bar before its declared availability."""
        return as_of_ns >= self.available_at_ns


class EventKind(StrEnum):
    TRADE = "trade"
    TRADE_CORRECTION = "trade_correction"
    QUOTE = "quote"
    PROVIDER_BAR = "provider_bar"
    CORPORATE_ACTION = "corporate_action"
    TIMER = "timer"


class EventTimeMeaning(StrEnum):
    """What `event_time_ns` represents; distinct fields exist because they serve different purposes."""

    PROVIDER_MARKET_TIME = (
        "provider_market_time"  # the provider's assigned trade/quote/bar time
    )
    SCHEDULED_BOUNDARY = "scheduled_boundary"  # a timer's declared calendar boundary


class TimingLabel(StrEnum):
    """Recorded-arrival replay and historical-reconstruction-with-a-modeled-delay are different
    evidence classes; this label must travel with the event, not be inferred after the fact."""

    RECORDED = "recorded"
    MODELED = "modeled"


def _reject_bool_int(v: object) -> object:
    if isinstance(v, bool):
        raise ValueError("boolean values are not accepted for this numeric field")  # noqa: TRY004 (pydantic only converts ValueError/AssertionError)
    return v


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
            raise ValueError(
                "available_at_ns cannot precede event_time_ns (no future value exposed early)"
            )
        if self.received_at_ns is not None:
            if self.timing_label != TimingLabel.RECORDED:
                raise ValueError(
                    "received_at_ns requires actual recorded-receipt evidence (timing_label=RECORDED)"
                )
            if self.available_at_ns < self.received_at_ns:
                raise ValueError(
                    "available_at_ns cannot precede received_at_ns (a recorded event is not"
                    " visible before its own receipt)"
                )
        return self

    @field_validator(
        "event_time_ns",
        "received_at_ns",
        "available_at_ns",
        "source_sequence",
        "revision",
        mode="before",
    )
    @classmethod
    def _reject_bool_timing_fields(cls, v: object) -> object:
        return _reject_bool_int(v)


def _positive_finite(v: Decimal) -> bool:
    if isinstance(v, bool):
        return False
    try:
        return v.is_finite() and v > 0
    except (TypeError, AttributeError):
        return False


class TradeEvent(MarketEvent):
    kind: Literal[EventKind.TRADE] = EventKind.TRADE
    price: Decimal
    size: Decimal
    trade_id: str
    venue: str
    conditions: tuple[str, ...] = ()
    tape: str | None = (
        None  # preserved verbatim (C10); never stripped or treated as blank
    )

    @field_validator("price", "size", mode="before")
    @classmethod
    def _reject_bool_price_size(cls, v: object) -> object:
        return _reject_bool_int(v)

    @model_validator(mode="after")
    def _check_price_and_size_positive(self) -> TradeEvent:
        if not _positive_finite(self.price):
            raise ValueError("trade price must be a finite positive value")
        if not _positive_finite(self.size):
            raise ValueError(
                "trade size must be a finite positive value (a cancellation is a distinct event)"
            )
        return self


class TradeCorrectionEvent(MarketEvent):
    """A correction or cancel referencing an earlier trade by ID; never edits it in place."""

    kind: Literal[EventKind.TRADE_CORRECTION] = EventKind.TRADE_CORRECTION
    original_trade_id: str
    action: Literal["correct", "cancel"]
    corrected_price: Decimal | None = None
    corrected_size: Decimal | None = None
    # `None` means "conditions unchanged by this correction"; `()` means "explicitly cleared to
    # no conditions" (IR-01 item 4) -- `if event.conditions:` cannot express both meanings.
    conditions: tuple[str, ...] | None = None
    tape: str | None = (
        None  # preserved verbatim (C10); never stripped or treated as blank
    )

    @model_validator(mode="after")
    def _check_correction_payload(self) -> TradeCorrectionEvent:
        if self.action == "correct":
            if self.corrected_price is None or self.corrected_size is None:
                raise ValueError(
                    "a 'correct' action requires corrected_price and corrected_size"
                )
            if not _positive_finite(self.corrected_price) or not _positive_finite(
                self.corrected_size
            ):
                raise ValueError(
                    "a replacement price/size must be a finite positive value"
                )
        if self.action == "cancel" and (
            self.corrected_price is not None or self.corrected_size is not None
        ):
            raise ValueError(
                "a 'cancel' action must not carry corrected_price/corrected_size"
            )
        return self


class QuoteEvent(MarketEvent):
    kind: Literal[EventKind.QUOTE] = EventKind.QUOTE
    bid_price: Decimal | None = None
    bid_size: Decimal | None = None
    ask_price: Decimal | None = None
    ask_size: Decimal | None = None
    venue: str
    conditions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _quote_numbers(self):
        for price in (self.bid_price, self.ask_price):
            if price is not None and not _positive_finite(price):
                raise ValueError(
                    "quote prices must be finite and positive when present"
                )
        for size in (self.bid_size, self.ask_size):
            if size is not None and (not size.is_finite() or size < 0):
                raise ValueError(
                    "quote sizes must be finite and nonnegative when present"
                )
        return self


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

    @field_validator(
        "bar_start_ns", "bar_end_ns", "trade_count", "bar_revision", mode="before"
    )
    @classmethod
    def _reject_bool_bar_fields(cls, v: object) -> object:
        return _reject_bool_int(v)

    @model_validator(mode="after")
    def _check_bar_bounds(self) -> ProviderBarEvent:
        if self.bar_end_ns <= self.bar_start_ns:
            raise ValueError("bar_end_ns must be after bar_start_ns")
        expected_duration = INTERVAL_DURATIONS_NS.get(self.interval)
        if (
            expected_duration is not None
            and (self.bar_end_ns - self.bar_start_ns) != expected_duration
        ):
            raise ValueError(
                f"bar duration does not match the declared interval {self.interval!r}"
            )
        # A completed bar cannot be visible before its own interval ends (C05): the base
        # envelope only checks availability against event_time_ns, which a provider could set
        # to bar_start -- that would let an in-progress update masquerade as a completed bar.
        if self.available_at_ns < self.bar_end_ns:
            raise ValueError(
                "a completed provider bar cannot be available before bar_end_ns"
            )
        prices = (self.open, self.high, self.low, self.close)
        if any(not _positive_finite(p) for p in prices):
            raise ValueError("OHLC prices must be finite and positive")
        lo, hi = min(self.open, self.close), max(self.open, self.close)
        if not (self.low <= lo <= hi <= self.high):
            raise ValueError(
                "OHLC values are inconsistent (low <= min(o,c) <= max(o,c) <= high required)"
            )
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
            raise ValueError(
                "an action cannot be available before max(effective_at_ns, known_at_ns)"
            )
        return self


class TimerEvent(MarketEvent):
    kind: Literal[EventKind.TIMER] = EventKind.TIMER
    boundary_name: str
    calendar_version: str


class BarCloseEvent(TimerEvent):
    """Internal scheduled interval closure, never decoded from an external timer."""

    boundary_name: Literal["bar_close"] = "bar_close"


class ExclusionReason(StrEnum):
    VERIFIED_NO_TRADE = (
        "verified_no_trade"  # the provider confirms the interval traded nothing
    )
    MISSING_DELIVERY = (
        "missing_delivery"  # the feed simply did not deliver required coverage
    )
    INSUFFICIENT_HISTORY = (
        "insufficient_history"  # fewer comparable sessions than required
    )
    UNSUPPORTED_ACTION = (
        "unsupported_action"  # an action type/ratio this contract cannot convert
    )
    INACTIVE_SYMBOL = "inactive_symbol"  # delisted/inactive at the requested date
    FEED_MISMATCH = (
        "feed_mismatch"  # mixed-feed data that cannot be pooled under one threshold
    )


class CoverageExclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    feed: str
    session_date: str  # ISO 8601 date
    reason: ExclusionReason
    detail: str


class UniverseManifest(BaseModel):
    """One manifest per experiment/run's data acquisition; frozen inputs for its evidence."""

    model_config = ConfigDict(extra="forbid")

    universe_basis: str  # e.g. "alpaca_active_2024_snapshot"; never a reconstructed historical universe
    feed: str
    calendar_version: str
    rules_version: str
    requested_instruments: tuple[str, ...]
    observed_instruments: tuple[str, ...]
    exclusions: tuple[CoverageExclusion, ...] = ()
    corporate_action_coverage: (
        str  # declared coverage statement (e.g. "splits_only_2024_v1")
    )
    timing_provenance: TimingLabel
    input_hashes: dict[str, str] = Field(default_factory=dict)
    data_precision: str  # e.g. "ns" | "us"
    gaps: tuple[str, ...] = ()  # declared gap descriptors; never silently absent

    @model_validator(mode="after")
    def _check_observed_subset_of_requested_or_excluded(self) -> UniverseManifest:
        requested = set(self.requested_instruments)
        observed = set(self.observed_instruments)
        excluded = {e.instrument_id for e in self.exclusions}
        missing = requested - observed - excluded
        if missing:
            raise ValueError(
                f"requested instrument(s) {sorted(missing)} are neither observed nor declared excluded"
            )
        return self

    def is_verified_no_trade(self, instrument_id: str, session_date: str) -> bool:
        return any(
            e.instrument_id == instrument_id
            and e.session_date == session_date
            and e.reason == ExclusionReason.VERIFIED_NO_TRADE
            for e in self.exclusions
        )

    def is_missing_delivery(self, instrument_id: str, session_date: str) -> bool:
        return any(
            e.instrument_id == instrument_id
            and e.session_date == session_date
            and e.reason == ExclusionReason.MISSING_DELIVERY
            for e in self.exclusions
        )


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


def classify_quote_condition(
    bid_price: Decimal | None, ask_price: Decimal | None
) -> QuoteCondition:
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
    def from_event(
        cls, event: QuoteEvent, *, size_unit: str = "shares"
    ) -> QuoteSnapshot:
        return cls(
            instrument_id=event.instrument_id,
            feed=event.feed,
            venue=event.venue,
            source_event_id=event.event_id,
            event_time_ns=event.event_time_ns,
            available_at_ns=event.available_at_ns,
            bid_price=event.bid_price,
            bid_size=event.bid_size,
            ask_price=event.ask_price,
            ask_size=event.ask_size,
            size_unit=size_unit,
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
            raise ValueError(
                "decision clock cannot precede the quote's own availability"
            )
        age_ns = decision_clock_ns - quote.available_at_ns
        limit_ns = self.freshness_limit_ms * 1_000_000
        return FreshnessState.FRESH if age_ns <= limit_ns else FreshnessState.STALE


@dataclass(frozen=True)
class HistorySession:
    """One prior session's declared coverage for one instrument/feed, calendar-ordered.

    `cumulative_volume` is the session's regular-session volume cumulative through the
    *same elapsed minute* being evaluated today, already converted to current split units
    by the caller (docs/data_rules.md). `coverage_complete` means the feed's declared
    coverage reaches that elapsed minute without a gap; a session can be selected by its
    scheduled `duration_minutes` while its data is still incomplete/missing.
    """

    session_date: date
    duration_minutes: int
    cumulative_volume: Decimal | None
    coverage_complete: bool


@dataclass(frozen=True)
class RvolResult:
    value: Decimal | None
    selected_dates: tuple[date, ...]
    mean_volume: Decimal | None
    status: str | None  # None means available


@dataclass(frozen=True)
class SessionGainResult:
    value: Decimal | None
    status: str | None


class FieldMismatch(NamedTuple):
    field: str
    builder_value: object
    provider_value: object


class ParityResult(NamedTuple):
    comparable: bool
    match: bool
    mismatches: tuple[FieldMismatch, ...]
    not_comparable_reason: str | None


@dataclass(frozen=True)
class SyntheticSymbolHistory:
    """One symbol's declared synthetic history for `FixtureHistoryProvider`."""

    sessions: list[HistorySession]  # newest-first
    previous_session_close: Decimal | None
    prior_session_volume: Decimal | None
    prior_session_close_for_dollar_volume: Decimal | None


class ObservationStatus(StrEnum):
    """One minute-addressable observation's trade status. Only `TRADED`/`NO_TRADE` ever
    exist as a `MinuteObservation`; a minute with no observation at all visible by a cutoff
    is a coverage gap encoded by its absence, never a status value (docs/data_rules.md:
    "missing bars are not proof of zero trades")."""

    TRADED = "TRADED"
    NO_TRADE = "NO_TRADE"  # verified: the feed confirms zero trades, not a guess


class UnitBasis(StrEnum):
    """`RAW` is the only basis `data.history`'s minute-addressable loader accepts as a
    point-in-time feature input (docs/data_rules.md: cache `adjustment=raw`; apply splits
    explicitly via `features.convert_split_units`). `SPLIT_ADJUSTED` marks a declared,
    parseable provider-pre-adjusted series that is always excluded, never silently treated
    as comparable raw history, because its action boundary is unverified (D20)."""

    RAW = "raw"
    SPLIT_ADJUSTED = "split_adjusted"


@dataclass(frozen=True)
class MinuteObservation:
    """One revision of one elapsed minute's declared historical coverage for one
    instrument/session/feed (minute-addressable history, Step 1 of
    docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md).

    `available_at` is when this exact revision became knowable -- independent of
    `session_date`/`minute_index` -- so a decision cutoff before `available_at` must never
    see it, even though the minute itself is already in the past. `revision` orders
    corrections for the same minute: the highest revision visible by a cutoff wins, never an
    earlier one silently "filled back in" once a later correction exists.
    """

    instrument_id: str
    feed: str
    source: str
    session_date: date
    minute_index: int  # 0-based elapsed minutes since the session's scheduled open
    available_at: datetime  # tz-aware UTC
    revision: int
    status: ObservationStatus
    unit_basis: UnitBasis
    close: Decimal | None  # None for a verified NO_TRADE minute: no trade, no price
    volume: Decimal


@dataclass(frozen=True)
class MinuteSymbolHistory:
    """One symbol's declared minute-addressable observations for `MinuteHistoryProvider`,
    across every session/revision; `data.history` groups and as-of-selects them per session.
    """

    observations: list[MinuteObservation]


@dataclass(frozen=True)
class SelectedSession:
    """One candidate prior session's `HistorySession`, rebuilt from minute-addressable
    observations as of one decision cutoff. Retains the exact revisions actually selected
    (evidence for later reconstruction) and, when the session could not be used, why."""

    history_session: HistorySession
    selected_revisions: tuple[MinuteObservation, ...]
    exclusion_reason: str | None  # None means every required minute was available and usable


@dataclass(frozen=True)
class PreviousSessionObservation:
    """The most recent prior trading session's full-session close/volume, rebuilt from
    minute-addressable observations as of one decision cutoff -- used for session-gain's
    previous close and prior-day dollar-volume eligibility (never the RVOL lookback's
    same-elapsed-minute slice, which is computed per candidate session instead)."""

    session_date: date | None
    close: Decimal | None
    volume: Decimal | None
    selected_revisions: tuple[MinuteObservation, ...]
    exclusion_reason: str | None


@dataclass(frozen=True)
class _ObservedBar:
    available_at_ms: int
    close: Decimal
    volume: Decimal


BucketIdentity = tuple[
    str, str, str, int
]  # (instrument_id, feed, interval, bar_start_ns)

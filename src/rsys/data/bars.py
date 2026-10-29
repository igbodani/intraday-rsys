"""Bar validation: canonical identity, OHLCV sanity, duplicates, coverage,
timezone and adjustment-mode checks (docs/data_rules.md "Quality and time").

Invalid, conflicting or missing bars must be visible as a named status, not
silently repaired, forward-filled or dropped (DQ01-DQ07).
"""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, model_validator

from rsys.data.events import QualityState


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


def validate_ohlcv(bar: RawBar) -> DataQualityCode | None:
    """`low <= min(open,close) <= max(open,close) <= high`, nonnegative volume, finite positive prices."""
    if bar.volume < 0:
        return DataQualityCode.INVALID_VOLUME
    prices = (bar.open, bar.high, bar.low, bar.close)
    if any(p <= 0 for p in prices):
        return DataQualityCode.INVALID_OHLC
    lo = min(bar.open, bar.close)
    hi = max(bar.open, bar.close)
    if not (bar.low <= lo <= hi <= bar.high):
        return DataQualityCode.INVALID_OHLC
    return None


class DuplicateResolution(NamedTuple):
    kept: RawBar
    code: DataQualityCode
    duplicate_count: int


def reconcile_duplicates(payloads: list[RawBar]) -> DuplicateResolution:
    """Same canonical identity, multiple payloads: dedupe exact repeats; block conflicting ones."""
    if not payloads:
        raise ValueError("reconcile_duplicates requires at least one payload")
    first = payloads[0]
    if all(p == first for p in payloads[1:]):
        return DuplicateResolution(kept=first, code=DataQualityCode.DEDUPLICATE_EXACT, duplicate_count=len(payloads) - 1)
    return DuplicateResolution(kept=first, code=DataQualityCode.CONFLICTING_DUPLICATE, duplicate_count=len(payloads) - 1)


class CoverageCheck(NamedTuple):
    code: DataQualityCode | None
    setup_status: SetupStatus
    missing_starts: list[str]


def check_required_intervals(required_starts: list[str], received_starts: list[str]) -> CoverageCheck:
    """Missing required bar starts never become a forward-filled price or a skipped session."""
    received = set(received_starts)
    missing = [s for s in required_starts if s not in received]
    if missing:
        return CoverageCheck(code=DataQualityCode.MISSING_REQUIRED_INTERVAL,
                              setup_status=SetupStatus.UNAVAILABLE, missing_starts=missing)
    return CoverageCheck(code=None, setup_status=SetupStatus.AVAILABLE, missing_starts=[])


def check_timezone_present(provider_timestamp: str, source_timezone: str | None) -> DataQualityCode | None:
    """A naive provider timestamp with no declared source zone is ambiguous (e.g. a DST-fallback hour)."""
    has_offset = provider_timestamp.endswith("Z") or "+" in provider_timestamp[10:] or "-" in provider_timestamp[10:]
    if not has_offset and source_timezone is None:
        return DataQualityCode.TIMEZONE_REQUIRED
    return None


def check_adjustment_mode(execution_required_adjustment: str, input_adjustment: str) -> DataQualityCode | None:
    """Raw execution/current-session data must never be silently fed from a differently adjusted series."""
    if execution_required_adjustment != input_adjustment:
        return DataQualityCode.ADJUSTMENT_MODE_MISMATCH
    return None


# Declared bucket durations for alignment validation; extending beyond these requires
# its own versioned entry, not a silent assumption (D05: 1-minute baseline in v1).
INTERVAL_DURATIONS_NS: dict[str, int] = {"1Min": 60_000_000_000}


class NormalizedBarIdentity(NamedTuple):
    """Canonical identity keyed on integer nanoseconds, so `...Z` and `...+00:00` bar_start
    strings that mean the same instant always compare identical."""

    instrument_id: str
    feed: str
    interval: str
    bar_start_ns: int
    adjustment_mode: str


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
    price_unit_version: str  # which corporate-action conversion state the OHLCV is expressed in
    source: str
    revision: int = 1
    quality_state: QualityState

    @model_validator(mode="after")
    def _check_bounds_and_alignment(self) -> NormalizedBar:
        if self.bar_end_ns <= self.bar_start_ns:
            raise ValueError("bar_end_ns must be after bar_start_ns")
        expected_duration = INTERVAL_DURATIONS_NS.get(self.interval)
        if expected_duration is not None and (self.bar_end_ns - self.bar_start_ns) != expected_duration:
            raise ValueError(f"bar duration does not match the declared interval {self.interval!r}")
        if self.available_at_ns < self.bar_end_ns:
            raise ValueError("available_at_ns cannot precede bar_end_ns (no future value exposed early)")
        return self

    def identity(self) -> NormalizedBarIdentity:
        return NormalizedBarIdentity(
            self.instrument_id, self.feed, self.interval, self.bar_start_ns, self.adjustment_mode
        )

    def is_available_at(self, as_of_ns: int) -> bool:
        """Public-view gate: forbid exposing a bar before its declared availability."""
        return as_of_ns >= self.available_at_ns

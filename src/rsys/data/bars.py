"""Bar validation: canonical identity, OHLCV sanity, duplicates, coverage,
timezone and adjustment-mode checks (docs/data_rules.md "Quality and time").

Invalid, conflicting or missing bars must be visible as a named status, not
silently repaired, forward-filled or dropped (DQ01-DQ07).
"""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict


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

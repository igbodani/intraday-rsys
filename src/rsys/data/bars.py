"""Bar validation: canonical identity, OHLCV sanity, duplicates, coverage,
timezone and adjustment-mode checks (docs/data_rules.md "Quality and time").

Invalid, conflicting or missing bars must be visible as a named status, not
silently repaired, forward-filled or dropped (DQ01-DQ07).
"""

from __future__ import annotations

from rsys.data.models import (
    CoverageCheck,
    DataQualityCode,
    DuplicateResolution,
    RawBar,
    SetupStatus,
)
from rsys.data.timestamps import INTERVAL_DURATIONS_NS

__all__ = [
    "INTERVAL_DURATIONS_NS",  # re-exported from timestamps.py; kept here for existing callers
]


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


def reconcile_duplicates(payloads: list[RawBar]) -> DuplicateResolution:
    """Same canonical identity, multiple payloads: dedupe exact repeats; block conflicting ones."""
    if not payloads:
        raise ValueError("reconcile_duplicates requires at least one payload")
    first = payloads[0]
    if all(p == first for p in payloads[1:]):
        return DuplicateResolution(
            kept=first,
            code=DataQualityCode.DEDUPLICATE_EXACT,
            duplicate_count=len(payloads) - 1,
        )
    return DuplicateResolution(
        kept=first,
        code=DataQualityCode.CONFLICTING_DUPLICATE,
        duplicate_count=len(payloads) - 1,
    )


def check_required_intervals(
    required_starts: list[str], received_starts: list[str]
) -> CoverageCheck:
    """Missing required bar starts never become a forward-filled price or a skipped session."""
    received = set(received_starts)
    missing = [s for s in required_starts if s not in received]
    if missing:
        return CoverageCheck(
            code=DataQualityCode.MISSING_REQUIRED_INTERVAL,
            setup_status=SetupStatus.UNAVAILABLE,
            missing_starts=missing,
        )
    return CoverageCheck(
        code=None, setup_status=SetupStatus.AVAILABLE, missing_starts=[]
    )


def check_timezone_present(
    provider_timestamp: str, source_timezone: str | None
) -> DataQualityCode | None:
    """A naive provider timestamp with no declared source zone is ambiguous (e.g. a DST-fallback hour)."""
    has_offset = (
        provider_timestamp.endswith("Z")
        or "+" in provider_timestamp[10:]
        or "-" in provider_timestamp[10:]
    )
    if not has_offset and source_timezone is None:
        return DataQualityCode.TIMEZONE_REQUIRED
    return None


def check_adjustment_mode(
    execution_required_adjustment: str, input_adjustment: str
) -> DataQualityCode | None:
    """Raw execution/current-session data must never be silently fed from a differently adjusted series."""
    if execution_required_adjustment != input_adjustment:
        return DataQualityCode.ADJUSTMENT_MODE_MISMATCH
    return None

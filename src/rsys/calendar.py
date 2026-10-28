"""Declared exchange calendar (NYSE, America/New_York), 2024 only.

This is a hand-declared calendar for the offline M1 baseline -- not a live
provider feed. `CALENDAR_VERSION` pins the exact holiday/early-close table so
any dependent evidence (data_rules.md's calendar-aware RVOL lookback, flatten
timers) can cite which declared schedule it used. Extending beyond 2024, or
moving to a provider-sourced calendar, is a later ticket's version bump, not
a silent edit to this one.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

CALENDAR_VERSION = "nyse-2024-v1"

EXCHANGE_TZ = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Full-day holidays (market closed), 2024 NYSE schedule.
_HOLIDAYS_2024: frozenset[date] = frozenset(
    {
        date(2024, 1, 1),  # New Year's Day
        date(2024, 1, 15),  # Martin Luther King Jr. Day
        date(2024, 2, 19),  # Washington's Birthday
        date(2024, 3, 29),  # Good Friday
        date(2024, 5, 27),  # Memorial Day
        date(2024, 6, 19),  # Juneteenth
        date(2024, 7, 4),  # Independence Day
        date(2024, 9, 2),  # Labor Day
        date(2024, 11, 28),  # Thanksgiving Day
        date(2024, 12, 25),  # Christmas Day
    }
)

# Scheduled early closes (13:00 local), 2024 NYSE schedule.
_EARLY_CLOSES_2024: frozenset[date] = frozenset(
    {
        date(2024, 7, 3),
        date(2024, 11, 29),
        date(2024, 12, 24),
    }
)


class CalendarCoverageError(Exception):
    """Raised for a date outside this declared calendar's covered range."""


def _check_covered(d: date) -> None:
    if d.year != 2024:
        raise CalendarCoverageError(f"{CALENDAR_VERSION} only covers calendar year 2024, got {d.isoformat()}")


def is_trading_day(d: date) -> bool:
    """Weekday and not a declared holiday. Does not special-case any other closure."""
    _check_covered(d)
    return d.weekday() < 5 and d not in _HOLIDAYS_2024


def is_early_close(d: date) -> bool:
    _check_covered(d)
    return d in _EARLY_CLOSES_2024


def session_close_local(d: date) -> time:
    """The actual scheduled close, including early closes (CAL-001)."""
    if not is_trading_day(d):
        raise CalendarCoverageError(f"{d.isoformat()} is not a trading day under {CALENDAR_VERSION}")
    return EARLY_CLOSE if is_early_close(d) else REGULAR_CLOSE


def session_open_local(d: date) -> time:
    if not is_trading_day(d):
        raise CalendarCoverageError(f"{d.isoformat()} is not a trading day under {CALENDAR_VERSION}")
    return REGULAR_OPEN


def to_exchange_utc(local_naive: datetime) -> datetime:
    """Convert an exchange-local (America/New_York) naive datetime to UTC, DST-aware."""
    if local_naive.tzinfo is not None:
        raise ValueError(f"expected a naive exchange-local datetime, got tz-aware value {local_naive!r}")
    return local_naive.replace(tzinfo=EXCHANGE_TZ).astimezone(ZoneInfo("UTC"))


def session_open_utc(d: date) -> datetime:
    return to_exchange_utc(datetime.combine(d, session_open_local(d)))


def session_close_utc(d: date) -> datetime:
    return to_exchange_utc(datetime.combine(d, session_close_local(d)))


def session_duration_minutes(d: date) -> int:
    """Scheduled session length in minutes (used to validate early-close RVOL coverage)."""
    open_utc = session_open_utc(d)
    close_utc = session_close_utc(d)
    return int((close_utc - open_utc).total_seconds() // 60)

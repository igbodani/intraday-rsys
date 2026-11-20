"""Versioned historical stock trade schedule, independent of the 2024 fixture calendar."""

from datetime import date, timedelta
from functools import lru_cache

from rsys.data.models import TapeHours
from rsys.data.xnys_dates import HALF_DAYS, WEEKDAY_CLOSURES

VERSION = "xnys-2015-2026-alpaca-day-v1"
REGULAR_OPEN = 570
MODES = {"regular", "premarket_regular", "regular_afterhours", "extended"}


def trading_day(day):
    if day.year not in WEEKDAY_CLOSURES:
        raise ValueError("Trade calendar covers 2015 through 2026")
    return day.weekday() < 5 and day.strftime("%m-%d") not in WEEKDAY_CLOSURES[day.year]


@lru_cache(maxsize=16384)
def tape_hours(day, mode="regular"):
    if mode not in MODES:
        raise ValueError("Unknown trade session_mode")
    if not trading_day(day):
        raise ValueError(f"{day} is not a trading session")
    short = day.strftime("%m-%d") in HALF_DAYS[day.year]
    regular_close = 780 if short else 960
    pre = mode in ("premarket_regular", "extended")
    post = mode in ("regular_afterhours", "extended")
    # Full windows for comparable scanner history, even when broker hours differed.
    data_open = 240 if pre else REGULAR_OPEN
    data_close = (1020 if short else 1200) if post else regular_close
    order_open, order_close = REGULAR_OPEN, regular_close
    # Conservative first full day following the July 11, 2019 launch announcement.
    if day >= date(2019, 7, 12):
        expanded = day >= date(2022, 2, 24)
        if pre:
            order_open = 240 if expanded else 540
        if post and not short:
            order_close = 1200 if expanded else 1080
        if post and short and expanded:
            order_close = (
                1020  # Trading API rollout explicitly specifies 17:00 on half days.
            )
    return TapeHours(data_open, data_close, order_open, order_close, regular_close)


def canonical_day(value):
    d = date.fromisoformat(value)
    if value != d.isoformat():
        raise ValueError("Use YYYY-MM-DD for session dates")
    return d


def execution_dates(config):
    if ("sessions" in config) == ("date_range" in config):
        raise ValueError("Specify exactly one of sessions and date_range")
    if "sessions" in config:
        days = [canonical_day(x) for x in config["sessions"]]
        if not days or days != sorted(set(days)) or not all(map(trading_day, days)):
            raise ValueError(
                "Execution dates must be unique, chronological trading sessions"
            )
        return days
    span = config["date_range"]
    if not isinstance(span, dict) or set(span) != {"start", "end"}:
        raise ValueError("date_range requires start and end")
    start, end = canonical_day(span["start"]), canonical_day(span["end"])
    trading_day(start)
    trading_day(end)
    days = []
    while start <= end:
        if trading_day(start):
            days.append(start)
        start += timedelta(days=1)
    if not days:
        raise ValueError("No trading sessions in date_range")
    return days


def prior_dates(day, lookback, mode):
    hours = tape_hours(day, mode)
    dates, comparable = [], 0
    cursor = day
    while comparable < lookback:
        cursor -= timedelta(days=1)
        if not trading_day(cursor):
            continue
        dates.append(cursor)
        comparable += tape_hours(cursor, mode).data_close >= hours.data_close
    return dates

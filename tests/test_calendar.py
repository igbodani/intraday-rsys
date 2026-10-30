import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from rsys.data.calendar import (
    is_early_close,
    is_trading_day,
    session_close_local,
    session_close_utc,
    session_open_utc,
    to_exchange_utc,
)
from rsys.data.errors import CalendarCoverageError

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_CASES = json.loads((REPO_ROOT / "fixtures" / "data_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DATA_CASES["dst_cases"], ids=lambda c: c["id"])
def test_dst_cases_convert_exchange_local_to_utc(case):
    assert case["zone"] == "America/New_York"
    local_naive = datetime.fromisoformat(case["local_open"])
    converted = to_exchange_utc(local_naive)
    expected = datetime.fromisoformat(case["expected_utc"])
    assert converted == expected


def test_to_exchange_utc_rejects_tz_aware_input():
    aware = datetime(2024, 3, 8, 9, 30, tzinfo=ZoneInfo("UTC"))
    with pytest.raises(ValueError):
        to_exchange_utc(aware)


def test_weekend_is_not_a_trading_day():
    assert not is_trading_day(date(2024, 3, 9))  # Saturday
    assert not is_trading_day(date(2024, 3, 10))  # Sunday


def test_declared_holiday_is_not_a_trading_day():
    assert not is_trading_day(date(2024, 1, 1))  # New Year's Day


def test_ordinary_weekday_is_a_trading_day():
    assert is_trading_day(date(2024, 3, 8))


def test_early_close_session_ends_at_13_00_local():
    d = date(2024, 11, 29)
    assert is_early_close(d)
    assert session_close_local(d).isoformat() == "13:00:00"


def test_regular_session_ends_at_16_00_local():
    d = date(2024, 3, 8)
    assert not is_early_close(d)
    assert session_close_local(d).isoformat() == "16:00:00"


def test_session_close_utc_reflects_dst():
    # Early close day 2024-11-29 is EST (UTC-5): 13:00 local -> 18:00 UTC.
    assert session_close_utc(date(2024, 11, 29)) == datetime(2024, 11, 29, 18, 0, tzinfo=ZoneInfo("UTC"))
    # Regular session day 2024-03-08 is EST (UTC-5): 16:00 local -> 21:00 UTC.
    assert session_close_utc(date(2024, 3, 8)) == datetime(2024, 3, 8, 21, 0, tzinfo=ZoneInfo("UTC"))


def test_session_bounds_reject_a_non_trading_day():
    with pytest.raises(CalendarCoverageError):
        session_open_utc(date(2024, 1, 1))


def test_calendar_rejects_dates_outside_its_declared_coverage():
    with pytest.raises(CalendarCoverageError):
        is_trading_day(date(2025, 1, 2))

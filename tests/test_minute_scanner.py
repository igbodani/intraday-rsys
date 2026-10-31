"""Calendar/coverage edges through a real immutable completed-bar context."""

import runpy
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from rsys.application.history_config import build_history_scanner, parse_scanner
from rsys.data.calendar import session_open_utc
from rsys.data.models import ProviderBarEvent
from rsys.replay.models import ReplayProfile
from rsys.replay.views import ClosedOnlyContext, ReplayView

ROOT = Path(__file__).resolve().parents[1]
build_fixture = runpy.run_path(str(ROOT / "scripts/make_history_scanner_fixture.py"))[
    "build_fixture"
]
MINUTE = 60_000_000_000


def scan(config, day, elapsed):
    start = int(session_open_utc(day).timestamp()) * 1_000_000_000
    cutoff = start + elapsed * MINUTE
    events = [
        ProviderBarEvent(
            event_id=f"minute-{minute}",
            instrument_id="AAA",
            provider="synthetic",
            feed="fixture",
            event_time_ns=start + minute * MINUTE,
            available_at_ns=start + (minute + 1) * MINUTE,
            event_time_meaning="provider_market_time",
            session_version="nyse-2024-v1",
            quality_state="ok",
            timing_label="modeled",
            adjustment_mode="raw",
            interval="1Min",
            bar_start_ns=start + minute * MINUTE,
            bar_end_ns=start + (minute + 1) * MINUTE,
            open=D(100),
            high=D(100),
            low=D(100),
            close=D(100),
            volume=D(100),
        )
        for minute in range(elapsed)
    ]
    context = ClosedOnlyContext(
        ReplayView(events, profile=ReplayProfile.BAR_V1), cutoff
    )
    scanner = build_history_scanner(config, parse_scanner(config), day, ["AAA"])
    return scanner.evaluate(context, as_of_ns=cutoff, top_n=1), scanner


def test_early_close_is_skipped_for_rvol_but_remains_the_previous_close_session():
    config = build_fixture()
    config["scanner"]["max_lookback_calendar_sessions"] = 2
    result, scanner = scan(config, date(2024, 12, 2), 211)
    detail = result.evidence["symbols"]["AAA"]
    assert detail["current_volume"] == 21100
    assert detail["rvol"]["selected_dates"] == (date(2024, 11, 27),)
    assert detail["rvol"]["mean_volume"] == 10550  # 211 minutes * 50
    assert detail["rvol"]["value"] == 2
    short, full = detail["history_sessions"]
    assert short["duration_minutes"] == 210
    assert short["exclusion_reason"] == "SESSION_TOO_SHORT"
    assert short["selected"] is False and full["selected"] is True
    assert scanner.snapshots[short["snapshot_id"]]["selected_revisions"] == []
    assert detail["previous_session"]["session_date"] == date(2024, 11, 29)
    assert detail["previous_session"]["volume"] == 10500  # 210 * 50
    assert result.result.top == ["AAA"]


def test_selected_missing_session_is_not_replaced_with_an_older_complete_one():
    config = build_fixture()
    config["scanner"]["max_lookback_calendar_sessions"] = 2
    config["scanner"]["history"]["AAA"] = [
        r
        for r in config["scanner"]["history"]["AAA"]
        if not (r["session_date"] == "2024-11-29" and r["minute_index"] == 0)
    ]
    result, _ = scan(config, date(2024, 12, 2), 1)
    detail = result.evidence["symbols"]["AAA"]
    assert detail["rvol"]["selected_dates"] == (date(2024, 11, 29),)
    assert detail["rvol"]["status"] == "MISSING_SELECTED_HISTORY"
    assert detail["history_sessions"][1]["coverage_complete"] is True
    assert detail["history_sessions"][1]["selected"] is False
    assert result.result.top == []


def test_calendar_boundary_is_missing_history_even_with_future_history_in_the_input():
    result, scanner = scan(build_fixture(), date(2024, 1, 2), 1)
    detail = result.evidence["symbols"]["AAA"]
    assert detail["rvol"]["status"] == "MISSING_SELECTED_HISTORY"
    assert detail["rvol"]["selected_dates"] == ()
    assert detail["previous_session"]["session_date"] is None
    assert all(
        snapshot["selected_revisions"] == [] for snapshot in scanner.snapshots.values()
    )
    assert result.result.top == []


def test_no_completed_minute_is_unavailable_instead_of_verified_zero():
    result, scanner = scan(build_fixture(), date(2024, 12, 2), 0)
    assert result.result.top == []
    assert result.evidence["symbols"]["AAA"]["current_status"] == "NO_COMPLETED_BAR"
    assert "current_volume" not in result.evidence["symbols"]["AAA"]
    assert scanner.snapshots == {}

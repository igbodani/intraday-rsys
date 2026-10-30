"""Multi-session statistics tests (Step 5 of docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md):
exposure and drawdown are hand-calculated from explicit fill/equity fixtures, independent
of any single-session identity."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from rsys.calendar import session_duration_minutes
from rsys.multi_session_evaluation import (
    UNAVAILABLE_NO_SESSIONS,
    UNAVAILABLE_NONPOSITIVE_PEAK,
    SessionEquityPoint,
    compute_drawdown,
    compute_exposure,
    compute_multi_session_metrics,
)
from rsys.session_coordinator import SessionResult
from rsys.standalone_run import FillRecord

REGULAR_DATE = date(2024, 12, 2)
EARLY_CLOSE_DATE = date(2024, 11, 29)
REGULAR_SCHEDULED_MS = session_duration_minutes(REGULAR_DATE) * 60_000


def _result(session_date, fills, ending_equity) -> SessionResult:
    return SessionResult(
        session_date=session_date,
        session_open_ns=0,
        decisions=[],
        fills=fills,
        contexts=[],
        ending_equity=ending_equity,
    )


def test_drawdown_hand_calculated_across_a_session_equity_curve():
    curve = [
        SessionEquityPoint(REGULAR_DATE, Decimal(1000)),
        SessionEquityPoint(REGULAR_DATE, Decimal(1200)),  # new peak
        SessionEquityPoint(REGULAR_DATE, Decimal(900)),  # decline of 300 from peak 1200
        SessionEquityPoint(REGULAR_DATE, Decimal(1500)),  # new peak
        SessionEquityPoint(REGULAR_DATE, Decimal(1100)),  # decline of 400 from peak 1500
    ]
    result = compute_drawdown(curve)
    assert result.max_drawdown == Decimal(400)
    assert result.max_drawdown_pct == Decimal(400) / Decimal(1500)
    assert result.peak_equity == Decimal(1500)
    assert result.trough_equity == Decimal(1100)


def test_an_empty_equity_curve_is_an_explicit_unavailable_result_not_zero_drawdown():
    result = compute_drawdown([])
    assert result.max_drawdown == Decimal(0)
    assert result.max_drawdown_pct == UNAVAILABLE_NO_SESSIONS


def test_a_nonpositive_peak_gives_an_explicit_unavailable_percentage():
    curve = [
        SessionEquityPoint(REGULAR_DATE, Decimal(-100)),
        SessionEquityPoint(REGULAR_DATE, Decimal(-500)),  # decline of 400 from a nonpositive peak
    ]
    result = compute_drawdown(curve)
    assert result.max_drawdown == Decimal(400)
    assert result.max_drawdown_pct == UNAVAILABLE_NONPOSITIVE_PEAK


def test_exposure_unions_overlapping_positions_instead_of_double_counting():
    # AAA open [0, 60_000); BBB open [30_000, 90_000): union is [0, 90_000) = 90_000ms, not 150_000ms.
    fills = [
        FillRecord(0, "AAA", "FILLED", Decimal(100), Decimal(1)),
        FillRecord(60_000, "AAA", "EXIT", Decimal(101), Decimal(1), net_pnl=Decimal(1)),
        FillRecord(30_000, "BBB", "FILLED", Decimal(50), Decimal(1)),
        FillRecord(90_000, "BBB", "EXIT", Decimal(49), Decimal(1), net_pnl=Decimal(-1)),
    ]
    result = compute_exposure([_result(REGULAR_DATE, fills, Decimal(1000))])
    assert result.open_ms == 90_000
    assert result.total_scheduled_ms == REGULAR_SCHEDULED_MS
    assert result.fraction == Decimal(90_000) / Decimal(REGULAR_SCHEDULED_MS)


def test_a_position_still_open_at_session_end_is_closed_at_the_scheduled_close_for_exposure():
    fills = [FillRecord(0, "AAA", "FILLED", Decimal(100), Decimal(1))]  # no EXIT recorded
    result = compute_exposure([_result(REGULAR_DATE, fills, Decimal(1000))])
    assert result.open_ms == REGULAR_SCHEDULED_MS  # open from the session's own open through its own close


def test_exposure_across_multiple_sessions_sums_scheduled_and_open_time():
    fills_a = [
        FillRecord(0, "AAA", "FILLED", Decimal(100), Decimal(1)),
        FillRecord(60_000, "AAA", "EXIT", Decimal(101), Decimal(1), net_pnl=Decimal(1)),
    ]
    fills_b = [
        FillRecord(0, "AAA", "FILLED", Decimal(100), Decimal(1)),
        FillRecord(120_000, "AAA", "EXIT", Decimal(99), Decimal(1), net_pnl=Decimal(-1)),
    ]
    early_scheduled_ms = session_duration_minutes(EARLY_CLOSE_DATE) * 60_000
    result = compute_exposure(
        [
            _result(EARLY_CLOSE_DATE, fills_a, Decimal(1001)),
            _result(REGULAR_DATE, fills_b, Decimal(1000)),
        ]
    )
    assert result.open_ms == 60_000 + 120_000
    assert result.total_scheduled_ms == early_scheduled_ms + REGULAR_SCHEDULED_MS
    assert result.fraction == Decimal(180_000) / Decimal(early_scheduled_ms + REGULAR_SCHEDULED_MS)


def test_zero_scheduled_time_across_no_sessions_is_an_explicit_unavailable_result():
    result = compute_exposure([])
    assert result.fraction == UNAVAILABLE_NO_SESSIONS
    assert result.total_scheduled_ms == 0


def test_multi_session_metrics_combines_each_sessions_own_fills_and_equity():
    fills_a = [
        FillRecord(0, "AAA", "FILLED", Decimal(100), Decimal(10)),
        FillRecord(60_000, "AAA", "EXIT", Decimal(110), Decimal(10), net_pnl=Decimal(100)),
    ]
    fills_b = [
        FillRecord(0, "AAA", "FILLED", Decimal(100), Decimal(10)),
        FillRecord(60_000, "AAA", "EXIT", Decimal(95), Decimal(10), net_pnl=Decimal(-50)),
    ]
    results = [
        _result(EARLY_CLOSE_DATE, fills_a, Decimal(1100)),
        _result(REGULAR_DATE, fills_b, Decimal(1050)),
    ]
    metrics = compute_multi_session_metrics(results, starting_equity=Decimal(1000))
    assert metrics.session_count == 2
    assert metrics.trade_metrics.completed_trade_count == 2
    assert metrics.trade_metrics.win_count == 1
    assert metrics.trade_metrics.loss_count == 1
    assert metrics.trade_metrics.realized_pnl == Decimal(50)
    assert metrics.trade_metrics.ending_equity == Decimal(1050)
    assert metrics.trade_metrics.net_change == Decimal(50)
    # Day 1 peaks at 1100, day 2 declines to 1050: a 50 drawdown from that peak.
    assert metrics.drawdown.max_drawdown == Decimal(50)
    assert metrics.drawdown.peak_equity == Decimal(1100)

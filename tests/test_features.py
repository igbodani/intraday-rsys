"""Causal historical feature tests (M2-02a, Step 3 of
docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md). H01-H03/DQ08 values are pinned in
fixtures/data_cases.json; this file reads them directly so a changed fixture value changes
the test input, not a hand-duplicated magic number."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from rsys.features import (
    MISSING_PREVIOUS_CLOSE,
    MISSING_SELECTED_HISTORY,
    MIXED_FEED_FEATURE,
    ZERO_DENOMINATOR,
    HistorySession,
    compute_cumulative_rvol,
    compute_dollar_volume_eligible,
    compute_session_gain,
    convert_split_units,
)

CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "data_cases.json").read_text()
)
_RVOL_CASES = {c["id"]: c for c in CASES["rvol_cases"]}


def _sessions(raw: list[dict]) -> list[HistorySession]:
    return [
        HistorySession(
            session_date=date.fromisoformat(row["date"]),
            duration_minutes=row["duration_minutes"],
            cumulative_volume=(
                Decimal(row["cumulative_volume"])
                if row["cumulative_volume"] is not None
                else None
            ),
            coverage_complete=row["coverage_complete"],
        )
        for row in raw
    ]


def _run(case_id: str):
    case = _RVOL_CASES[case_id]
    return case, compute_cumulative_rvol(
        elapsed_minutes=case["elapsed_minutes"],
        lookback_sessions=case["lookback"],
        current_volume=Decimal(case["current_volume"]),
        history_newest_first=_sessions(case["history_newest_first"]),
        feed="sip",
        history_feed="sip",
    )


def test_h01_morning_history_selects_full_and_shortened_sessions():
    case, result = _run("H01")
    assert [d.isoformat() for d in result.selected_dates] == case["expected_selected_dates"]
    assert result.mean_volume == Decimal(case["expected_mean_volume"])
    assert result.value == Decimal(case["expected_rvol"])
    assert result.status is None


def test_h02_afternoon_history_excludes_the_shortened_session():
    case, result = _run("H02")
    assert [d.isoformat() for d in result.selected_dates] == case["expected_selected_dates"]
    assert result.mean_volume == Decimal(case["expected_mean_volume"])
    assert result.value == Decimal(case["expected_rvol"])


def test_h03_selected_newest_session_missing_stays_unavailable():
    case, result = _run("H03")
    assert [d.isoformat() for d in result.selected_dates] == case["expected_selected_dates"]
    assert result.value is None
    assert result.status == case["expected_status"] == MISSING_SELECTED_HISTORY


def test_dq08_mixed_feed_history_is_rejected():
    case = _RVOL_CASES["H01"]
    result = compute_cumulative_rvol(
        elapsed_minutes=case["elapsed_minutes"],
        lookback_sessions=case["lookback"],
        current_volume=Decimal(case["current_volume"]),
        history_newest_first=_sessions(case["history_newest_first"]),
        feed="sip",
        history_feed="iex",
    )
    assert result.status == MIXED_FEED_FEATURE
    assert result.value is None


def test_insufficient_calendar_history_is_unavailable():
    sessions = _sessions(_RVOL_CASES["H01"]["history_newest_first"])[:1]  # only 1 of 2 needed
    result = compute_cumulative_rvol(
        elapsed_minutes=60, lookback_sessions=2, current_volume=Decimal(1200),
        history_newest_first=sessions, feed="sip", history_feed="sip",
    )
    assert result.status == MISSING_SELECTED_HISTORY
    assert result.value is None


def test_zero_denominator_is_unavailable_not_divide_by_zero():
    sessions = [
        HistorySession(date(2024, 12, 2), 390, Decimal(0), True),
        HistorySession(date(2024, 11, 29), 210, Decimal(0), True),
    ]
    result = compute_cumulative_rvol(
        elapsed_minutes=60, lookback_sessions=2, current_volume=Decimal(1200),
        history_newest_first=sessions, feed="sip", history_feed="sip",
    )
    assert result.status == ZERO_DENOMINATOR
    assert result.value is None


def test_missing_prior_close_is_unavailable_gain():
    result = compute_session_gain(latest_close=Decimal(103), previous_close=None)
    assert result.value is None
    assert result.status == MISSING_PREVIOUS_CLOSE


def test_session_gain_matches_hand_calculation():
    result = compute_session_gain(latest_close=Decimal(103), previous_close=Decimal(100))
    assert result.value == Decimal("0.03")
    assert result.status is None


def test_split_conversion_leaves_rvol_and_gain_invariant():
    """A consistent 2-for-1 split conversion of historical price/volume must not change the
    computed RVOL or gain versus an unconverted, already-current-unit comparison (F17-style,
    docs/data_rules.md)."""
    ratio = Decimal(2)
    raw_price, raw_volume = Decimal(200), Decimal(300)
    converted_price, converted_volume = convert_split_units(
        price=raw_price, volume=raw_volume, ratio=ratio
    )
    assert converted_price == Decimal(100)
    assert converted_volume == Decimal(600)

    current_sessions = [
        HistorySession(date(2024, 12, 2), 390, Decimal(600), True),
        HistorySession(date(2024, 11, 29), 210, Decimal(400), True),
    ]
    baseline = compute_cumulative_rvol(
        elapsed_minutes=60, lookback_sessions=2, current_volume=Decimal(1200),
        history_newest_first=current_sessions, feed="sip", history_feed="sip",
    )
    doubled_sessions = [
        HistorySession(s.session_date, s.duration_minutes, s.cumulative_volume * ratio, True)
        for s in current_sessions
    ]
    converted = compute_cumulative_rvol(
        elapsed_minutes=60, lookback_sessions=2, current_volume=Decimal(1200) * ratio,
        history_newest_first=doubled_sessions, feed="sip", history_feed="sip",
    )
    assert converted.value == baseline.value

    # A split effective since the previous session: its raw close (200) converts to 100 in
    # current units, while today's already-current-unit close (103) is unaffected.
    gain_baseline = compute_session_gain(latest_close=Decimal(103), previous_close=Decimal(100))
    previous_close_converted, _ = convert_split_units(
        price=Decimal(200), volume=Decimal(0), ratio=ratio
    )
    gain_converted = compute_session_gain(
        latest_close=Decimal(103), previous_close=previous_close_converted
    )
    assert gain_converted.value == gain_baseline.value


def test_dollar_volume_eligibility_requires_an_explicit_threshold():
    assert compute_dollar_volume_eligible(
        prior_session_volume=Decimal(1_000_000), prior_session_close=Decimal(10),
        min_dollar_volume=Decimal(5_000_000),
    )
    assert not compute_dollar_volume_eligible(
        prior_session_volume=Decimal(1_000_000), prior_session_close=Decimal(10),
        min_dollar_volume=Decimal(50_000_000),
    )


def test_dollar_volume_eligibility_missing_prior_data_is_not_eligible():
    assert not compute_dollar_volume_eligible(
        prior_session_volume=None, prior_session_close=Decimal(10),
        min_dollar_volume=Decimal(1),
    )

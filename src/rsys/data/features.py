"""Causal historical features for the scanner (M2-02a, Step 3 of
docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md). See docs/data_rules.md "RVOL and early-close
sessions" and docs/contracts.md "Scanner baseline" (D14, proposed) for the governing
semantics; `fixtures/data_cases.json` (H01-H03, DQ08) pins the exact expected values.

Honesty note: this module computes RVOL/gain/dollar-volume from already-supplied,
calendar-ordered prior-session observations. It does not implement a real historical data
loader, feed entitlement check or dollar-volume eligibility threshold (D12/D13/D14 remain
Open/Proposed); a caller (fixture or future provider adapter) must supply `HistorySession`
rows and an explicit `min_dollar_volume`. Nothing here invents a missing market assumption.
"""

from __future__ import annotations

from decimal import Decimal

from rsys.data.models import HistorySession, RvolResult, SessionGainResult

MISSING_SELECTED_HISTORY = "MISSING_SELECTED_HISTORY"
ZERO_DENOMINATOR = "ZERO_DENOMINATOR"
MISSING_PREVIOUS_CLOSE = "MISSING_PREVIOUS_CLOSE"
MIXED_FEED_FEATURE = "MIXED_FEED_FEATURE"


def compute_cumulative_rvol(
    *,
    elapsed_minutes: int,
    lookback_sessions: int,
    current_volume: Decimal,
    history_newest_first: list[HistorySession],
    feed: str,
    history_feed: str,
    max_lookback_calendar_sessions: int = 252,
) -> RvolResult:
    """Select the most recent `lookback_sessions` calendar-comparable prior sessions whose
    *scheduled* duration reaches `elapsed_minutes`, searching at most
    `max_lookback_calendar_sessions` sessions newest-first. Selection is by calendar/duration
    before any volume is inspected; a selected session's missing/incomplete data makes the
    result unavailable -- it is never skipped in favor of an older session (docs/data_rules.md).
    """
    if feed != history_feed:
        return RvolResult(None, (), None, MIXED_FEED_FEATURE)
    if lookback_sessions < 1:
        raise ValueError("lookback_sessions must be a positive integer")
    if current_volume < 0 or not current_volume.is_finite():
        raise ValueError("current_volume must be a nonnegative finite Decimal")

    selected: list[HistorySession] = []
    for session in history_newest_first[:max_lookback_calendar_sessions]:
        if session.duration_minutes >= elapsed_minutes:
            selected.append(session)
            if len(selected) == lookback_sessions:
                break
    selected_dates = tuple(s.session_date for s in selected)
    if len(selected) < lookback_sessions:
        return RvolResult(None, selected_dates, None, MISSING_SELECTED_HISTORY)

    volumes: list[Decimal] = []
    for session in selected:
        if not session.coverage_complete or session.cumulative_volume is None:
            return RvolResult(None, selected_dates, None, MISSING_SELECTED_HISTORY)
        volumes.append(session.cumulative_volume)

    mean_volume = sum(volumes, Decimal(0)) / Decimal(len(volumes))
    if mean_volume == 0:
        return RvolResult(None, selected_dates, mean_volume, ZERO_DENOMINATOR)
    return RvolResult(current_volume / mean_volume, selected_dates, mean_volume, None)


def compute_session_gain(
    *, latest_close: Decimal, previous_close: Decimal | None
) -> SessionGainResult:
    """`latest completed raw close / previous regular-session close (current split units) - 1`.
    Cash-dividend price effects stay in this price-return gain; this function does not flag
    or adjust for them -- the caller retains any known ex-dividend flag separately."""
    if previous_close is None:
        return SessionGainResult(None, MISSING_PREVIOUS_CLOSE)
    if (
        previous_close <= 0
        or not previous_close.is_finite()
        or not latest_close.is_finite()
    ):
        raise ValueError(
            "close prices must be finite and previous_close must be positive"
        )
    return SessionGainResult(latest_close / previous_close - 1, None)


def convert_split_units(
    *, price: Decimal, volume: Decimal, ratio: Decimal
) -> tuple[Decimal, Decimal]:
    """`ratio = new_shares / old_shares` for a split effective between the historical
    observation and today. Historical price becomes `price / ratio`; historical volume
    becomes `volume * ratio` (docs/data_rules.md). Compose multiple ratios before calling."""
    if ratio <= 0 or not ratio.is_finite():
        raise ValueError("split ratio must be a positive finite Decimal")
    return price / ratio, volume * ratio


def compute_dollar_volume_eligible(
    *,
    prior_session_volume: Decimal | None,
    prior_session_close: Decimal | None,
    min_dollar_volume: Decimal,
) -> bool:
    """Prior-day dollar volume = prior session volume * prior session close, both already in
    current split units. `min_dollar_volume` has no proposed/registered default (D14 leaves the
    exact value unset "until the data audit", docs/contracts.md): callers must always supply
    it explicitly, never a module-level fallback. Missing prior volume/close is not eligible."""
    if min_dollar_volume <= 0 or not min_dollar_volume.is_finite():
        raise ValueError("min_dollar_volume must be a positive finite Decimal")
    if prior_session_volume is None or prior_session_close is None:
        return False
    return prior_session_volume * prior_session_close >= min_dollar_volume

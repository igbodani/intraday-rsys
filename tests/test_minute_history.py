"""Minute-addressable, as-of scanner history tests (Step 1 of
docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md). These are independently hand-calculated synthetic
fixtures -- never call them backtest results. They extend `data/history.py`'s existing dated
prior-session boundary (`prior_sessions`, `FixtureHistoryProvider`) with a minute-level
`MinuteHistoryProvider` that selects, among declared revisions, only what was visible by an
explicit decision cutoff, then feed that straight through the unchanged
`features.compute_cumulative_rvol`/`compute_session_gain`/`compute_dollar_volume_eligible` and
`strategies.history.candidate_input`/`strategies.scanner.rank_candidates` pipeline."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from rsys.data.calendar import session_duration_minutes, to_exchange_utc
from rsys.data.features import (
    MISSING_SELECTED_HISTORY,
    ZERO_DENOMINATOR,
    compute_cumulative_rvol,
)
from rsys.data.history import (
    MISSING_MINUTE,
    UNSUPPORTED_UNIT_BASIS,
    MinuteHistoryProvider,
    candidate_prior_session_dates,
)
from rsys.data.models import (
    MinuteObservation,
    MinuteSymbolHistory,
    ObservationStatus,
    UnitBasis,
)
from rsys.strategies.history import candidate_input
from rsys.strategies.scanner import NOT_ELIGIBLE, rank_candidates

FEED = "sip"
SOURCE = "fixture-minute-loader"


def _at(local_naive: datetime) -> datetime:
    """An exchange-local wall-clock moment, converted to tz-aware UTC -- used both for
    `available_at` revision timestamps and decision-cutoff `as_of` values."""
    return to_exchange_utc(local_naive)


def _obs(
    *,
    symbol: str,
    session_date: date,
    minute_index: int,
    available_at: datetime,
    volume,
    close,
    revision: int = 0,
    status: ObservationStatus = ObservationStatus.TRADED,
    unit_basis: UnitBasis = UnitBasis.RAW,
) -> MinuteObservation:
    return MinuteObservation(
        instrument_id=symbol,
        feed=FEED,
        source=SOURCE,
        session_date=session_date,
        minute_index=minute_index,
        available_at=available_at,
        revision=revision,
        status=status,
        unit_basis=unit_basis,
        close=close,
        volume=Decimal(volume),
    )


def _full_session(
    symbol: str,
    session_date: date,
    *,
    explicit_volumes: list,
    filler_volume,
    close: Decimal,
    available_at: datetime,
) -> list[MinuteObservation]:
    """Every minute of `session_date`'s declared scheduled duration: the first
    `len(explicit_volumes)` minutes take the given hand-picked volumes, the rest take a
    constant `filler_volume` so the full-session total stays trivially hand-computable
    (`sum(explicit_volumes) + filler_count * filler_volume`). Used for the immediately prior
    trading session, whose full-session close/volume feed gain and dollar-volume eligibility.
    """
    duration = session_duration_minutes(session_date)
    volumes = list(explicit_volumes) + [filler_volume] * (duration - len(explicit_volumes))
    return [
        _obs(
            symbol=symbol,
            session_date=session_date,
            minute_index=i,
            available_at=available_at,
            volume=v,
            close=close,
        )
        for i, v in enumerate(volumes)
    ]


def _constant_session(
    symbol: str, session_date: date, *, minutes: int, volume, close, available_at: datetime
) -> list[MinuteObservation]:
    return [
        _obs(
            symbol=symbol,
            session_date=session_date,
            minute_index=i,
            available_at=available_at,
            volume=volume,
            close=close,
        )
        for i in range(minutes)
    ]


# 1. Hand-calculated multi-session RVOL (and the gain/eligibility it feeds into).


def test_two_session_rvol_gain_and_eligibility_match_the_hand_calculation():
    as_of = _at(datetime(2024, 12, 3, 9, 33))
    observations = [
        *_full_session(
            "AAA",
            date(2024, 12, 2),
            explicit_volumes=[300, 250, 250],
            filler_volume=1,
            close=Decimal("100.50"),
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
        ),
        *[
            _obs(
                symbol="AAA",
                session_date=date(2024, 11, 29),
                minute_index=i,
                available_at=_at(datetime(2024, 11, 29, 13, 5)),
                volume=v,
                close=Decimal(97),
            )
            for i, v in enumerate([150, 150, 100])
        ],
    ]
    provider = MinuteHistoryProvider(
        {"AAA": MinuteSymbolHistory(observations)}, feed=FEED, elapsed_minutes=3, as_of=as_of
    )

    row = candidate_input(
        symbol="AAA",
        decision_date=date(2024, 12, 3),
        elapsed_minutes=3,
        current_volume=Decimal(1200),
        latest_close=Decimal("103.515"),
        provider=provider,
        lookback_sessions=2,
        min_dollar_volume=Decimal(100000),
        feed=FEED,
        history_feed=FEED,
    )
    # mean(800, 400) = 600; 1200 / 600 = 2. 103.515 / 100.50 - 1 = 0.03.
    assert row.rvol == Decimal(2)
    assert row.gain == Decimal("0.03")
    assert row.dollar_volume_eligible is True

    builds = provider.session_builds("AAA")
    assert builds[0].history_session.session_date == date(2024, 12, 2)
    # The RVOL lookback only ever needs the same elapsed 3 minutes as today (300+250+250);
    # the full 1187-share session total belongs to the separate previous-session evidence
    # below, used for gain/dollar-volume, not this denominator session.
    assert builds[0].history_session.cumulative_volume == Decimal(800)
    assert len(builds[0].selected_revisions) == 3
    assert builds[0].exclusion_reason is None
    assert builds[1].history_session.session_date == date(2024, 11, 29)
    assert builds[1].history_session.cumulative_volume == Decimal(400)

    previous = provider.previous_session_build("AAA")
    assert previous.session_date == date(2024, 12, 2)
    assert previous.close == Decimal("100.50")
    assert previous.volume == Decimal(1187)
    assert previous.exclusion_reason is None


# 2. Missing/zero denominators and insufficient history.


def test_missing_minute_marks_the_session_unavailable_not_zero():
    as_of = _at(datetime(2024, 12, 3, 9, 33))
    observations = [
        _obs(
            symbol="AAA",
            session_date=date(2024, 12, 2),
            minute_index=0,
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
            volume=100,
            close=Decimal(100),
        ),
        # minute 1 is never observed at all: a genuine coverage gap, not zero volume.
    ]
    provider = MinuteHistoryProvider(
        {"AAA": MinuteSymbolHistory(observations)}, feed=FEED, elapsed_minutes=2, as_of=as_of
    )
    sessions = provider.sessions_before("AAA", date(2024, 12, 3))
    build = provider.session_builds("AAA")[0]
    assert build.exclusion_reason == MISSING_MINUTE
    assert build.history_session.coverage_complete is False
    assert build.history_session.cumulative_volume is None

    result = compute_cumulative_rvol(
        elapsed_minutes=2,
        lookback_sessions=1,
        current_volume=Decimal(100),
        history_newest_first=sessions,
        feed=FEED,
        history_feed=FEED,
    )
    assert result.status == MISSING_SELECTED_HISTORY
    assert result.value is None


def test_zero_denominator_is_distinguished_from_missing_history():
    as_of = _at(datetime(2024, 12, 3, 9, 33))
    observations = [
        _obs(
            symbol="AAA",
            session_date=date(2024, 12, 2),
            minute_index=i,
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
            volume=0,
            close=None,
            status=ObservationStatus.NO_TRADE,
        )
        for i in range(2)
    ]
    provider = MinuteHistoryProvider(
        {"AAA": MinuteSymbolHistory(observations)}, feed=FEED, elapsed_minutes=2, as_of=as_of
    )
    sessions = provider.sessions_before("AAA", date(2024, 12, 3))
    build = provider.session_builds("AAA")[0]
    # Every required minute is a *verified* no-trade interval, not a gap: coverage is
    # complete and the (correctly zero) volume is retained, never treated as missing.
    assert build.exclusion_reason is None
    assert build.history_session.coverage_complete is True
    assert build.history_session.cumulative_volume == Decimal(0)

    result = compute_cumulative_rvol(
        elapsed_minutes=2,
        lookback_sessions=1,
        current_volume=Decimal(100),
        history_newest_first=sessions,
        feed=FEED,
        history_feed=FEED,
    )
    assert result.status == ZERO_DENOMINATOR
    assert result.value is None


def test_insufficient_calendar_history_before_a_new_calendar_year():
    # 2024-01-02 is the second trading day of the declared calendar: 01-01 is a holiday and
    # the calendar does not cover 2023, so no prior trading session can exist to select.
    assert candidate_prior_session_dates(date(2024, 1, 2)) == []
    provider = MinuteHistoryProvider(
        {}, feed=FEED, elapsed_minutes=5, as_of=_at(datetime(2024, 1, 2, 9, 36))
    )
    sessions = provider.sessions_before("AAA", date(2024, 1, 2))
    assert sessions == []
    result = compute_cumulative_rvol(
        elapsed_minutes=5,
        lookback_sessions=1,
        current_volume=Decimal(10),
        history_newest_first=sessions,
        feed=FEED,
        history_feed=FEED,
    )
    assert result.status == MISSING_SELECTED_HISTORY


# 3. Early-close sessions and mismatched units.


def test_early_close_duration_excludes_the_shortened_session_at_an_afternoon_elapsed_minute():
    as_of = _at(datetime(2024, 12, 3, 20, 33))
    observations = [
        *_constant_session(
            "AAA",
            date(2024, 12, 2),
            minutes=300,
            volume=2,
            close=Decimal(100),
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
        ),
        *_constant_session(
            "AAA",
            date(2024, 11, 27),
            minutes=300,
            volume=1,
            close=Decimal(100),
            available_at=_at(datetime(2024, 11, 27, 16, 5)),
        ),
        # 2024-11-29 is a declared early close (210 scheduled minutes): deliberately no data
        # supplied for it, since it cannot reach elapsed minute 300 regardless of coverage.
    ]
    provider = MinuteHistoryProvider(
        {"AAA": MinuteSymbolHistory(observations)}, feed=FEED, elapsed_minutes=300, as_of=as_of
    )
    sessions = provider.sessions_before("AAA", date(2024, 12, 3))
    result = compute_cumulative_rvol(
        elapsed_minutes=300,
        lookback_sessions=2,
        current_volume=Decimal(900),
        history_newest_first=sessions,
        feed=FEED,
        history_feed=FEED,
    )
    assert [d.isoformat() for d in result.selected_dates] == ["2024-12-02", "2024-11-27"]
    assert result.value == Decimal(2)  # mean(600, 300) = 450; 900 / 450 = 2
    assert result.status is None

    skipped = next(s for s in sessions if s.session_date == date(2024, 11, 29))
    assert skipped.duration_minutes == 210  # the real declared early-close duration


def test_mismatched_unit_basis_is_rejected_not_silently_converted():
    as_of = _at(datetime(2024, 12, 3, 9, 33))
    observations = [
        _obs(
            symbol="AAA",
            session_date=date(2024, 12, 2),
            minute_index=0,
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
            volume=100,
            close=Decimal(100),
        ),
        _obs(
            symbol="AAA",
            session_date=date(2024, 12, 2),
            minute_index=1,
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
            volume=200,
            close=Decimal(101),
            unit_basis=UnitBasis.SPLIT_ADJUSTED,
        ),
    ]
    provider = MinuteHistoryProvider(
        {"AAA": MinuteSymbolHistory(observations)}, feed=FEED, elapsed_minutes=2, as_of=as_of
    )
    sessions = provider.sessions_before("AAA", date(2024, 12, 3))
    build = provider.session_builds("AAA")[0]
    assert build.exclusion_reason == UNSUPPORTED_UNIT_BASIS
    assert build.history_session.coverage_complete is False

    result = compute_cumulative_rvol(
        elapsed_minutes=2,
        lookback_sessions=1,
        current_volume=Decimal(100),
        history_newest_first=sessions,
        feed=FEED,
        history_feed=FEED,
    )
    assert result.status == MISSING_SELECTED_HISTORY


# 4. Delayed observations and revisions arriving after the cutoff.


def test_delayed_first_publication_and_a_later_revision_are_cutoff_gated():
    minute0 = _obs(
        symbol="AAA",
        session_date=date(2024, 12, 2),
        minute_index=0,
        available_at=_at(datetime(2024, 12, 2, 16, 5)),
        volume=100,
        close=Decimal(100),
    )
    minute1_rev0 = _obs(
        symbol="AAA",
        session_date=date(2024, 12, 2),
        minute_index=1,
        available_at=_at(datetime(2024, 12, 3, 15, 0)),  # delayed: published mid-next-day
        volume=200,
        close=Decimal(101),
        revision=0,
    )
    minute1_rev1 = _obs(
        symbol="AAA",
        session_date=date(2024, 12, 2),
        minute_index=1,
        available_at=_at(datetime(2024, 12, 3, 16, 0)),  # a later correction
        volume=250,
        close=Decimal(102),
        revision=1,
    )
    observations = [minute0, minute1_rev0, minute1_rev1]

    def _at_cutoff(as_of):
        provider = MinuteHistoryProvider(
            {"AAA": MinuteSymbolHistory(observations)},
            feed=FEED,
            elapsed_minutes=2,
            as_of=as_of,
        )
        session = provider.sessions_before("AAA", date(2024, 12, 3))[0]
        return session, provider.session_builds("AAA")[0]

    early_session, early_build = _at_cutoff(_at(datetime(2024, 12, 3, 14, 33)))
    assert early_build.exclusion_reason == MISSING_MINUTE  # revision 0 not yet available
    assert early_session.coverage_complete is False

    mid_session, mid_build = _at_cutoff(_at(datetime(2024, 12, 3, 15, 30)))
    assert mid_build.exclusion_reason is None
    assert mid_session.cumulative_volume == Decimal(300)  # 100 + revision 0's 200
    assert mid_build.selected_revisions[1].revision == 0

    late_session, late_build = _at_cutoff(_at(datetime(2024, 12, 3, 16, 30)))
    assert late_session.cumulative_volume == Decimal(350)  # 100 + revision 1's corrected 250
    assert late_build.selected_revisions[1].revision == 1


# 5/6. Future-data mutations leave earlier results unchanged, with a negative control.


def test_future_revision_mutation_leaves_an_earlier_decision_unchanged_with_negative_control():
    as_of = _at(datetime(2024, 12, 3, 9, 33))
    minute0 = _obs(
        symbol="AAA",
        session_date=date(2024, 12, 2),
        minute_index=0,
        available_at=_at(datetime(2024, 12, 2, 16, 5)),
        volume=100,
        close=Decimal(100),
    )
    minute1 = _obs(
        symbol="AAA",
        session_date=date(2024, 12, 2),
        minute_index=1,
        available_at=_at(datetime(2024, 12, 2, 16, 5)),
        volume=200,
        close=Decimal(100),
    )

    def _rvol(observations):
        provider = MinuteHistoryProvider(
            {"AAA": MinuteSymbolHistory(observations)},
            feed=FEED,
            elapsed_minutes=2,
            as_of=as_of,
        )
        sessions = provider.sessions_before("AAA", date(2024, 12, 3))
        return compute_cumulative_rvol(
            elapsed_minutes=2,
            lookback_sessions=1,
            current_volume=Decimal(900),
            history_newest_first=sessions,
            feed=FEED,
            history_feed=FEED,
        )

    baseline = _rvol([minute0, minute1])

    future_correction = _obs(
        symbol="AAA",
        session_date=date(2024, 12, 2),
        minute_index=1,
        available_at=_at(datetime(2024, 12, 3, 20, 0)),  # published well after `as_of`
        volume=999,
        close=Decimal(999),
        revision=1,
    )
    with_future = _rvol([minute0, minute1, future_correction])
    assert with_future == baseline

    # Negative control: mutating an *eligible*, already-visible earlier observation does
    # change the result, proving the invariance above is not vacuous.
    changed_minute0 = _obs(
        symbol="AAA",
        session_date=date(2024, 12, 2),
        minute_index=0,
        available_at=_at(datetime(2024, 12, 2, 16, 5)),
        volume=150,
        close=Decimal(100),
    )
    changed = _rvol([changed_minute0, minute1])
    assert changed != baseline


# 7. Integration: minute observations -> prior-session selection -> RVOL/gain/eligibility ->
# scanner ranking, for two symbols.


def test_history_to_ranking_integration_fixture():
    as_of = _at(datetime(2024, 12, 3, 9, 33))
    aaa_observations = [
        *_full_session(
            "AAA",
            date(2024, 12, 2),
            explicit_volumes=[300, 250, 250],
            filler_volume=1,
            close=Decimal("100.50"),
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
        ),
        *[
            _obs(
                symbol="AAA",
                session_date=date(2024, 11, 29),
                minute_index=i,
                available_at=_at(datetime(2024, 11, 29, 13, 5)),
                volume=v,
                close=Decimal(97),
            )
            for i, v in enumerate([150, 150, 100])
        ],
    ]
    bbb_observations = [
        *_full_session(
            "BBB",
            date(2024, 12, 2),
            explicit_volumes=[30, 25, 25],
            filler_volume=Decimal("0.01"),
            close=Decimal(5),
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
        ),
        *[
            _obs(
                symbol="BBB",
                session_date=date(2024, 11, 29),
                minute_index=i,
                available_at=_at(datetime(2024, 11, 29, 13, 5)),
                volume=v,
                close=Decimal(5),
            )
            for i, v in enumerate([15, 15, 10])
        ],
    ]
    provider = MinuteHistoryProvider(
        {
            "AAA": MinuteSymbolHistory(aaa_observations),
            "BBB": MinuteSymbolHistory(bbb_observations),
        },
        feed=FEED,
        elapsed_minutes=3,
        as_of=as_of,
    )

    rows = [
        candidate_input(
            symbol="AAA",
            decision_date=date(2024, 12, 3),
            elapsed_minutes=3,
            current_volume=Decimal(1200),
            latest_close=Decimal("103.515"),
            provider=provider,
            lookback_sessions=2,
            min_dollar_volume=Decimal(100000),
            feed=FEED,
            history_feed=FEED,
        ),
        candidate_input(
            symbol="BBB",
            decision_date=date(2024, 12, 3),
            elapsed_minutes=3,
            current_volume=Decimal(120),
            latest_close=Decimal("5.15"),
            provider=provider,
            lookback_sessions=2,
            min_dollar_volume=Decimal(100000),
            feed=FEED,
            history_feed=FEED,
        ),
    ]
    assert rows[0].dollar_volume_eligible is True
    assert rows[1].dollar_volume_eligible is False  # BBB's prior-day dollar volume is tiny

    result = rank_candidates(rows, top_n=10)
    assert result.top == ["AAA"]
    assert result.ranked == ["AAA"]
    assert result.excluded == {"BBB": NOT_ELIGIBLE}
    assert result.ranked_candidates[0].rvol == Decimal(2)
    assert result.ranked_candidates[0].gain == Decimal("0.03")


# Additional ambiguous/invalid-input rejections.


def test_conflicting_same_revision_observations_are_rejected_as_ambiguous():
    as_of = _at(datetime(2024, 12, 3, 9, 33))
    conflicting = [
        _obs(
            symbol="AAA",
            session_date=date(2024, 12, 2),
            minute_index=0,
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
            volume=100,
            close=Decimal(100),
        ),
        _obs(
            symbol="AAA",
            session_date=date(2024, 12, 2),
            minute_index=0,
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
            volume=150,  # same instrument/session/minute/revision, conflicting payload
            close=Decimal(100),
        ),
    ]
    provider = MinuteHistoryProvider(
        {"AAA": MinuteSymbolHistory(conflicting)}, feed=FEED, elapsed_minutes=1, as_of=as_of
    )
    with pytest.raises(ValueError, match="ambiguous minute revision"):
        provider.sessions_before("AAA", date(2024, 12, 3))


def test_contradictory_no_trade_observation_is_rejected():
    as_of = _at(datetime(2024, 12, 3, 9, 33))
    bad = [
        _obs(
            symbol="AAA",
            session_date=date(2024, 12, 2),
            minute_index=0,
            available_at=_at(datetime(2024, 12, 2, 16, 5)),
            volume=10,  # a verified NO_TRADE minute cannot also carry nonzero volume
            close=None,
            status=ObservationStatus.NO_TRADE,
        ),
    ]
    provider = MinuteHistoryProvider(
        {"AAA": MinuteSymbolHistory(bad)}, feed=FEED, elapsed_minutes=1, as_of=as_of
    )
    with pytest.raises(ValueError, match="NO_TRADE"):
        provider.sessions_before("AAA", date(2024, 12, 3))

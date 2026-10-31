"""Independent boundary and provenance regressions for the as-of history adapter."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from rsys.data.features import compute_cumulative_rvol
from rsys.data.history import MinuteHistoryProvider, build_minute_history_sessions
from rsys.data.models import (
    MinuteObservation,
    MinuteSymbolHistory,
    ObservationStatus,
    UnitBasis,
)
from rsys.strategies.history import candidate_input
from rsys.strategies.scanner import rank_candidates

DAY = date(2024, 12, 3)
PRIOR = date(2024, 12, 2)
CUTOFF = datetime(2024, 12, 3, 14, 31, tzinfo=UTC)


def observation(*, symbol="AAA", day=PRIOR, minute=0, volume="100"):
    return MinuteObservation(
        instrument_id=symbol,
        feed="sip",
        source="fixture-A",
        session_date=day,
        minute_index=minute,
        available_at=datetime(2024, 12, 2, 22, tzinfo=UTC),
        revision=0,
        status=ObservationStatus.TRADED,
        unit_basis=UnitBasis.RAW,
        close=Decimal(100),
        volume=Decimal(volume),
    )


def provider(rows, elapsed=1):
    return MinuteHistoryProvider(
        {"AAA": MinuteSymbolHistory(rows)},
        feed="sip",
        elapsed_minutes=elapsed,
        as_of=CUTOFF,
        max_lookback_calendar_sessions=3,
    )


@pytest.mark.parametrize("elapsed", [0, -1, 1.5, True])
@pytest.mark.parametrize("entry", ["provider", "builder"])
def test_invalid_elapsed_minutes_cannot_fabricate_complete_zero_history(elapsed, entry):
    with pytest.raises(ValueError, match="elapsed_minutes"):
        if entry == "provider":
            provider([], elapsed)
        else:
            build_minute_history_sessions(
                [],
                symbol="AAA",
                feed="sip",
                decision_date=DAY,
                elapsed_minutes=elapsed,
                as_of=CUTOFF,
            )


@pytest.mark.parametrize(
    "changes",
    [
        {"volume": Decimal(-100)},
        {"volume": Decimal("NaN")},
        {"volume": Decimal("sNaN")},
        {"volume": Decimal("Infinity")},
        {"close": None},
        {"close": Decimal(0)},
        {"close": Decimal(-1)},
        {"close": Decimal("NaN")},
        {"close": Decimal("Infinity")},
    ],
)
def test_invalid_traded_payload_is_rejected_for_rvol_and_previous_session(changes):
    bad = replace(observation(), **changes)
    # A negative first volume must not cancel part of the second minute's 200 shares
    # and create the superficially valid denominator 100 (and RVOL 600 / 100 = 6).
    p = provider([bad, observation(minute=1, volume="200")], elapsed=2)
    with pytest.raises(ValueError, match="volume|close"):
        p.sessions_before("AAA", DAY)
    with pytest.raises(ValueError, match="volume|close"):
        p.previous_session_close("AAA", DAY)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "UNKNOWN"},
        {"revision": -1},
        {"revision": 0.5},
        {"available_at": datetime(2024, 12, 2, 22)},
    ],
)
def test_invalid_observation_identity_is_rejected(changes):
    with pytest.raises(ValueError, match="status|revision|timezone"):
        provider([replace(observation(), **changes)]).sessions_before("AAA", DAY)


def test_naive_cutoff_is_rejected_even_with_no_observations():
    with pytest.raises(ValueError, match="timezone"):
        MinuteHistoryProvider(
            {}, feed="sip", elapsed_minutes=1, as_of=CUTOFF.replace(tzinfo=None)
        )


def test_excluded_early_close_is_not_read_but_is_validated_when_comparable():
    rows = [observation(minute=i, volume="2") for i in range(300)]
    rows += [
        observation(day=date(2024, 11, 27), minute=i, volume="1") for i in range(300)
    ]
    short = observation(day=date(2024, 11, 29))
    # These conflict, but the 210-minute session cannot contribute to a minute-300 RVOL.
    rows += [short, replace(short, volume=Decimal(999))]
    p = provider(rows, elapsed=300)
    result = compute_cumulative_rvol(
        elapsed_minutes=300,
        lookback_sessions=2,
        current_volume=Decimal(900),
        history_newest_first=p.sessions_before("AAA", DAY),
        feed="sip",
        history_feed="sip",
    )
    assert result.value == Decimal(2)  # 900 / mean(600, 300)
    assert result.selected_dates == (PRIOR, date(2024, 11, 27))
    skipped = p.session_builds("AAA")[1]
    assert skipped.history_session.duration_minutes == 210
    assert skipped.history_session.cumulative_volume is None
    assert skipped.history_session.coverage_complete is False
    assert skipped.selected_revisions == ()
    assert skipped.exclusion_reason == "SESSION_TOO_SHORT"
    # Negative control: at minute 1 that same early-close session is comparable.
    with pytest.raises(ValueError, match="ambiguous minute revision"):
        provider(rows).sessions_before("AAA", DAY)


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "fixture-B"},
        {"available_at": CUTOFF - timedelta(minutes=1)},
    ],
)
def test_conflicting_revision_provenance_is_rejected_in_both_input_orders(changes):
    first = observation()
    other = replace(first, **changes)
    for rows in ([first, other], [other, first]):
        with pytest.raises(ValueError, match="ambiguous minute revision"):
            provider(rows).sessions_before("AAA", DAY)


def test_exact_duplicates_leave_volume_and_retained_evidence_unchanged():
    a, b = observation(), observation(minute=1, volume="200")
    baseline = provider([a, b], elapsed=2)
    expected = baseline.sessions_before("AAA", DAY)
    duplicated = provider([b, replace(a), a, replace(b)], elapsed=2)
    assert duplicated.sessions_before("AAA", DAY) == expected
    assert duplicated.session_builds("AAA") == baseline.session_builds("AAA")
    assert expected[0].cumulative_volume == Decimal(300)


def test_future_invalid_revision_preserves_full_ranking_and_retained_evidence():
    histories = {
        symbol: [
            observation(symbol=symbol, minute=i, volume=first if i == 0 else "1")
            for i in range(390)
        ]
        for symbol, first in (("AAA", "100"), ("BBB", "200"))
    }

    def snapshot(rows):
        p = MinuteHistoryProvider(
            {symbol: MinuteSymbolHistory(values) for symbol, values in rows.items()},
            feed="sip",
            elapsed_minutes=1,
            as_of=CUTOFF,
            max_lookback_calendar_sessions=1,
        )
        candidates = [
            candidate_input(
                symbol=symbol,
                decision_date=DAY,
                elapsed_minutes=1,
                current_volume=Decimal(600),
                latest_close=Decimal(103),
                provider=p,
                lookback_sessions=1,
                min_dollar_volume=Decimal(1),
                feed="sip",
                history_feed="sip",
            )
            for symbol in rows
        ]
        return (
            rank_candidates(candidates, top_n=2),
            {s: p.session_builds(s) for s in rows},
            {s: p.previous_session_build(s) for s in rows},
        )

    baseline = snapshot(histories)
    assert baseline[0].ranked == ["AAA", "BBB"]  # RVOL 6 versus 3
    future = replace(
        histories["AAA"][0],
        revision=1,
        available_at=CUTOFF + timedelta(hours=1),
        volume=Decimal("NaN"),
        close=Decimal(-1),
    )
    assert snapshot({**histories, "AAA": histories["AAA"] + [future]}) == baseline
    changed = replace(histories["AAA"][0], volume=Decimal(300))
    past_changed = snapshot({**histories, "AAA": [changed, *histories["AAA"][1:]]})
    assert past_changed[0].ranked == ["BBB", "AAA"]  # RVOL 3 versus 2

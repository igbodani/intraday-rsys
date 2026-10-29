import random
from decimal import Decimal

import pytest

from rsys.data.events import (
    CorporateActionEvent,
    EventTimeMeaning,
    ProviderBarEvent,
    QualityState,
    TimerEvent,
    TimingLabel,
    TradeEvent,
)
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.replay import (
    IncompleteReplayError,
    LateInsertionError,
    Phase,
    ReplayProfile,
    ReplayStream,
    UnsupportedPhaseError,
    assert_stream_covers_boundary,
    event_phase,
    replay_order,
    same_time_batch,
)

T0 = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")
T1 = parse_utc_timestamp_ns("2024-06-10T13:31:00Z")
SESSION_CLOSE = parse_utc_timestamp_ns("2024-06-10T20:00:00Z")

_COMMON = {
    "provider": "alpaca",
    "feed": "fixture",
    "event_time_meaning": EventTimeMeaning.PROVIDER_MARKET_TIME,
    "session_version": "nyse-2024-v1",
    "quality_state": QualityState.OK,
    "timing_label": TimingLabel.RECORDED,
}


def _trade(event_id, instrument_id, at_ns, *, source_sequence=None):
    return TradeEvent(
        **_COMMON, event_id=event_id, instrument_id=instrument_id, event_time_ns=at_ns, available_at_ns=at_ns,
        source_sequence=source_sequence, price=Decimal(100), size=Decimal(10), trade_id=event_id, venue="V",
    )


def _bar(event_id, instrument_id, at_ns, *, bar_revision=1):
    return ProviderBarEvent(
        **_COMMON, event_id=event_id, instrument_id=instrument_id, event_time_ns=at_ns, available_at_ns=at_ns,
        bar_start_ns=at_ns - 60_000_000_000, bar_end_ns=at_ns, interval="1Min",
        open=Decimal(100), high=Decimal(101), low=Decimal(99), close=Decimal(100), volume=Decimal(10),
        adjustment_mode="raw", bar_revision=bar_revision,
    )


def _timer(event_id, at_ns, boundary_name="session_close"):
    return TimerEvent(
        **{**_COMMON, "event_time_meaning": EventTimeMeaning.SCHEDULED_BOUNDARY},
        event_id=event_id, instrument_id="*", event_time_ns=at_ns, available_at_ns=at_ns,
        boundary_name=boundary_name, calendar_version="nyse-2024-v1",
    )


def _action(event_id, instrument_id, at_ns):
    return CorporateActionEvent(
        **_COMMON, event_id=event_id, instrument_id=instrument_id, event_time_ns=at_ns, available_at_ns=at_ns,
        action_type="split", ratio=Decimal(2), effective_at_ns=at_ns, known_at_ns=at_ns,
    )


# ---- Stable, deterministic order (REPLAY-001) --------------------------------

def test_replay_order_is_independent_of_input_list_order():
    events = [_trade("t1", "AAA", T0), _trade("t2", "BBB", T0), _bar("b1", "AAA", T1), _timer("tm1", SESSION_CLOSE)]
    shuffled = events.copy()
    random.Random(7).shuffle(shuffled)

    ordered_a = replay_order(events, ReplayProfile.TRADE_REPLAY_V1)
    ordered_b = replay_order(shuffled, ReplayProfile.TRADE_REPLAY_V1)

    assert [e.event_id for e in ordered_a] == [e.event_id for e in ordered_b]


def test_sort_key_prefers_source_sequence_over_stable_tiebreak():
    first = _trade("z-last-alpha", "AAA", T0, source_sequence=2)
    second = _trade("a-first-alpha", "AAA", T0, source_sequence=1)
    ordered = replay_order([first, second], ReplayProfile.TRADE_REPLAY_V1)
    assert [e.event_id for e in ordered] == ["a-first-alpha", "z-last-alpha"]


def test_sort_key_falls_back_to_stable_tiebreak_without_sequence():
    # Same instrument/time/phase, no source_sequence: event_id breaks the tie deterministically.
    first = _trade("bbb", "AAA", T0)
    second = _trade("aaa", "AAA", T0)
    ordered = replay_order([first, second], ReplayProfile.TRADE_REPLAY_V1)
    assert [e.event_id for e in ordered] == ["aaa", "bbb"]


# ---- Same-time batching -------------------------------------------------------

def test_same_time_batch_includes_every_symbol_with_an_event_at_that_instant():
    events = [_trade("t1", "AAA", T0), _trade("t2", "BBB", T0), _trade("t3", "CCC", T1)]
    ordered = replay_order(events, ReplayProfile.TRADE_REPLAY_V1)
    batch = same_time_batch(ordered, T0)
    assert {e.instrument_id for e in batch} == {"AAA", "BBB"}


# ---- Phase assignment and profile isolation ----------------------------------

def test_trade_event_is_unsupported_under_bar_v1_profile():
    with pytest.raises(UnsupportedPhaseError):
        event_phase(_trade("t1", "AAA", T0), ReplayProfile.BAR_V1)


def test_trade_event_is_market_data_apply_under_trade_replay_v1():
    assert event_phase(_trade("t1", "AAA", T0), ReplayProfile.TRADE_REPLAY_V1) == Phase.MARKET_DATA_APPLY


def test_first_bar_version_publishes_at_bar_publish_in_both_profiles():
    bar = _bar("b1", "AAA", T0, bar_revision=1)
    assert event_phase(bar, ReplayProfile.BAR_V1) == Phase.BAR_PUBLISH
    assert event_phase(bar, ReplayProfile.TRADE_REPLAY_V1) == Phase.BAR_PUBLISH


def test_bar_revision_publishes_at_bar_update_publish_only_under_trade_replay_v1():
    revision = _bar("b1", "AAA", T0, bar_revision=2)
    assert event_phase(revision, ReplayProfile.TRADE_REPLAY_V1) == Phase.BAR_UPDATE_PUBLISH
    with pytest.raises(UnsupportedPhaseError):
        event_phase(revision, ReplayProfile.BAR_V1)


# ---- Timers independent of trades; EOF/incomplete-boundary detection --------

def test_session_close_timer_is_dispatched_even_with_no_later_trade():
    events = [_trade("t1", "AAA", T0), _timer("tm1", SESSION_CLOSE)]
    ordered = replay_order(events, ReplayProfile.TRADE_REPLAY_V1)
    assert ordered[-1].event_id == "tm1"


def test_assert_stream_covers_boundary_passes_when_timer_present():
    events = [_trade("t1", "AAA", T0), _timer("tm1", SESSION_CLOSE)]
    assert_stream_covers_boundary(events, SESSION_CLOSE)  # must not raise


def test_assert_stream_covers_boundary_detects_an_incomplete_run():
    events = [_trade("t1", "AAA", T0)]  # stream ends well before the session-close boundary
    with pytest.raises(IncompleteReplayError):
        assert_stream_covers_boundary(events, SESSION_CLOSE)


# ---- ReplayStream dispatch and the late-insertion queue rule ----------------

def test_replay_stream_dispatches_in_sort_order():
    events = [_trade("t2", "AAA", T1), _trade("t1", "AAA", T0)]
    stream = ReplayStream(events, ReplayProfile.TRADE_REPLAY_V1)
    assert [e.event_id for e in stream] == ["t1", "t2"]


def test_insert_derived_rejects_an_already_dispatched_bucket():
    stream = ReplayStream([_trade("t1", "AAA", T0)], ReplayProfile.TRADE_REPLAY_V1)
    next(stream)  # dispatch t1 (available_at_ns=T0, phase MARKET_DATA_APPLY)

    with pytest.raises(LateInsertionError):
        # Same instant, same phase: this bucket has already been dispatched.
        stream.insert_derived(_trade("late-arrival", "AAA", T0))


def test_insert_derived_accepts_a_future_bucket_and_preserves_order():
    stream = ReplayStream([_trade("t1", "AAA", T0)], ReplayProfile.TRADE_REPLAY_V1)
    next(stream)  # dispatch t1

    stream.insert_derived(_bar("revision", "AAA", T1, bar_revision=2))

    assert [e.event_id for e in stream] == ["revision"]


def test_insert_derived_rejects_same_phase_same_instant_even_with_a_larger_tiebreak():
    """C07 regression: the old rule compared the FULL sort key (including source_sequence and
    the stable tiebreak), so a same-phase/same-instant derived event could slip in whenever its
    tiebreak happened to sort after what was already dispatched. Same `(availability, phase)` as
    an already-dispatched bucket must always be rejected, regardless of tiebreak ordering."""
    stream = ReplayStream([_trade("t1", "AAA", T0, source_sequence=1)], ReplayProfile.TRADE_REPLAY_V1)
    next(stream)  # dispatch t1 (available_at_ns=T0, phase MARKET_DATA_APPLY, source_sequence=1)

    # Same instant, same phase, but a strictly larger source_sequence -- under the old
    # full-tuple comparison this would have sorted AFTER t1's key and been wrongly accepted.
    with pytest.raises(LateInsertionError):
        stream.insert_derived(_trade("zzz-late", "AAA", T0, source_sequence=99))


def test_insert_derived_accepts_a_later_phase_at_the_same_instant():
    """The explicit exception the rule allows: a derived phase-37 publication produced while
    dispatching phase-35 market data at the SAME availability instant is strictly later by phase
    even though availability is unchanged."""
    stream = ReplayStream([_trade("t1", "AAA", T0)], ReplayProfile.TRADE_REPLAY_V1)
    next(stream)  # dispatch t1 at phase MARKET_DATA_APPLY (35)

    stream.insert_derived(_bar("same-instant-revision", "AAA", T0, bar_revision=2))  # phase 37, same T0

    assert [e.event_id for e in stream] == ["same-instant-revision"]


def test_corporate_action_phase_is_shared_by_both_profiles():
    action = _action("a1", "AAA", T0)
    assert event_phase(action, ReplayProfile.BAR_V1) == Phase.CORPORATE_ACTION
    assert event_phase(action, ReplayProfile.TRADE_REPLAY_V1) == Phase.CORPORATE_ACTION

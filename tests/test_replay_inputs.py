"""R3-B integration: the worked one-minute trade fixture run through the real `ReplayRunner`,
`SeriesRouter` and `ReplayView`/contexts -- not only direct builder calls
(docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md section 8).
"""
from decimal import Decimal

from rsys.data.bar_rules import FixtureBarRuleSet
from rsys.data.events import (
    EventTimeMeaning,
    QualityState,
    TimerEvent,
    TimingLabel,
    TradeCorrectionEvent,
    TradeEvent,
)
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.replay import ReplayProfile
from rsys.replay_runtime import (
    ReplayRunner,
    SeriesRouter,
    same_time_availability_points,
)

_COMMON = {
    "provider": "alpaca",
    "feed": "fixture",
    "event_time_meaning": EventTimeMeaning.PROVIDER_MARKET_TIME,
    "session_version": "nyse-2024-v1",
    "quality_state": QualityState.OK,
    "timing_label": TimingLabel.RECORDED,
}


def _trade(event_id, market_time, available_at, price, size, conditions=("ALL",)):
    return TradeEvent(
        **_COMMON, event_id=event_id, instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns(market_time), available_at_ns=parse_utc_timestamp_ns(available_at),
        price=Decimal(price), size=Decimal(size), trade_id=event_id, venue="V", conditions=conditions,
    )


def _cancel(event_id, original_trade_id, available_at):
    ns = parse_utc_timestamp_ns(available_at)
    return TradeCorrectionEvent(
        **_COMMON, event_id=event_id, instrument_id="AAA", event_time_ns=ns, available_at_ns=ns,
        original_trade_id=original_trade_id, action="cancel",
    )


def _timer(event_id, at, instrument_id="AAA"):
    return TimerEvent(
        **{**_COMMON, "event_time_meaning": EventTimeMeaning.SCHEDULED_BOUNDARY},
        event_id=event_id, instrument_id=instrument_id,
        event_time_ns=parse_utc_timestamp_ns(at), available_at_ns=parse_utc_timestamp_ns(at),
        boundary_name="session_close", calendar_version="nyse-2024-v1",
    )


def _worked_example_events():
    return [
        _trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10),
        _trade("T2", "2024-06-10T09:30:20Z", "2024-06-10T09:30:20Z", 102, 20),
        _trade("T3", "2024-06-10T09:30:30Z", "2024-06-10T09:30:30Z", 150, 5, conditions=("VOLUME_ONLY",)),
        _trade("T4", "2024-06-10T09:30:50Z", "2024-06-10T09:30:50Z", 101, 10),
        _timer("close1", "2024-06-10T09:31:00Z"),
        _trade("T5", "2024-06-10T09:30:55Z", "2024-06-10T09:31:05Z", 103, 5),
        _cancel("C2", "T2", "2024-06-10T09:31:10Z"),
        _cancel("C5", "T5", "2024-06-10T09:31:15Z"),
    ]


def _new_runner():
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    runner = ReplayRunner(_worked_example_events(), ReplayProfile.TRADE_REPLAY_V1, router)
    return runner, router


BUCKET_START = parse_utc_timestamp_ns("2024-06-10T09:30:00Z")


def _assert_forming(forming, o, h, low, c, volume, count, numerator, denominator):
    assert forming is not None
    assert (forming.open, forming.high, forming.low, forming.close) == (
        Decimal(o), Decimal(h), Decimal(low), Decimal(c),
    )
    assert forming.volume == Decimal(volume)
    assert forming.trade_count == count
    assert forming.vwap_numerator == Decimal(numerator)
    assert forming.vwap_denominator == Decimal(denominator)


def test_worked_example_forming_bar_matches_the_table_at_every_arrival():
    """Runs the exact worked table from section 8 through the real runner/router/forming_bar,
    not direct builder calls."""
    runner, router = _new_runner()

    runner.run_through(parse_utc_timestamp_ns("2024-06-10T09:30:05Z"))
    _assert_forming(router.forming_bar("AAA", BUCKET_START), 100, 100, 100, 100, 10, 1, 1000, 10)

    runner.run_through(parse_utc_timestamp_ns("2024-06-10T09:30:20Z"))
    _assert_forming(router.forming_bar("AAA", BUCKET_START), 100, 102, 100, 102, 30, 2, 3040, 30)

    runner.run_through(parse_utc_timestamp_ns("2024-06-10T09:30:30Z"))
    _assert_forming(router.forming_bar("AAA", BUCKET_START), 100, 102, 100, 102, 35, 3, 3040, 30)

    runner.run_through(parse_utc_timestamp_ns("2024-06-10T09:30:50Z"))
    _assert_forming(router.forming_bar("AAA", BUCKET_START), 100, 102, 100, 101, 45, 4, 4050, 40)

    # Scheduled close at 09:31:00: the bucket is no longer "forming".
    runner.run_through(parse_utc_timestamp_ns("2024-06-10T09:31:00Z"))
    assert router.forming_bar("AAA", BUCKET_START) is None
    closed_version = router.version_store_for("AAA").latest(("AAA", "fixture", "1Min", BUCKET_START))
    assert closed_version is not None
    assert (closed_version.open, closed_version.high, closed_version.low, closed_version.close) == (
        Decimal(100), Decimal(102), Decimal(100), Decimal(101),
    )
    assert closed_version.volume == Decimal(45)
    assert closed_version.trade_count == 4

    # T5 (market time 09:30:55, available 09:31:05) is a late report into the ALREADY-CLOSED
    # bucket -- a revision, never a fresh forming bucket.
    runner.run_through(parse_utc_timestamp_ns("2024-06-10T09:31:05Z"))
    assert router.forming_bar("AAA", BUCKET_START) is None
    revised = router.version_store_for("AAA").latest(("AAA", "fixture", "1Min", BUCKET_START))
    assert revised.revision == 1
    assert (revised.high, revised.close) == (Decimal(103), Decimal(103))
    assert revised.volume == Decimal(50)
    assert revised.trade_count == 5
    assert revised.vwap_numerator == Decimal(4565)
    assert revised.vwap_denominator == Decimal(45)

    runner.run_through(parse_utc_timestamp_ns("2024-06-10T09:31:10Z"))  # cancel T2
    after_cancel_t2 = router.version_store_for("AAA").latest(("AAA", "fixture", "1Min", BUCKET_START))
    assert (after_cancel_t2.high, after_cancel_t2.close) == (Decimal(103), Decimal(103))
    assert after_cancel_t2.volume == Decimal(30)
    assert after_cancel_t2.trade_count == 4
    assert after_cancel_t2.vwap_numerator == Decimal(2525)
    assert after_cancel_t2.vwap_denominator == Decimal(25)

    runner.run_through(parse_utc_timestamp_ns("2024-06-10T09:31:15Z"))  # cancel T5
    final = router.version_store_for("AAA").latest(("AAA", "fixture", "1Min", BUCKET_START))
    assert (final.open, final.high, final.low, final.close) == (Decimal(100), Decimal(101), Decimal(100), Decimal(101))
    assert final.volume == Decimal(25)
    assert final.trade_count == 3
    assert final.vwap_numerator == Decimal(2010)
    assert final.vwap_denominator == Decimal(20)


# ---- Causality: forming_bar must never leak a not-yet-processed future trade ----

def test_forming_bar_query_before_t2_does_not_see_t2():
    runner, router = _new_runner()
    runner.run_through(parse_utc_timestamp_ns("2024-06-10T09:30:05Z"))  # only T1 processed
    forming = router.forming_bar("AAA", BUCKET_START)
    assert forming.close == Decimal(100)
    assert forming.trade_count == 1


def test_context_after_freezes_at_the_cutoff_and_exposes_forming_bar():
    runner, _router = _new_runner()
    runner.run_through(parse_utc_timestamp_ns("2024-06-10T09:30:20Z"))
    context = runner.context_after(parse_utc_timestamp_ns("2024-06-10T09:30:20Z"), intrabar=True)
    forming = context.forming_bar("AAA")
    assert forming.close == Decimal(102)
    latest_trade = context.latest_trade("AAA")
    assert latest_trade is not None and latest_trade.event_id == "T2"


# ---- Fixture-only threshold observer: proves a decision can occur before minute close ----

def test_fixture_threshold_observer_first_crosses_at_t2_using_forming_state_only():
    """A software test of observation mechanics, not a new ORB/VWAP strategy or performance
    claim: a declared close >= 101 threshold first crosses when T2 arrives at 09:30:20."""
    runner, router = _new_runner()
    events = runner.run_to_completion()
    points = same_time_availability_points(events, ReplayProfile.TRADE_REPLAY_V1)

    threshold = Decimal(101)
    first_crossing_ns = None
    runner2, router2 = _new_runner()
    for point in points:
        runner2.run_through(point)
        forming = router2.forming_bar("AAA", BUCKET_START)
        if forming is not None and forming.close is not None and forming.close >= threshold:
            first_crossing_ns = point
            break

    assert first_crossing_ns == parse_utc_timestamp_ns("2024-06-10T09:30:20Z")
    assert router.version_store_for("AAA") is not None  # the full run still completes cleanly


def test_duplicate_trade_delivery_does_not_double_count_in_the_running_forming_bar():
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    trade = _trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10)
    router.apply_trade(trade)
    router.apply_trade(trade)  # identical redelivery must be idempotent (BAR-005)
    forming = router.forming_bar("AAA", BUCKET_START)
    assert forming.volume == Decimal(10)
    assert forming.trade_count == 1

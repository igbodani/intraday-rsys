"""R3-B integration: the worked one-minute trade fixture run through the real `ReplayRunner`,
`SeriesRouter` and `ReplayView`/contexts -- not only direct builder calls
(docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md section 8).
"""
from decimal import Decimal

import pytest

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
from rsys.replay_runtime import ReplayRunner, SeriesRouter

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
    runner = ReplayRunner(
        _worked_example_events(), ReplayProfile.TRADE_REPLAY_V1, router, authority="trade_builder",
    )
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

    t1 = parse_utc_timestamp_ns("2024-06-10T09:30:05Z")
    runner.run_through(t1)
    _assert_forming(router.forming_bar("AAA", t1), 100, 100, 100, 100, 10, 1, 1000, 10)

    t2 = parse_utc_timestamp_ns("2024-06-10T09:30:20Z")
    runner.run_through(t2)
    _assert_forming(router.forming_bar("AAA", t2), 100, 102, 100, 102, 30, 2, 3040, 30)

    t3 = parse_utc_timestamp_ns("2024-06-10T09:30:30Z")
    runner.run_through(t3)
    _assert_forming(router.forming_bar("AAA", t3), 100, 102, 100, 102, 35, 3, 3040, 30)

    t4 = parse_utc_timestamp_ns("2024-06-10T09:30:50Z")
    runner.run_through(t4)
    _assert_forming(router.forming_bar("AAA", t4), 100, 102, 100, 101, 45, 4, 4050, 40)

    # Scheduled close at 09:31:00: the bucket is no longer "forming".
    close_ns = parse_utc_timestamp_ns("2024-06-10T09:31:00Z")
    runner.run_through(close_ns)
    assert router.forming_bar("AAA", close_ns) is None
    closed_version = router.version_store_for("AAA").latest(("AAA", "fixture", "1Min", BUCKET_START))
    assert closed_version is not None
    assert (closed_version.open, closed_version.high, closed_version.low, closed_version.close) == (
        Decimal(100), Decimal(102), Decimal(100), Decimal(101),
    )
    assert closed_version.volume == Decimal(45)
    assert closed_version.trade_count == 4

    # T5 (market time 09:30:55, available 09:31:05) is a late report into the ALREADY-CLOSED
    # bucket -- a revision, never a fresh forming bucket.
    t5_available = parse_utc_timestamp_ns("2024-06-10T09:31:05Z")
    runner.run_through(t5_available)
    assert router.forming_bar("AAA", t5_available) is None
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
    t1 = parse_utc_timestamp_ns("2024-06-10T09:30:05Z")
    runner.run_through(t1)  # only T1 processed
    forming = router.forming_bar("AAA", t1)
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

class _ThresholdCrossObserver:
    """A fixture-only synthetic intrabar observer (IR-03): never an ORB/VWAP strategy or
    performance claim, just a software test of observation mechanics."""

    name = "fixture_threshold_cross_v1"
    mode = "intrabar"

    def __init__(self, threshold: Decimal):
        self.threshold = threshold

    def decide(self, context, instrument_id: str):
        forming = context.forming_bar(instrument_id)
        if forming is not None and forming.close is not None and forming.close >= self.threshold:
            return {"crossed": True, "close": forming.close}
        return None


class _CloseOnlyRecorder:
    """A closed-only observer used as a negative control: it must never be able to call
    `forming_bar` at all (ClosedOnlyContext has no such method)."""

    name = "fixture_close_only_recorder_v1"
    mode = "closed_only"

    def decide(self, context, instrument_id: str):
        assert not hasattr(context, "forming_bar")
        bars = context.closed_bars(instrument_id)
        return {"closed_count": len(bars)} if bars else None


def test_fixture_threshold_observer_first_crosses_at_t2_using_forming_state_only():
    """A software test of observation mechanics, not a new ORB/VWAP strategy or performance
    claim: a declared close >= 101 threshold first crosses when T2 arrives at 09:30:20.

    Driven entirely by `ReplayRunner.run_with_observers` (IR-03) -- not a test-only loop over
    the router: the runner itself iterates cadence points and invokes the observer.
    """
    runner, router = _new_runner()
    threshold_observer = _ThresholdCrossObserver(Decimal(101))
    records = runner.run_with_observers([threshold_observer], ["AAA"])

    crossings = [r for r in records if r.observer_name == threshold_observer.name]
    assert crossings  # at least one crossing was recorded
    first_crossing = min(crossings, key=lambda r: r.decision_key_ns)
    assert first_crossing.decision_key_ns == parse_utc_timestamp_ns("2024-06-10T09:30:20Z")
    assert first_crossing.result["close"] == Decimal(102)
    assert router.version_store_for("AAA") is not None  # the full run still completes cleanly


def test_close_only_observer_never_gets_forming_access_through_the_runner():
    """A close-only observer dispatched by the SAME runner mechanism never receives forming-bar
    observations, proving the two cadences/capabilities stay properly separated."""
    runner, _router = _new_runner()
    recorder = _CloseOnlyRecorder()
    records = runner.run_with_observers([recorder], ["AAA"])
    assert any(r.observer_name == recorder.name for r in records)


def test_duplicate_trade_delivery_does_not_double_count_in_the_running_forming_bar():
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    trade = _trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10)
    router.apply_trade(trade)
    router.apply_trade(trade)  # identical redelivery must be idempotent (BAR-005)
    forming = router.forming_bar("AAA", parse_utc_timestamp_ns("2024-06-10T09:30:05Z"))
    assert forming.volume == Decimal(10)
    assert forming.trade_count == 1


# ---- IR-02 / R3I-01: a saved context's own query answers must never move after the fact ----

def test_saved_context_survives_later_trade_correction_and_close():
    """The exact scenario from docs/R3_INTEGRATION_REPAIR_TASKS.md section 5. T1 at +5s is
    100x10; T2 at +20s is 120x10. A context saved right after T1 must answer identically
    forever, even after T2, a cancel, closure and a later minute are all applied."""
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    t1_ns = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    t2_ns = parse_utc_timestamp_ns("2024-06-10T13:30:20Z")

    router.apply_trade(_trade("T1", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10))
    k5_before = router.forming_bar("AAA", t1_ns)
    assert k5_before.close == Decimal(100)
    assert k5_before.volume == Decimal(10)
    assert k5_before.vwap_numerator == Decimal(1000)
    assert k5_before.vwap_denominator == Decimal(10)

    router.apply_trade(_trade("T2", "2024-06-10T13:30:20Z", "2024-06-10T13:30:20Z", 120, 10))
    # Re-querying the SAME frozen key K5 after T2 must be byte-identical to before.
    k5_after = router.forming_bar("AAA", t1_ns)
    assert k5_after.close == k5_before.close == Decimal(100)
    assert k5_after.volume == k5_before.volume == Decimal(10)
    assert k5_after == k5_before  # BarVersion is frozen/eq; identical query, identical object value

    k20 = router.forming_bar("AAA", t2_ns)
    assert k20.close == Decimal(120)
    assert k20.volume == Decimal(20)
    assert k20.vwap_numerator == Decimal(2200)
    assert k20.vwap_denominator == Decimal(20)

    # Process a cancel of T2, closure, and a later minute.
    cancel_ns = parse_utc_timestamp_ns("2024-06-10T13:30:25Z")
    router.apply_correction(TradeCorrectionEvent(
        **_COMMON, event_id="C2", instrument_id="AAA", event_time_ns=cancel_ns, available_at_ns=cancel_ns,
        original_trade_id="T2", action="cancel",
    ))
    router.advance_clock(parse_utc_timestamp_ns("2024-06-10T13:31:00Z"))
    router.apply_trade(_trade("T3", "2024-06-10T13:32:05Z", "2024-06-10T13:32:05Z", 999, 1))

    # K5 and K20 must STILL retain their original values and forming status as known then.
    k5_final = router.forming_bar("AAA", t1_ns)
    assert k5_final == k5_before
    k20_final = router.forming_bar("AAA", t2_ns)
    assert k20_final == k20

    # A cutoff strictly before T1 is unavailable.
    before_anything = router.forming_bar("AAA", t1_ns - 1)
    assert before_anything is None

    # The closed-only completed history now contains the builder's completed (post-cancel) bar.
    closed = router.closed_bars("AAA", parse_utc_timestamp_ns("2024-06-10T13:31:00Z"))
    assert len(closed) == 1
    assert closed[0].status == "closed_as_of"
    assert closed[0].volume == Decimal(10)  # T2 was cancelled before close

    # A later revision (none occurred here) would only be visible under a later cutoff; the
    # already-closed bucket from this cutoff onward is not a "forming" bucket at K5/K20 again.
    assert router.forming_bar("AAA", t1_ns) == k5_before
    assert router.forming_bar("AAA", t2_ns) == k20


def test_saved_context_across_two_instruments_never_crosses_series_identity():
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    aaa_ns = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    bbb_ns = parse_utc_timestamp_ns("2024-06-10T13:30:06Z")
    router.apply_trade(TradeEvent(
        **_COMMON, event_id="a1", instrument_id="AAA", event_time_ns=aaa_ns, available_at_ns=aaa_ns,
        price=Decimal(100), size=Decimal(10), trade_id="a1", venue="V",
    ))
    router.apply_trade(TradeEvent(
        **_COMMON, event_id="b1", instrument_id="BBB", event_time_ns=bbb_ns, available_at_ns=bbb_ns,
        price=Decimal(200), size=Decimal(5), trade_id="b1", venue="V",
    ))
    aaa_forming = router.forming_bar("AAA", bbb_ns)
    bbb_forming = router.forming_bar("BBB", bbb_ns)
    assert aaa_forming.close == Decimal(100)
    assert bbb_forming.close == Decimal(200)
    assert aaa_forming.instrument_id == "AAA"
    assert bbb_forming.instrument_id == "BBB"


# ---- IR-02 / R3I-02: the authoritative builder's completed history reaches closed_bars() ----

def test_trade_authority_exposes_completed_bars_to_closed_context():
    from rsys.replay import replay_order
    from rsys.views import ClosedOnlyContext, ReplayView

    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    events = [
        _trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10),
        _timer("close1", "2024-06-10T09:31:00Z"),
    ]
    runner = ReplayRunner(events, ReplayProfile.TRADE_REPLAY_V1, router)
    processed = runner.run_to_completion()

    ordered = replay_order(processed, ReplayProfile.TRADE_REPLAY_V1)
    view = ReplayView(ordered, router=router, authority="trade_builder")
    context = ClosedOnlyContext(view, parse_utc_timestamp_ns("2024-06-10T09:31:00Z"))

    bars = context.closed_bars("AAA")
    assert len(bars) == 1
    assert bars[0].status == "closed_as_of"
    assert bars[0].close == Decimal(100)
    assert bars[0].volume == Decimal(10)


# ---- IR-03 / R3I-12: a first late trade into an elapsed bucket is not currently forming ----

def test_first_late_trade_respects_elapsed_boundary():
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    # The clock advances to 09:35 with no trades at all for AAA.
    router.advance_clock(parse_utc_timestamp_ns("2024-06-10T09:35:00Z"))

    # The FIRST trade for AAA arrives late, inside an already-elapsed 09:30-09:31 bucket.
    late_ns = parse_utc_timestamp_ns("2024-06-10T09:35:01Z")
    router.apply_trade(TradeEvent(
        **_COMMON, event_id="late1", instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns("2024-06-10T09:30:05Z"), available_at_ns=late_ns,
        price=Decimal(100), size=Decimal(10), trade_id="late1", venue="V",
    ))

    bucket_start = parse_utc_timestamp_ns("2024-06-10T09:30:00Z")
    assert router.forming_bar("AAA", late_ns) is None  # never presented as currently forming
    closed = router.closed_bars("AAA", late_ns)
    assert len(closed) == 1
    assert closed[0].bar_start_ns == bucket_start
    assert closed[0].status == "closed_as_of"
    assert closed[0].revision == 0  # the first materialization of a late bucket IS its close

    # A genuine revision after that first late close increments the revision counter normally.
    router.apply_trade(TradeEvent(
        **_COMMON, event_id="late2", instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns("2024-06-10T09:30:06Z"), available_at_ns=late_ns + 1,
        price=Decimal(105), size=Decimal(1), trade_id="late2", venue="V",
    ))
    revised = router.closed_bars("AAA", late_ns + 1)[0]
    assert revised.status == "revised"
    assert revised.revision == 1


# ---- IR-06 / R3I-13: quote capability and quality are enforced at the view boundary ----

def test_disabled_quote_capability_refuses_to_serve_raw_quotes():
    from rsys.data.events import QuoteEvent
    from rsys.replay import replay_order
    from rsys.views import IntrabarContext, ReplayView

    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    quote_ns = parse_utc_timestamp_ns("2024-06-10T09:30:05Z")
    events = [QuoteEvent(
        **_COMMON, event_id="q1", instrument_id="AAA", event_time_ns=quote_ns, available_at_ns=quote_ns,
        bid_price=Decimal(99), bid_size=Decimal(1), ask_price=Decimal(101), ask_size=Decimal(1), venue="V",
    )]
    runner = ReplayRunner(events, ReplayProfile.TRADE_REPLAY_V1, router)
    processed = runner.run_to_completion()
    ordered = replay_order(processed, ReplayProfile.TRADE_REPLAY_V1)

    # Capability disabled (quotes_enabled=False, the default/safe posture).
    disabled_view = ReplayView(ordered, router=router, quotes_enabled=False)
    disabled_context = IntrabarContext(disabled_view, quote_ns)
    with pytest.raises(NotImplementedError):
        disabled_context.latest_quote("AAA")

    # Capability enabled: the quote becomes usable.
    enabled_view = ReplayView(ordered, router=router, quotes_enabled=True)
    enabled_context = IntrabarContext(enabled_view, quote_ns)
    assert enabled_context.latest_quote("AAA").event_id == "q1"


def test_invalid_quality_provider_bar_never_reaches_closed_bars():
    from rsys.data.events import ProviderBarEvent
    from rsys.views import ClosedOnlyContext, ReplayView

    bar_ns = parse_utc_timestamp_ns("2024-06-10T09:31:00Z")
    invalid_bar = ProviderBarEvent(
        **{**_COMMON, "quality_state": QualityState.INVALID}, event_id="pb1", instrument_id="AAA",
        event_time_ns=bar_ns, available_at_ns=bar_ns,
        bar_start_ns=parse_utc_timestamp_ns("2024-06-10T09:30:00Z"), bar_end_ns=bar_ns, interval="1Min",
        open=Decimal(100), high=Decimal(101), low=Decimal(99), close=Decimal(100), volume=Decimal(10),
        adjustment_mode="raw",
    )
    view = ReplayView([invalid_bar])
    context = ClosedOnlyContext(view, bar_ns)
    assert context.closed_bars("AAA") == []


# ---- IR-03 / R3I-07: a split cannot silently produce a valid-looking mixed-unit bar ----

def test_split_cannot_silently_create_a_mixed_unit_bar():
    from rsys.data.events import CorporateActionEvent

    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    # A trade BEFORE the split, inside the 09:30-09:31 bucket.
    router.apply_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10))

    split_ns = parse_utc_timestamp_ns("2024-06-10T09:30:30Z")  # strictly inside the same bucket
    action = CorporateActionEvent(
        **_COMMON, event_id="split1", instrument_id="AAA",
        event_time_ns=split_ns, available_at_ns=split_ns,
        action_type="split", ratio=Decimal(2), effective_at_ns=split_ns, known_at_ns=split_ns,
    )
    result = router.apply_action(action)
    assert result["AAA"] is not None
    assert result["AAA"].status == "invalidated"

    # The bucket is no longer forming, and never reaches closed_bars() as a normal bar.
    assert router.forming_bar("AAA", split_ns) is None
    assert router.closed_bars("AAA", split_ns) == []

    # A trade AFTER the split, in the SAME bucket, does not resurrect it as valid/forming.
    router.apply_trade(_trade("T2", "2024-06-10T09:30:40Z", "2024-06-10T09:30:40Z", 50, 5))
    assert router.forming_bar("AAA", parse_utc_timestamp_ns("2024-06-10T09:30:40Z")) is None


def test_split_exactly_on_a_bucket_boundary_is_not_a_straddle():
    from rsys.data.events import CorporateActionEvent

    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    router.apply_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10))

    boundary_ns = parse_utc_timestamp_ns("2024-06-10T09:31:00Z")  # exactly on the boundary
    action = CorporateActionEvent(
        **_COMMON, event_id="split1", instrument_id="AAA",
        event_time_ns=boundary_ns, available_at_ns=boundary_ns,
        action_type="split", ratio=Decimal(2), effective_at_ns=boundary_ns, known_at_ns=boundary_ns,
    )
    result = router.apply_action(action)
    assert result["AAA"] is None  # a boundary-exact dispatch belongs cleanly to the next bucket
    assert router.forming_bar("AAA", parse_utc_timestamp_ns("2024-06-10T09:30:50Z")) is not None

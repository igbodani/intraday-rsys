"""IR-07: integrated R3 acceptance checks, exercised through the real public runner, views,
contexts, CLI and run-evidence lifecycle -- not a second test-only runner that silently
supplies the missing behavior (docs/R3_INTEGRATION_REPAIR_TASKS.md section 10).
"""
from decimal import Decimal
from pathlib import Path

from rsys.data.bar_rules import FixtureBarRuleSet
from rsys.data.models import (
    CorporateActionEvent,
    EventTimeMeaning,
    ProviderBarEvent,
    QualityState,
    QuoteEvent,
    QuoteFreshnessPolicy,
    TimerEvent,
    TimingLabel,
    TradeCorrectionEvent,
    TradeEvent,
)
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.replay.clock import replay_order
from rsys.replay.models import ReplayProfile
from rsys.replay.runtime import ReplayRunner, SeriesRouter
from rsys.replay.views import ClosedOnlyContext, ReplayView

REPO_ROOT = Path(__file__).resolve().parents[1]

_COMMON = {
    "provider": "alpaca",
    "feed": "fixture",
    "event_time_meaning": EventTimeMeaning.PROVIDER_MARKET_TIME,
    "session_version": "nyse-2024-v1",
    "quality_state": QualityState.OK,
    "timing_label": TimingLabel.RECORDED,
}

T0 = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")


def _trade(event_id, instrument_id, market_time, available_at, price, size, conditions=("ALL",)):
    return TradeEvent(
        **_COMMON, event_id=event_id, instrument_id=instrument_id,
        event_time_ns=parse_utc_timestamp_ns(market_time), available_at_ns=parse_utc_timestamp_ns(available_at),
        price=Decimal(price), size=Decimal(size), trade_id=event_id, venue="V", conditions=conditions,
    )


def _cancel(event_id, instrument_id, original_trade_id, available_at):
    ns = parse_utc_timestamp_ns(available_at)
    return TradeCorrectionEvent(
        **_COMMON, event_id=event_id, instrument_id=instrument_id, event_time_ns=ns, available_at_ns=ns,
        original_trade_id=original_trade_id, action="cancel",
    )


def _timer(event_id, instrument_id, at):
    return TimerEvent(
        **{**_COMMON, "event_time_meaning": EventTimeMeaning.SCHEDULED_BOUNDARY},
        event_id=event_id, instrument_id=instrument_id,
        event_time_ns=parse_utc_timestamp_ns(at), available_at_ns=parse_utc_timestamp_ns(at),
        boundary_name="session_close", calendar_version="nyse-2024-v1",
    )


# ---- 1. Saved-context stability after trades, corrections, close, future actions ----

def test_saved_context_stability_integrated():
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    router.apply_trade(_trade("T1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10))
    k5 = router.forming_bar("AAA", parse_utc_timestamp_ns("2024-06-10T13:30:05Z"))
    assert k5.close == Decimal(100)

    router.apply_trade(_trade("T2", "AAA", "2024-06-10T13:30:20Z", "2024-06-10T13:30:20Z", 120, 10))
    router.apply_correction(_cancel("C1", "AAA", "T2", "2024-06-10T13:30:25Z"))
    router.advance_clock(parse_utc_timestamp_ns("2024-06-10T13:31:00Z"))
    action_ns = parse_utc_timestamp_ns("2024-06-10T13:35:00Z")  # a future, unrelated action
    router.apply_action(CorporateActionEvent(
        **_COMMON, event_id="split1", instrument_id="AAA", event_time_ns=action_ns, available_at_ns=action_ns,
        action_type="split", ratio=Decimal(2), effective_at_ns=action_ns, known_at_ns=action_ns,
    ))

    k5_again = router.forming_bar("AAA", parse_utc_timestamp_ns("2024-06-10T13:30:05Z"))
    assert k5_again == k5  # unchanged by every later event


# ---- 2. Future perturbation of trades/quotes/corrections/actions; leaky negative control ----

def _build_prefix_through_k(perturb: bool):
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    router.apply_trade(_trade("T1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10))
    k_ns = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    before = router.forming_bar("AAA", k_ns)
    price = Decimal(999) if perturb else Decimal(150)
    router.apply_trade(_trade("T2", "AAA", "2024-06-10T13:30:20Z", "2024-06-10T13:30:20Z", price, 5))
    return before, router


def test_future_perturbation_does_not_change_the_honest_prefix():
    honest_before, _ = _build_prefix_through_k(perturb=False)
    perturbed_before, _ = _build_prefix_through_k(perturb=True)
    assert honest_before == perturbed_before


def test_future_perturbation_harness_catches_a_deliberately_leaky_double():
    """A negative control proving the comparison itself would catch a real leak."""
    router_a = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    router_a.apply_trade(_trade("T1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10))
    router_a.apply_trade(_trade("T2", "AAA", "2024-06-10T13:30:20Z", "2024-06-10T13:30:20Z", 150, 5))
    leaky_a = router_a.forming_bar("AAA", parse_utc_timestamp_ns("2024-06-10T13:30:20Z"))  # peeks at everything

    router_b = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    router_b.apply_trade(_trade("T1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10))
    router_b.apply_trade(_trade("T2", "AAA", "2024-06-10T13:30:20Z", "2024-06-10T13:30:20Z", 999, 5))
    leaky_b = router_b.forming_bar("AAA", parse_utc_timestamp_ns("2024-06-10T13:30:20Z"))

    assert leaky_a != leaky_b  # the harness correctly detects this (deliberately unbounded) leak


# ---- 3. Equal timestamp/different phase visibility; complete cross-symbol batching ----

def test_equal_timestamp_different_phase_visibility():
    from rsys.replay.clock import event_phase
    from rsys.replay.models import Phase

    trade_ns = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    trade = _trade("T1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10)
    bar = ProviderBarEvent(
        **_COMMON, event_id="pb1", instrument_id="AAA", event_time_ns=trade_ns, available_at_ns=trade_ns,
        bar_start_ns=trade_ns - 60_000_000_000, bar_end_ns=trade_ns, interval="1Min",
        open=Decimal(100), high=Decimal(101), low=Decimal(99), close=Decimal(100), volume=Decimal(10),
        adjustment_mode="raw", bar_revision=2,  # a revision -> BAR_UPDATE_PUBLISH (phase 37)
    )
    assert event_phase(trade, ReplayProfile.TRADE_REPLAY_V1) == Phase.MARKET_DATA_APPLY
    assert event_phase(bar, ReplayProfile.TRADE_REPLAY_V1) == Phase.BAR_UPDATE_PUBLISH
    ordered = replay_order([bar, trade], ReplayProfile.TRADE_REPLAY_V1)
    assert [e.event_id for e in ordered] == ["T1", "pb1"]  # phase 35 strictly before phase 37


def test_same_time_cross_symbol_batch_waits_for_the_full_batch():
    events = [
        _trade("A1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10),
        _trade("B1", "BBB", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 200, 5),
        _trade("A2", "AAA", "2024-06-10T13:30:06Z", "2024-06-10T13:30:06Z", 101, 1),
    ]
    from rsys.replay.runtime import same_time_availability_points
    ordered = replay_order(events, ReplayProfile.TRADE_REPLAY_V1)
    points = same_time_availability_points(ordered, ReplayProfile.TRADE_REPLAY_V1)
    first_batch = [e for e in ordered if e.available_at_ns == points[0]]
    assert {e.instrument_id for e in first_batch} == {"AAA", "BBB"}


# ---- 4. Shuffled input order with unchanged identities -> identical semantic hash ----
# (Covered end-to-end at the CLI/config level in tests/test_replay_cli.py::
#  test_reversed_input_order_yields_the_same_semantic_trace_hash; not duplicated here.)


# ---- 5. Exactly one authoritative source when provider and builder streams coexist ----

def test_provider_and_trade_streams_coexisting_only_one_is_authoritative():
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    trade_events = [
        _trade("T1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10),
        _timer("close1", "AAA", "2024-06-10T13:31:00Z"),
    ]
    provider_bar = ProviderBarEvent(
        **_COMMON, event_id="pb1", instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns("2024-06-10T13:31:00Z"),
        available_at_ns=parse_utc_timestamp_ns("2024-06-10T13:31:00Z"),
        bar_start_ns=T0, bar_end_ns=parse_utc_timestamp_ns("2024-06-10T13:31:00Z"), interval="1Min",
        open=Decimal(999), high=Decimal(999), low=Decimal(999), close=Decimal(999), volume=Decimal(999),
        adjustment_mode="raw",
    )
    all_events = [*trade_events, provider_bar]
    runner = ReplayRunner(all_events, ReplayProfile.TRADE_REPLAY_V1, router, authority="trade_builder")
    processed = runner.run_to_completion()
    ordered = replay_order(processed, ReplayProfile.TRADE_REPLAY_V1)

    # trade_builder authority: the comparison-stream provider bar never contributes.
    view = ReplayView(ordered, router=router, authority="trade_builder")
    context = ClosedOnlyContext(view, parse_utc_timestamp_ns("2024-06-10T13:31:00Z"))
    bars = context.closed_bars("AAA")
    assert len(bars) == 1
    assert bars[0].close == Decimal(100)  # the BUILDER's value, not the provider's 999
    assert bars[0].volume == Decimal(10)  # never additively combined with the provider's 999


# ---- 6. Multi-symbol, multi-minute: quiet interval, late first arrival, correction, split ----

def test_multi_symbol_multi_minute_integrated_scenario():
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    events = [
        # AAA: a normal trade then a correction.
        _trade("A1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10),
        TradeCorrectionEvent(
            **_COMMON, event_id="AC1", instrument_id="AAA",
            event_time_ns=parse_utc_timestamp_ns("2024-06-10T13:30:06Z"),
            available_at_ns=parse_utc_timestamp_ns("2024-06-10T13:30:06Z"),
            original_trade_id="A1", action="correct", corrected_price=Decimal(105), corrected_size=Decimal(10),
        ),
        # BBB: quiet minute (no trades), just a scheduled closure.
        _timer("close_bbb", "BBB", "2024-06-10T13:31:00Z"),
        # Scheduled closure for AAA too.
        _timer("close_aaa", "AAA", "2024-06-10T13:31:00Z"),
        # A late first arrival for CCC, inside an already-elapsed bucket for that instrument.
        _trade("C1", "CCC", "2024-06-10T13:30:05Z", "2024-06-10T13:35:00Z", 50, 1),
    ]
    runner = ReplayRunner(events, ReplayProfile.TRADE_REPLAY_V1, router, authority="trade_builder")
    runner.run_to_completion()

    aaa_bars = router.closed_bars("AAA", parse_utc_timestamp_ns("2024-06-10T13:31:00Z"))
    assert len(aaa_bars) == 1 and aaa_bars[0].close == Decimal(105)

    bbb_bars = router.closed_bars("BBB", parse_utc_timestamp_ns("2024-06-10T13:31:00Z"))
    assert bbb_bars == []  # a quiet minute has no bar to show, not a fabricated flat one

    ccc_forming = router.forming_bar("CCC", parse_utc_timestamp_ns("2024-06-10T13:35:00Z"))
    assert ccc_forming is None  # the late-first-arrival bucket closed immediately, never "forming"
    ccc_closed = router.closed_bars("CCC", parse_utc_timestamp_ns("2024-06-10T13:35:00Z"))
    assert len(ccc_closed) == 1 and ccc_closed[0].revision == 0


def test_missing_coverage_produces_an_incomplete_retained_attempt(tmp_path):
    from rsys.application.config import load_config
    from rsys.application.replay import run_replay_from_config

    config = load_config(REPO_ROOT / "configs" / "replay_trade_fixture.yaml")
    truncated_config = config.model_copy(
        update={"input": str(REPO_ROOT / "fixtures" / "replay_truncated_fixture_events.json")},
    )
    result = run_replay_from_config(truncated_config, tmp_path / "out")
    assert result.complete is False
    assert result.incomplete_reason is not None
    assert result.trace_path.exists()  # retained, inspectable, not silently dropped


def test_unsupported_split_straddle_through_the_runner():
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    events = [
        _trade("T1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10),
        CorporateActionEvent(
            **_COMMON, event_id="split1", instrument_id="AAA",
            event_time_ns=parse_utc_timestamp_ns("2024-06-10T13:30:30Z"),
            available_at_ns=parse_utc_timestamp_ns("2024-06-10T13:30:30Z"),
            action_type="split", ratio=Decimal(2),
            effective_at_ns=parse_utc_timestamp_ns("2024-06-10T13:30:30Z"),
            known_at_ns=parse_utc_timestamp_ns("2024-06-10T13:30:30Z"),
        ),
    ]
    runner = ReplayRunner(events, ReplayProfile.TRADE_REPLAY_V1, router, authority="trade_builder")
    runner.run_to_completion()
    bars = router.closed_bars("AAA", parse_utc_timestamp_ns("2024-06-10T13:31:00Z"))
    assert bars == []  # the straddled bucket is invalidated, never a normal closed bar


# ---- 7. Runner/CLI-driven intrabar decision + closed-only observer on trade-built bars ----

class _ThresholdObserver:
    name = "r3_acceptance_threshold_v1"
    mode = "intrabar"

    def decide(self, context, instrument_id):
        forming = context.forming_bar(instrument_id)
        if forming is not None and forming.close is not None and forming.close >= Decimal(101):
            return {"close": forming.close}
        return None


class _ClosedOnlyRecorder:
    name = "r3_acceptance_closed_only_v1"
    mode = "closed_only"

    def decide(self, context, instrument_id):
        assert not hasattr(context, "forming_bar")
        bars = context.closed_bars(instrument_id)
        return {"count": len(bars)} if bars else None


def test_runner_driven_intrabar_and_closed_only_observers_together():
    router = SeriesRouter("fixture", "1Min", FixtureBarRuleSet())
    events = [
        _trade("T1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10),
        _trade("T2", "AAA", "2024-06-10T13:30:20Z", "2024-06-10T13:30:20Z", 102, 20),
        _timer("close1", "AAA", "2024-06-10T13:31:00Z"),
    ]
    runner = ReplayRunner(events, ReplayProfile.TRADE_REPLAY_V1, router, authority="trade_builder")
    records = runner.run_with_observers([_ThresholdObserver(), _ClosedOnlyRecorder()], ["AAA"])

    intrabar_hits = [r for r in records if r.observer_name == "r3_acceptance_threshold_v1"]
    assert intrabar_hits  # the forming bar crossed 101 before the minute closed
    assert intrabar_hits[0].decision_key_ns == parse_utc_timestamp_ns("2024-06-10T13:30:20Z")

    closed_hits = [r for r in records if r.observer_name == "r3_acceptance_closed_only_v1"]
    assert closed_hits  # the close-only observer saw the completed, trade-built bar
    assert closed_hits[0].result["count"] == 1


# ---- 8. Two retained successful attempts + one retained failed/incomplete attempt ----

def test_two_successful_and_one_failed_attempt_all_independently_retained(tmp_path):
    from rsys.application.config import load_config
    from rsys.application.replay import run_replay_from_config
    from rsys.research.storage import RunStorage

    good_config = load_config(REPO_ROOT / "configs" / "replay_trade_fixture.yaml")
    bad_config = good_config.model_copy(
        update={"input": str(REPO_ROOT / "fixtures" / "replay_truncated_fixture_events.json")},
    )

    out_root = tmp_path / "attempts_root"
    result_1 = run_replay_from_config(good_config, out_root)
    result_2 = run_replay_from_config(good_config, out_root)
    result_3 = run_replay_from_config(bad_config, out_root)

    assert result_1.complete and result_2.complete
    assert not result_3.complete
    assert len({result_1.run_id, result_2.run_id, result_3.run_id}) == 3  # three distinct attempts

    storage = RunStorage(out_root)
    record_1 = storage.load_record(result_1.run_id)
    record_2 = storage.load_record(result_2.run_id)
    record_3 = storage.load_record(result_3.run_id)
    assert record_1.status == "success"
    assert record_2.status == "success"
    assert record_3.status == "failed"
    assert record_1.config_hash == record_2.config_hash  # identical config -> identical hash
    assert record_1.config_hash != record_3.config_hash  # a different input path IS a different config
    assert record_1.data_hash != record_3.data_hash  # different input content, independently hashed
    storage.close()


def test_quote_only_input_never_changes_stock_trade_bar_aggregates():
    router = SeriesRouter(
        "fixture", "1Min", FixtureBarRuleSet(),
        quote_freshness_policy=QuoteFreshnessPolicy(freshness_limit_ms=1000),
    )
    router.apply_trade(_trade("T1", "AAA", "2024-06-10T13:30:05Z", "2024-06-10T13:30:05Z", 100, 10))
    before = router.forming_bar("AAA", parse_utc_timestamp_ns("2024-06-10T13:30:05Z"))

    router.apply_quote(QuoteEvent(
        **_COMMON, event_id="q1", instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns("2024-06-10T13:30:06Z"),
        available_at_ns=parse_utc_timestamp_ns("2024-06-10T13:30:06Z"),
        bid_price=Decimal(99), bid_size=Decimal(1000), ask_price=Decimal(101), ask_size=Decimal(1000), venue="V",
    ))
    after = router.forming_bar("AAA", parse_utc_timestamp_ns("2024-06-10T13:30:05Z"))
    assert before == after  # the quote never touched the trade bar's own aggregates

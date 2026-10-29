"""Regressions from the independent 381-test review; use public runtime paths."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from rsys.config import ConfigError, load_raw, parse_config
from rsys.data.bar_rules import FixtureBarRuleSet
from rsys.data.events import CorporateActionEvent, QuoteEvent, TimerEvent, TradeEvent
from rsys.data.quotes import QuoteFreshnessPolicy
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.replay import ReplayProfile
from rsys.replay_runtime import ReplayRunner, SeriesRouter

ROOT = Path(__file__).resolve().parents[1]
N = 1_000_000_000
T = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")
COMMON = {
    "provider": "alpaca",
    "feed": "fixture",
    "instrument_id": "AAA",
    "event_time_meaning": "provider_market_time",
    "session_version": "nyse-2024-v1",
    "quality_state": "ok",
    "timing_label": "recorded",
}


def trade(name, at, available, price=100, instrument="AAA"):
    return TradeEvent(
        **{**COMMON, "instrument_id": instrument},
        event_id=name,
        event_time_ns=T + at * N,
        available_at_ns=T + available * N,
        received_at_ns=T + available * N,
        price=Decimal(price),
        size=Decimal(10),
        trade_id=name,
        venue="V",
        conditions=("ALL",),
    )


def timer(at, available=None):
    available = at if available is None else available
    return TimerEvent(
        **{**COMMON, "event_time_meaning": "scheduled_boundary"},
        event_id=f"timer{at}",
        event_time_ns=T + at * N,
        available_at_ns=T + available * N,
        boundary_name="window_end",
        calendar_version="nyse-2024-v1",
    )


def runner(events, *, quotes=False, **kwargs):
    router = SeriesRouter(
        "fixture",
        "1Min",
        FixtureBarRuleSet(),
        quote_freshness_policy=QuoteFreshnessPolicy(freshness_limit_ms=1000)
        if quotes
        else None,
    )
    return ReplayRunner(
        events,
        ReplayProfile.TRADE_REPLAY_V1,
        router,
        authority="trade_builder",
        quotes_enabled=quotes,
        **kwargs,
    ), router


def test_delayed_timer_cannot_backdate_a_late_trade_into_saved_context():
    replay, _ = runner([trade("a", 5, 5), trade("late", 20, 70, 120), timer(60, 80)])
    replay.run_through(T + 65 * N)
    saved = replay.context_after(T + 65 * N, intrabar=True)
    before = saved.closed_bars("AAA")
    replay.run_to_completion()
    assert saved.closed_bars("AAA") == before
    historical = replay.context_after(T + 65 * N, intrabar=True)
    assert historical.closed_bars("AAA") == before
    assert all(b.close != Decimal(120) for b in historical.closed_bars("AAA"))


def test_delayed_action_cannot_rewrite_a_saved_forming_context():
    action = CorporateActionEvent(
        **COMMON,
        event_id="split",
        event_time_ns=T + 30 * N,
        available_at_ns=T + 50 * N,
        received_at_ns=T + 50 * N,
        action_type="split",
        ratio=Decimal(2),
        effective_at_ns=T + 30 * N,
        known_at_ns=T + 30 * N,
    )
    replay, _ = runner([trade("a", 5, 5), action])
    replay.run_through(T + 40 * N)
    saved = replay.context_after(T + 40 * N, intrabar=True)
    before = saved.forming_bar("AAA")
    replay.run_to_completion()
    assert saved.forming_bar("AAA") == before
    assert replay.context_after(T + 40 * N, intrabar=True).forming_bar("AAA") == before
    assert replay.context_after(T + 50 * N, intrabar=True).forming_bar("AAA") is None


def test_elapsed_minute_closes_without_a_timer_in_the_input():
    replay, _ = runner([trade("a", 5, 5), trade("b", 65, 65, 101)])
    replay.run_through(T + 65 * N)
    bars = replay.context_after(T + 65 * N, intrabar=False).closed_bars("AAA")
    assert len(bars) == 1
    assert bars[0].close == Decimal(100)
    assert bars[0].available_at_ns == T + 60 * N


class ClosedObserver:
    name = "closed_probe"
    mode = "closed_only"

    def decide(self, context, instrument_id):
        return tuple(str(b.close) for b in context.closed_bars(instrument_id))


def test_closed_observer_is_not_called_again_for_a_later_trade():
    replay, _ = runner([trade("a", 5, 5), timer(60), trade("b", 65, 65), timer(120)])
    decisions = replay.run_with_observers([ClosedObserver()], ["AAA"])
    assert [d.decision_key_ns for d in decisions] == [T + 60 * N, T + 120 * N]


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("interval_policy", "interval", "nonsense"),
        ("bars", "rule_set_id", "not_registered:v999"),
        ("replay", "ordering_profile", "bar_v1"),
        ("interval_policy", "unit_policy", "splits_effective_and_known_asof"),
        ("coverage", "required_boundary_at", "not-a-date"),
    ],
)
def test_unsupported_replay_configuration_fails_early(section, field, value):
    config = deepcopy(load_raw(ROOT / "configs/replay_trade_fixture.yaml"))
    config[section][field] = value
    with pytest.raises(ConfigError):
        parse_config(config)


def test_strategy_quote_access_enforces_declared_freshness():
    quote = QuoteEvent(
        **COMMON,
        event_id="q",
        event_time_ns=T + 5 * N,
        available_at_ns=T + 5 * N,
        received_at_ns=T + 5 * N,
        bid_price=Decimal(99),
        ask_price=Decimal(101),
        bid_size=Decimal(1),
        ask_size=Decimal(1),
        venue="V",
    )
    replay, _ = runner([quote, timer(60)], quotes=True)
    replay.run_through(T + 5 * N)
    saved = replay.context_after(T + 5 * N, intrabar=True)
    assert saved.latest_quote("AAA") == quote
    replay.run_to_completion()
    assert replay.context_after(T + 60 * N, intrabar=True).latest_quote("AAA") is None
    assert saved.latest_quote("AAA") == quote


def test_phase_cutoffs_and_same_timestamp_context_are_frozen():
    from rsys.data.keys import ReplayKey

    replay, _ = runner([trade("a", 5, 5), trade("b", 5, 5, 120)])
    replay.run_through_key(
        ReplayKey(T + 5 * N, 35, -1, ("alpaca", "fixture", "AAA", "trade", "a"))
    )
    saved = replay.context_after(ReplayKey(T + 5 * N, 37, 0), intrabar=True)
    assert saved.forming_bar("AAA").close == Decimal(100)
    phase35 = replay.context_after(ReplayKey(T + 5 * N, 35, 2**63 - 1), intrabar=True)
    assert phase35.forming_bar("AAA") is None
    replay.run_to_completion()
    assert saved.forming_bar("AAA").close == Decimal(100)
    assert replay.context_after(T + 5 * N, intrabar=True).forming_bar(
        "AAA"
    ).close == Decimal(120)


def test_duplicate_delivery_does_not_retrigger_a_closed_observer():
    original = trade("a", 5, 5)
    duplicate = original.model_copy(
        update={"available_at_ns": T + 65 * N, "received_at_ns": T + 65 * N}
    )
    replay, _ = runner([original, timer(60), duplicate])
    records = replay.run_with_observers([ClosedObserver()], ["AAA"])
    assert [r.decision_key_ns for r in records] == [T + 60 * N]


def test_first_late_trade_after_action_cannot_create_a_valid_mixed_unit_bar():
    action = CorporateActionEvent(
        **COMMON,
        event_id="split",
        event_time_ns=T + 30 * N,
        available_at_ns=T + 30 * N,
        received_at_ns=T + 30 * N,
        action_type="split",
        ratio=Decimal(2),
        effective_at_ns=T + 30 * N,
        known_at_ns=T + 30 * N,
    )
    replay, router = runner(
        [action, trade("late", 5, 40), trade("post", 45, 45, 50), timer(60)]
    )
    replay.run_to_completion()
    assert replay.context_after(T + 60 * N, intrabar=False).closed_bars("AAA") == []
    assert any(v.status == "invalidated" for v in router.frozen_versions())


def test_late_first_arrival_without_prior_instrument_timer_is_closed():
    replay, _ = runner([trade("late", 5, 75)])
    replay.run_to_completion()
    context = replay.context_after(T + 75 * N, intrabar=True)
    assert context.forming_bar("AAA") is None
    bars = context.closed_bars("AAA")
    assert len(bars) == 1 and bars[0].available_at_ns == T + 75 * N


class RecordingObserver:
    name = "causality_probe"
    mode = "intrabar"

    def __init__(self):
        self.saved = []

    @staticmethod
    def inspect(context, instrument):
        forming = context.forming_bar(instrument)
        bars = context.closed_bars(instrument)
        quote = context.quote_observation(instrument)
        return (
            str(forming.close) if forming else None,
            tuple((str(b.close), b.publication_key) for b in bars),
            quote.state,
            str(quote.event.bid_price) if quote.event else None,
        )

    def decide(self, context, instrument):
        value = self.inspect(context, instrument)
        self.saved.append((context, instrument, value))
        return value


def _future_run(price, *, reverse=False, leaky=False):
    from rsys.data.events import TradeCorrectionEvent

    quote = QuoteEvent(
        **COMMON,
        event_id="q1",
        event_time_ns=T + 5 * N,
        available_at_ns=T + 5 * N,
        received_at_ns=T + 5 * N,
        bid_price=Decimal(99),
        ask_price=Decimal(101),
        venue="V",
    )
    future_quote = quote.model_copy(
        update={
            "event_id": "q2",
            "event_time_ns": T + 80 * N,
            "available_at_ns": T + 80 * N,
            "received_at_ns": T + 80 * N,
            "bid_price": Decimal(price),
            "ask_price": Decimal(price + 2),
        }
    )
    correction = TradeCorrectionEvent(
        **COMMON,
        event_id="correct",
        event_time_ns=T + 90 * N,
        available_at_ns=T + 90 * N,
        received_at_ns=T + 90 * N,
        original_trade_id="a",
        action="correct",
        corrected_price=Decimal(price),
        corrected_size=Decimal(10),
    )
    action = CorporateActionEvent(
        **COMMON,
        event_id="split",
        event_time_ns=T + 30 * N,
        available_at_ns=T + 100 * N,
        received_at_ns=T + 100 * N,
        action_type="split",
        ratio=Decimal(2),
        effective_at_ns=T + 30 * N,
        known_at_ns=T + 30 * N,
    )
    events = [
        trade("a", 5, 5),
        trade("b", 5, 5, 200, "BBB"),
        quote,
        trade("late", 20, 70, price),
        future_quote,
        correction,
        action,
        timer(120),
    ]
    replay, router = runner(list(reversed(events)) if reverse else events, quotes=True)
    if leaky:
        # Negative control uses the same public observer/harness but deliberately gives
        # it a context from a fully processed future. The comparison MUST fail.
        future, _ = runner(events, quotes=True)
        future.run_to_completion()
        future_context = future.context_after(T + 120 * N, intrabar=True)
        replay.context_after = lambda *_args, **_kwargs: future_context
    observer = RecordingObserver()
    decisions = replay.run_with_observers([observer], ["AAA", "BBB"])
    prefix = [r for r in decisions if r.decision_key_ns <= T + 65 * N]
    return prefix, observer, replay, router


def test_future_perturbations_preserve_full_decision_prefix_and_saved_contexts():
    baseline, observer, _, _ = _future_run(120)
    perturbed, observer2, _, _ = _future_run(999, reverse=True)
    assert baseline == perturbed
    for o in (observer, observer2):
        for context, instrument, original in o.saved:
            assert o.inspect(context, instrument) == original


def test_same_prefix_harness_detects_a_deliberately_leaky_runner():
    baseline, _, _, _ = _future_run(120, leaky=True)
    perturbed, _, _, _ = _future_run(999, leaky=True)
    with pytest.raises(AssertionError):
        assert baseline == perturbed


def test_cross_symbol_batch_observer_sees_both_symbols_before_deciding():
    class CrossSymbol:
        name, mode = "cross_symbol", "intrabar"

        def decide(self, context, instrument_id):
            return tuple(context.latest_trade(i).price for i in ("AAA", "BBB"))

    events = [trade("a", 5, 5), trade("b", 5, 5, 200, "BBB")]
    first, _ = runner(events)
    second, _ = runner(list(reversed(events)))
    a = first.run_with_observers([CrossSymbol()], ["AAA", "BBB"])
    b = second.run_with_observers([CrossSymbol()], ["BBB", "AAA"])
    assert a == b
    assert all(r.result == (Decimal(100), Decimal(200)) for r in a)


def test_bad_quote_does_not_resurrect_an_older_good_quote():
    good = QuoteEvent(
        **COMMON,
        event_id="q1",
        event_time_ns=T + 5 * N,
        available_at_ns=T + 5 * N,
        bid_price=Decimal(99),
        ask_price=Decimal(101),
        venue="V",
    )
    invalid = good.model_copy(
        update={
            "event_id": "q2",
            "event_time_ns": T + 6 * N,
            "available_at_ns": T + 6 * N,
            "quality_state": "invalid",
        }
    )
    replay, _ = runner([good, invalid], quotes=True)
    replay.run_to_completion()
    context = replay.context_after(T + 6 * N, intrabar=True)
    assert context.latest_quote("AAA") is None
    assert context.quote_observation("AAA").state == "invalid"


def test_provider_redelivery_does_not_retrigger_completed_bar_observer():
    from rsys.replay_io import load_events_from_json

    events = load_events_from_json(ROOT / "fixtures/replay_bar_fixture_events.json")
    original = events[0]
    repeated = original.model_copy(
        update={
            "event_id": "redelivery",
            "available_at_ns": original.available_at_ns + N,
            "received_at_ns": original.available_at_ns + N,
        }
    )
    replay = ReplayRunner([original, repeated], ReplayProfile.BAR_V1)
    assert len(replay.run_with_observers([ClosedObserver()], ["AAA"])) == 1


def test_conflicting_provider_revision_fails_before_acceptance():
    from rsys.replay_io import load_events_from_json

    original = load_events_from_json(ROOT / "fixtures/replay_bar_fixture_events.json")[
        0
    ]
    conflict = original.model_copy(
        update={
            "event_id": "conflict",
            "high": Decimal(999),
            "available_at_ns": original.available_at_ns + N,
            "received_at_ns": original.available_at_ns + N,
        }
    )
    replay = ReplayRunner([original, conflict], ReplayProfile.BAR_V1)
    with pytest.raises(ValueError, match="conflicting provider publication"):
        replay.run_to_completion()
    assert replay.processed == [original]

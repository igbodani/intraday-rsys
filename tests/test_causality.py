from decimal import Decimal

import pytest

from rsys.data.events import (
    EventTimeMeaning,
    ProviderBarEvent,
    QualityState,
    TimingLabel,
    TradeEvent,
)
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.replay import ReplayProfile, replay_order
from rsys.views import ClosedOnlyContext, IntrabarContext, ReplayView

T0 = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")
T1 = parse_utc_timestamp_ns("2024-06-10T13:31:00Z")
T2 = parse_utc_timestamp_ns("2024-06-10T13:32:00Z")
K = T1  # the future-perturbation cutoff

_COMMON = {
    "provider": "alpaca",
    "feed": "fixture",
    "event_time_meaning": EventTimeMeaning.PROVIDER_MARKET_TIME,
    "session_version": "nyse-2024-v1",
    "quality_state": QualityState.OK,
    "timing_label": TimingLabel.RECORDED,
}


def _bar(event_id, instrument_id, at_ns, close):
    return ProviderBarEvent(
        **_COMMON, event_id=event_id, instrument_id=instrument_id, event_time_ns=at_ns, available_at_ns=at_ns,
        bar_start_ns=at_ns - 60_000_000_000, bar_end_ns=at_ns, interval="1Min",
        open=Decimal(100), high=Decimal(101), low=Decimal(99), close=close, volume=Decimal(10),
        adjustment_mode="raw",
    )


def _trade(event_id, instrument_id, at_ns, price):
    return TradeEvent(
        **_COMMON, event_id=event_id, instrument_id=instrument_id, event_time_ns=at_ns, available_at_ns=at_ns,
        price=price, size=Decimal(10), trade_id=event_id, venue="V",
    )


def _baseline_stream():
    return [
        _bar("b0", "AAA", T0, Decimal(100)),
        _bar("b1", "AAA", T1, Decimal(101)),  # at/through cutoff K
        _bar("b2", "AAA", T2, Decimal(102)),  # strictly after K
    ]


def _perturbed_future_stream():
    """Identical prefix through K; only the strictly-after-K bar differs."""
    return [
        _bar("b0", "AAA", T0, Decimal(100)),
        _bar("b1", "AAA", T1, Decimal(101)),
        _bar("b2", "AAA", T2, Decimal(999)),  # perturbed, but strictly after K
    ]


def _honest_closed_bars_decision(stream: list, as_of_ns: int) -> list[str]:
    """A correct strategy view: only bars available at/before `as_of_ns`."""
    ordered = replay_order(stream, ReplayProfile.BAR_V1)
    view = ReplayView(ordered)
    context = ClosedOnlyContext(view, as_of_ns)
    return [str(bar.close) for bar in context.closed_bars("AAA")]


def _leaky_decision_double(stream: list, as_of_ns: int) -> list[str]:
    """Deliberately leaky test double: ignores availability and reads every bar in the stream,
    including ones strictly after the cutoff. Must never be shipped as a real strategy --
    this exists solely to prove the harness below actually catches a leak.
    """
    return [str(bar.close) for bar in stream if bar.instrument_id == "AAA"]


# ---- REPLAY-002: perturbing unavailable future data cannot change earlier outputs ----

def test_future_perturbation_does_not_change_the_honest_decision_through_cutoff():
    original = _honest_closed_bars_decision(_baseline_stream(), K)
    perturbed = _honest_closed_bars_decision(_perturbed_future_stream(), K)
    assert original == perturbed == ["100", "101"]


def test_future_perturbation_harness_catches_a_deliberately_leaky_double():
    """The harness itself must fail the comparison for a double that peeks at future data --
    proving it would catch the same mistake in real strategy code."""
    original = _leaky_decision_double(_baseline_stream(), K)
    perturbed = _leaky_decision_double(_perturbed_future_stream(), K)
    assert original != perturbed  # the leak is visible: output through K changed


# ---- REPLAY-003: close-only vs. opted-in intrabar context -------------------

def test_closed_only_context_has_no_forming_or_trade_access():
    view = ReplayView(replay_order(_baseline_stream(), ReplayProfile.BAR_V1))
    context = ClosedOnlyContext(view, K)
    assert not hasattr(context, "forming_bar")
    assert not hasattr(context, "latest_trade")
    assert not hasattr(context, "latest_quote")


def test_intrabar_context_still_has_closed_bars_and_a_bounded_latest_trade():
    events = [*_baseline_stream(), _trade("t0", "AAA", T0, Decimal(100)), _trade("t2", "AAA", T2, Decimal(999))]
    view = ReplayView(replay_order(events, ReplayProfile.TRADE_REPLAY_V1))
    context = IntrabarContext(view, K)

    assert [str(bar.close) for bar in context.closed_bars("AAA")] == ["100", "101"]
    latest = context.latest_trade("AAA")
    assert latest is not None and latest.event_id == "t0"  # t2 is strictly after K; never exposed


def test_intrabar_context_forming_bar_is_explicitly_not_yet_implemented():
    view = ReplayView(replay_order(_baseline_stream(), ReplayProfile.BAR_V1))
    context = IntrabarContext(view, K)
    with pytest.raises(NotImplementedError):
        context.forming_bar("AAA")


# ---- Mutation resistance: snapshot views never expose live mutable state ----

def test_closed_bars_result_is_a_fresh_list_each_call():
    view = ReplayView(replay_order(_baseline_stream(), ReplayProfile.BAR_V1))
    first = view.closed_bars("AAA", K)
    first.append("tampered")
    second = view.closed_bars("AAA", K)
    assert "tampered" not in second
    assert len(second) == 2


def test_market_event_is_frozen_against_in_place_mutation():
    bar = _bar("b0", "AAA", T0, Decimal(100))
    with pytest.raises(ValueError):
        bar.close = Decimal(999)

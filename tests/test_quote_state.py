from decimal import Decimal

import pytest

from rsys.data.errors import MismatchedQuoteSeriesError
from rsys.data.models import (
    EventTimeMeaning,
    FreshnessState,
    QualityState,
    QuoteCondition,
    QuoteEvent,
    QuoteFreshnessPolicy,
    TimingLabel,
    classify_quote_condition,
)
from rsys.data.quotes import QuoteState
from rsys.data.timestamps import parse_utc_timestamp_ns

_COMMON = {
    "provider": "alpaca",
    "feed": "fixture",
    "event_time_meaning": EventTimeMeaning.PROVIDER_MARKET_TIME,
    "session_version": "nyse-2024-v1",
    "quality_state": QualityState.OK,
    "timing_label": TimingLabel.RECORDED,
}


def _quote(event_id, at, bid_price=None, bid_size=None, ask_price=None, ask_size=None, instrument_id="AAA"):
    ns = parse_utc_timestamp_ns(at)
    return QuoteEvent(
        **_COMMON, event_id=event_id, instrument_id=instrument_id, event_time_ns=ns, available_at_ns=ns,
        bid_price=bid_price, bid_size=bid_size, ask_price=ask_price, ask_size=ask_size, venue="V",
    )


# ---- Condition classification -------------------------------------------------

def test_normal_condition_when_bid_below_ask():
    assert classify_quote_condition(Decimal(99), Decimal(101)) == QuoteCondition.NORMAL


def test_locked_condition_when_bid_equals_ask():
    assert classify_quote_condition(Decimal(100), Decimal(100)) == QuoteCondition.LOCKED


def test_crossed_condition_when_bid_above_ask():
    assert classify_quote_condition(Decimal(101), Decimal(99)) == QuoteCondition.CROSSED


def test_missing_bid_condition():
    assert classify_quote_condition(None, Decimal(101)) == QuoteCondition.MISSING_BID


def test_missing_ask_condition():
    assert classify_quote_condition(Decimal(99), None) == QuoteCondition.MISSING_ASK


def test_missing_both_condition():
    assert classify_quote_condition(None, None) == QuoteCondition.MISSING_BOTH


# ---- QuoteState: series identity and snapshot construction --------------------

def test_quote_state_rejects_a_mismatched_series():
    state = QuoteState("AAA", "fixture", QuoteFreshnessPolicy(freshness_limit_ms=500))
    wrong = _quote("q1", "2024-06-10T09:30:00Z", Decimal(99), Decimal(1), Decimal(101), Decimal(1), instrument_id="BBB")
    with pytest.raises(MismatchedQuoteSeriesError):
        state.apply(wrong)
    assert state.latest() is None  # rejected before any mutation


def test_quote_state_apply_produces_a_frozen_snapshot_with_condition():
    state = QuoteState("AAA", "fixture", QuoteFreshnessPolicy(freshness_limit_ms=500))
    event = _quote("q1", "2024-06-10T09:30:00Z", Decimal(99), Decimal(1), Decimal(101), Decimal(1))
    snapshot = state.apply(event)
    assert snapshot.condition == QuoteCondition.NORMAL
    assert snapshot.source_event_id == "q1"
    with pytest.raises(ValueError, match="frozen|immutable"):
        snapshot.bid_price = Decimal(1)  # type: ignore[misc]


def test_quote_state_latest_reflects_the_most_recently_applied_quote():
    state = QuoteState("AAA", "fixture", QuoteFreshnessPolicy(freshness_limit_ms=500))
    state.apply(_quote("q1", "2024-06-10T09:30:00Z", Decimal(99), Decimal(1), Decimal(101), Decimal(1)))
    state.apply(_quote("q2", "2024-06-10T09:30:05Z", Decimal(100), Decimal(1), Decimal(102), Decimal(1)))
    assert state.latest().source_event_id == "q2"


def test_quote_state_before_any_quote_is_none():
    state = QuoteState("AAA", "fixture", QuoteFreshnessPolicy(freshness_limit_ms=500))
    assert state.latest() is None
    assert state.latest_with_freshness(parse_utc_timestamp_ns("2024-06-10T09:30:00Z")) is None


# ---- Freshness policy ----------------------------------------------------------

def test_freshness_policy_rejects_nonpositive_limit():
    with pytest.raises(ValueError, match="positive"):
        QuoteFreshnessPolicy(freshness_limit_ms=0)


def test_quote_within_freshness_limit_is_fresh():
    state = QuoteState("AAA", "fixture", QuoteFreshnessPolicy(freshness_limit_ms=1000))
    state.apply(_quote("q1", "2024-06-10T09:30:00Z", Decimal(99), Decimal(1), Decimal(101), Decimal(1)))
    decision_clock = parse_utc_timestamp_ns("2024-06-10T09:30:00.500000000Z")
    _snapshot, freshness = state.latest_with_freshness(decision_clock)
    assert freshness == FreshnessState.FRESH


def test_quote_beyond_freshness_limit_is_stale():
    state = QuoteState("AAA", "fixture", QuoteFreshnessPolicy(freshness_limit_ms=1000))
    state.apply(_quote("q1", "2024-06-10T09:30:00Z", Decimal(99), Decimal(1), Decimal(101), Decimal(1)))
    decision_clock = parse_utc_timestamp_ns("2024-06-10T09:30:02Z")
    _snapshot, freshness = state.latest_with_freshness(decision_clock)
    assert freshness == FreshnessState.STALE


def test_decision_clock_before_quote_availability_is_rejected():
    state = QuoteState("AAA", "fixture", QuoteFreshnessPolicy(freshness_limit_ms=1000))
    state.apply(_quote("q1", "2024-06-10T09:30:05Z", Decimal(99), Decimal(1), Decimal(101), Decimal(1)))
    with pytest.raises(ValueError, match="cannot precede"):
        state.latest_with_freshness(parse_utc_timestamp_ns("2024-06-10T09:30:00Z"))

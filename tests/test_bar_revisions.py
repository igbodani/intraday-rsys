from datetime import UTC, datetime
from decimal import Decimal

import pytest

from rsys.data.actions import CorporateAction
from rsys.data.bar_builder import (
    EvictedBucketError,
    SnapshotStatus,
    TradeBarBuilder,
    UnsupportedSplitOverlapError,
    raise_if_bucket_straddles_action,
)
from rsys.data.bar_rules import FixtureBarRuleSet
from rsys.data.events import (
    EventTimeMeaning,
    QualityState,
    TimingLabel,
    TradeCorrectionEvent,
    TradeEvent,
)
from rsys.data.timestamps import parse_utc_timestamp_ns

_COMMON = {
    "provider": "alpaca",
    "feed": "fixture",
    "event_time_meaning": EventTimeMeaning.PROVIDER_MARKET_TIME,
    "session_version": "nyse-2024-v1",
    "quality_state": QualityState.OK,
    "timing_label": TimingLabel.RECORDED,
}


def _trade(event_id, event_time, available_at=None, price=100, size=10):
    at = available_at or event_time
    return TradeEvent(
        **_COMMON, event_id=event_id, instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns(event_time), available_at_ns=parse_utc_timestamp_ns(at),
        price=Decimal(price), size=Decimal(size), trade_id=event_id, venue="V",
    )


def _cancel(event_id, original_trade_id, available_at):
    ns = parse_utc_timestamp_ns(available_at)
    return TradeCorrectionEvent(
        **_COMMON, event_id=event_id, instrument_id="AAA", event_time_ns=ns, available_at_ns=ns,
        original_trade_id=original_trade_id, action="cancel",
    )


def _builder(**kwargs) -> TradeBarBuilder:
    return TradeBarBuilder("AAA", "fixture", "1Min", FixtureBarRuleSet(), **kwargs)


# ---- BAR-010: old snapshots cannot mutate after a new arrival/correction ----

def test_previously_returned_snapshot_is_unaffected_by_a_later_correction():
    builder = _builder()
    snap_before = builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z"))
    builder.advance_clock(parse_utc_timestamp_ns("2024-06-10T09:31:00Z"))

    builder.on_trade(_trade("T2", "2024-06-10T09:30:20Z", available_at="2024-06-10T09:31:05Z", price=200, size=1))

    assert snap_before.close == Decimal(100)
    assert snap_before.volume == Decimal(10)


def test_bar_snapshot_is_frozen():
    builder = _builder()
    snap = builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z"))
    with pytest.raises(ValueError):
        snap.close = Decimal(999)


# ---- Retention policy: eviction must surface as an explicit unresolved state ----

def test_correction_against_an_evicted_bucket_is_explicit_not_silent():
    builder = _builder(retention_limit=1)
    builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z"))
    builder.advance_clock(parse_utc_timestamp_ns("2024-06-10T09:31:00Z"))  # closes bucket 1

    builder.on_trade(_trade("T2", "2024-06-10T09:31:05Z"))
    builder.advance_clock(parse_utc_timestamp_ns("2024-06-10T09:32:00Z"))  # closes bucket 2, evicts bucket 1

    with pytest.raises(EvictedBucketError):
        builder.on_correction(_cancel("cxl-T1", "T1", "2024-06-10T09:33:00Z"))


def test_new_trade_into_an_evicted_bucket_is_also_explicit_not_silent():
    builder = _builder(retention_limit=1)
    builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z"))
    builder.advance_clock(parse_utc_timestamp_ns("2024-06-10T09:31:00Z"))
    builder.on_trade(_trade("T2", "2024-06-10T09:31:05Z"))
    builder.advance_clock(parse_utc_timestamp_ns("2024-06-10T09:32:00Z"))

    with pytest.raises(EvictedBucketError):
        builder.on_trade(_trade("T1-late", "2024-06-10T09:30:40Z", available_at="2024-06-10T09:32:05Z"))


def test_retention_does_not_evict_within_the_declared_limit():
    builder = _builder(retention_limit=5)
    builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z"))
    builder.advance_clock(parse_utc_timestamp_ns("2024-06-10T09:31:00Z"))

    snap = builder.on_correction(_cancel("cxl-T1", "T1", "2024-06-10T09:31:30Z"))
    assert snap.status == SnapshotStatus.REVISED


# ---- BAR-012: a bucket straddling a split's dispatch is blocked -------------

def test_bucket_straddling_a_split_dispatch_is_blocked():
    action = CorporateAction(
        instrument_id="AAA", action_type="split", ratio=Decimal(2),
        effective_at=datetime(2024, 6, 10, 9, 30, 30, tzinfo=UTC),
        known_at=datetime(2024, 6, 10, 9, 30, 30, tzinfo=UTC),
        source="fixture_test",
    )
    bucket_start = parse_utc_timestamp_ns("2024-06-10T09:30:00Z")
    bucket_end = parse_utc_timestamp_ns("2024-06-10T09:31:00Z")

    with pytest.raises(UnsupportedSplitOverlapError):
        raise_if_bucket_straddles_action(bucket_start, bucket_end, action)


def test_bucket_not_overlapping_a_split_dispatch_is_not_blocked():
    action = CorporateAction(
        instrument_id="AAA", action_type="split", ratio=Decimal(2),
        effective_at=datetime(2024, 6, 10, 9, 29, 0, tzinfo=UTC),
        known_at=datetime(2024, 6, 10, 9, 29, 0, tzinfo=UTC),
        source="fixture_test",
    )
    bucket_start = parse_utc_timestamp_ns("2024-06-10T09:30:00Z")
    bucket_end = parse_utc_timestamp_ns("2024-06-10T09:31:00Z")

    raise_if_bucket_straddles_action(bucket_start, bucket_end, action)  # must not raise

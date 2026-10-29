"""Encodes the synthetic worked example from
docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md section 8 (09:30-09:31 bucket,
2024-06-10). Timestamps are used directly as UTC for test simplicity; the
plan's note to convert America/New_York to UTC in stored fixtures does not
change any of the arithmetic below."""
from decimal import Decimal

import pytest

from rsys.data.bar_builder import (
    BarSnapshot,
    ConflictingTradeError,
    MismatchedSeriesError,
    RejectedQualityError,
    SnapshotStatus,
    TradeBarBuilder,
    UnresolvedCorrectionError,
)
from rsys.data.bar_rules import FixtureBarRuleSet
from rsys.data.bar_version_store import BarVersionStore
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


def _trade(event_id, event_time, available_at, price, size, conditions=("ALL",)):
    return TradeEvent(
        **_COMMON, event_id=event_id, instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns(event_time), available_at_ns=parse_utc_timestamp_ns(available_at),
        price=Decimal(price), size=Decimal(size), trade_id=event_id, venue="V", conditions=conditions,
    )


def _cancel(event_id, original_trade_id, available_at):
    ns = parse_utc_timestamp_ns(available_at)
    return TradeCorrectionEvent(
        **_COMMON, event_id=event_id, instrument_id="AAA", event_time_ns=ns, available_at_ns=ns,
        original_trade_id=original_trade_id, action="cancel",
    )


def _builder() -> TradeBarBuilder:
    return TradeBarBuilder("AAA", "fixture", "1Min", FixtureBarRuleSet())


def _assert_ohlc(snap: BarSnapshot, o, h, low, c):
    assert (snap.open, snap.high, snap.low, snap.close) == (Decimal(o), Decimal(h), Decimal(low), Decimal(c))


def test_worked_example_full_sequence():
    builder = _builder()

    # T1 09:30:05 100x10 ALL
    s1 = builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10))
    _assert_ohlc(s1, 100, 100, 100, 100)
    assert (s1.volume, s1.trade_count) == (Decimal(10), 1)
    assert (s1.vwap_numerator, s1.vwap_denominator) == (Decimal(1000), Decimal(10))
    assert s1.status == SnapshotStatus.FORMING
    # BAR-001: the prefix after T1 contains no later-trade information.
    assert s1.close == Decimal(100)

    # T2 09:30:20 102x20 ALL
    s2 = builder.on_trade(_trade("T2", "2024-06-10T09:30:20Z", "2024-06-10T09:30:20Z", 102, 20))
    _assert_ohlc(s2, 100, 102, 100, 102)
    assert (s2.volume, s2.trade_count) == (Decimal(30), 2)
    assert (s2.vwap_numerator, s2.vwap_denominator) == (Decimal(3040), Decimal(30))

    # T3 09:30:30 150x5 VOLUME_ONLY: volume/count move, OHLC and VWAP do not (BAR-004).
    s3 = builder.on_trade(_trade("T3", "2024-06-10T09:30:30Z", "2024-06-10T09:30:30Z", 150, 5, ("VOLUME_ONLY",)))
    _assert_ohlc(s3, 100, 102, 100, 102)
    assert (s3.volume, s3.trade_count) == (Decimal(35), 3)
    assert (s3.vwap_numerator, s3.vwap_denominator) == (Decimal(3040), Decimal(30))

    # T4 09:30:50 101x10 ALL
    s4 = builder.on_trade(_trade("T4", "2024-06-10T09:30:50Z", "2024-06-10T09:30:50Z", 101, 10))
    _assert_ohlc(s4, 100, 102, 100, 101)
    assert (s4.volume, s4.trade_count) == (Decimal(45), 4)
    assert (s4.vwap_numerator, s4.vwap_denominator) == (Decimal(4050), Decimal(40))

    # 09:31:00 timer: close current knowledge. A quiet 10s before close still gets its closure (BAR-009).
    closed = builder.advance_clock(parse_utc_timestamp_ns("2024-06-10T09:31:00Z"))
    assert len(closed) == 1
    s_closed = closed[0]
    _assert_ohlc(s_closed, 100, 102, 100, 101)
    assert s_closed.status == SnapshotStatus.CLOSED_AS_OF
    assert s_closed.revision == 0

    # T5 arrives 09:31:05 with event_time 09:30:55 (late, same bucket): BAR-006 current-time revision.
    s5 = builder.on_trade(_trade("T5", "2024-06-10T09:30:55Z", "2024-06-10T09:31:05Z", 103, 5))
    _assert_ohlc(s5, 100, 103, 100, 103)
    assert (s5.volume, s5.trade_count) == (Decimal(50), 5)
    assert (s5.vwap_numerator, s5.vwap_denominator) == (Decimal(4565), Decimal(45))
    assert s5.status == SnapshotStatus.REVISED
    assert s5.revision == 1
    assert s5.available_at_ns == parse_utc_timestamp_ns("2024-06-10T09:31:05Z")

    # Cancel T2 at 09:31:10: recompute, not "subtract volume" (BAR-007).
    s6 = builder.on_correction(_cancel("cxl-T2", "T2", "2024-06-10T09:31:10Z"))
    _assert_ohlc(s6, 100, 103, 100, 103)
    assert (s6.volume, s6.trade_count) == (Decimal(30), 4)
    assert (s6.vwap_numerator, s6.vwap_denominator) == (Decimal(2525), Decimal(25))
    assert s6.revision == 2

    # Cancel T5 at 09:31:15.
    s7 = builder.on_correction(_cancel("cxl-T5", "T5", "2024-06-10T09:31:15Z"))
    _assert_ohlc(s7, 100, 101, 100, 101)
    assert (s7.volume, s7.trade_count) == (Decimal(25), 3)
    assert (s7.vwap_numerator, s7.vwap_denominator) == (Decimal(2010), Decimal(20))
    assert s7.revision == 3

    # BAR-010: the originally-closed snapshot object itself never changed.
    assert s_closed.close == Decimal(101)
    assert s_closed.revision == 0


# ---- BAR-002: half-open interval boundary ------------------------------------

def test_boundary_trade_belongs_to_the_next_bucket():
    builder = _builder()
    first_bucket_start = parse_utc_timestamp_ns("2024-06-10T09:30:00Z")
    next_bucket_start = parse_utc_timestamp_ns("2024-06-10T09:31:00Z")
    assert builder.bucket_start_ns(next_bucket_start) == next_bucket_start
    assert builder.bucket_start_ns(next_bucket_start) != first_bucket_start

    boundary_trade = builder.on_trade(_trade("Tb", "2024-06-10T09:31:00Z", "2024-06-10T09:31:00Z", 200, 1))
    assert boundary_trade.bar_start_ns == next_bucket_start


def test_nanosecond_precision_is_preserved_in_bucket_assignment():
    builder = _builder()
    just_before = parse_utc_timestamp_ns("2024-06-10T09:30:59.999999999Z")
    assert builder.bucket_start_ns(just_before) == parse_utc_timestamp_ns("2024-06-10T09:30:00Z")


# ---- BAR-005: idempotent redelivery vs. conflicting duplicate ---------------

def test_identical_redelivery_is_idempotent():
    builder = _builder()
    trade = _trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10)
    first = builder.on_trade(trade)
    second = builder.on_trade(trade)
    assert (first.volume, first.trade_count) == (second.volume, second.trade_count) == (Decimal(10), 1)


def test_conflicting_redelivery_without_a_correction_is_rejected():
    builder = _builder()
    builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10))
    with pytest.raises(ConflictingTradeError):
        builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 999, 10))


# ---- BAR-007: unresolved original references --------------------------------

def test_correction_referencing_an_unknown_trade_id_is_rejected():
    builder = _builder()
    with pytest.raises(UnresolvedCorrectionError):
        builder.on_correction(_cancel("cxl-ghost", "no-such-trade", "2024-06-10T09:31:10Z"))


# ---- BAR-008: no-price / no-trade states stay distinct -----------------------

def test_volume_only_only_bucket_has_no_eligible_price():
    builder = _builder()
    snap = builder.on_trade(_trade("T3", "2024-06-10T09:30:30Z", "2024-06-10T09:30:30Z", 150, 5, ("VOLUME_ONLY",)))
    assert snap.open is None
    assert snap.high is None
    assert snap.low is None
    assert snap.close is None
    assert snap.volume == Decimal(5)
    assert snap.vwap_denominator == Decimal(0)


def test_empty_bucket_snapshot_has_no_trades_and_no_price():
    builder = _builder()
    snap = builder.snapshot(parse_utc_timestamp_ns("2024-06-10T09:30:00Z"), parse_utc_timestamp_ns("2024-06-10T09:30:00Z"))
    assert snap.trade_count == 0
    assert snap.volume == Decimal(0)
    assert snap.open is None


# ---- BAR-009: timer closes a quiet bucket without waiting for a trade -------

def test_timer_closes_a_bucket_with_no_recent_trade():
    builder = _builder()
    builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10))
    closed = builder.advance_clock(parse_utc_timestamp_ns("2024-06-10T09:31:00Z"))
    assert len(closed) == 1
    assert closed[0].status == SnapshotStatus.CLOSED_AS_OF


# ---- C05 regression: a mismatched-series or non-OK-quality event must never mutate state ----

def test_wrong_instrument_trade_cannot_alter_this_builders_state():
    """C05: a BBB trade must never be accepted (and thus never mutate state) by an AAA builder,
    even if the caller mistakenly routes it there."""
    builder = _builder()  # instrument_id="AAA"
    wrong_series_trade = TradeEvent(
        **_COMMON, event_id="wrong", instrument_id="BBB",
        event_time_ns=parse_utc_timestamp_ns("2024-06-10T09:30:05Z"),
        available_at_ns=parse_utc_timestamp_ns("2024-06-10T09:30:05Z"),
        price=Decimal(100), size=Decimal(10), trade_id="wrong", venue="V",
    )
    with pytest.raises(MismatchedSeriesError):
        builder.on_trade(wrong_series_trade)

    snap = builder.snapshot(parse_utc_timestamp_ns("2024-06-10T09:30:00Z"), parse_utc_timestamp_ns("2024-06-10T09:30:05Z"))
    assert snap.trade_count == 0  # rejected before any mutation


def test_wrong_feed_trade_cannot_alter_this_builders_state():
    builder = _builder()  # feed="fixture"
    wrong_feed_trade = TradeEvent(
        **{**_COMMON, "feed": "other_feed"}, event_id="wrong", instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns("2024-06-10T09:30:05Z"),
        available_at_ns=parse_utc_timestamp_ns("2024-06-10T09:30:05Z"),
        price=Decimal(100), size=Decimal(10), trade_id="wrong", venue="V",
    )
    with pytest.raises(MismatchedSeriesError):
        builder.on_trade(wrong_feed_trade)


def test_non_ok_quality_trade_cannot_alter_builder_state():
    builder = _builder()
    suspect_trade = TradeEvent(
        **{**_COMMON, "quality_state": QualityState.SUSPECT}, event_id="s1", instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns("2024-06-10T09:30:05Z"),
        available_at_ns=parse_utc_timestamp_ns("2024-06-10T09:30:05Z"),
        price=Decimal(100), size=Decimal(10), trade_id="s1", venue="V",
    )
    with pytest.raises(RejectedQualityError):
        builder.on_trade(suspect_trade)


def test_trade_event_rejects_negative_or_zero_size():
    with pytest.raises(ValueError, match="size"):
        _trade("bad", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, -10)
    with pytest.raises(ValueError, match="size"):
        _trade("bad", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 0)


def test_trade_event_rejects_negative_or_zero_price():
    with pytest.raises(ValueError, match="price"):
        _trade("bad", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", -1, 10)


# ---- C06 regression: an as-of query must reflect only what was published by the cutoff ----

def test_version_store_as_of_does_not_leak_a_later_trades_price_into_an_earlier_query():
    """The original C06 defect: after T1 at second 5 and T2 at second 20, querying a builder
    snapshot as-of second 5 incorrectly returned T2's price (merely re-stamped with the earlier
    time) because `snapshot()` always recomputes from the CURRENT contributor set. The version
    store must instead answer only from what had actually been published by the cutoff."""
    store = BarVersionStore()
    builder = TradeBarBuilder("AAA", "fixture", "1Min", FixtureBarRuleSet(), version_store=store)
    identity = ("AAA", "fixture", "1Min", parse_utc_timestamp_ns("2024-06-10T09:30:00Z"))

    builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10))
    builder.on_trade(_trade("T2", "2024-06-10T09:30:20Z", "2024-06-10T09:30:20Z", 150, 5))

    as_of_5 = store.as_of(identity, parse_utc_timestamp_ns("2024-06-10T09:30:05Z"))
    assert as_of_5 is not None
    assert as_of_5.close == Decimal(100)  # only T1 had been published by second 5
    assert as_of_5.source_event_ids == ("T1",)

    as_of_20 = store.as_of(identity, parse_utc_timestamp_ns("2024-06-10T09:30:20Z"))
    assert as_of_20 is not None
    assert as_of_20.close == Decimal(150)

    # Meanwhile the builder's own live query always reflects everything accepted so far --
    # it is documented as current-only, never a historical substitute.
    live = builder.snapshot(identity[3], parse_utc_timestamp_ns("2024-06-10T09:30:05Z"))
    assert live.close == Decimal(150)


def test_version_store_as_of_before_any_publication_is_a_typed_unavailable_result():
    store = BarVersionStore()
    identity = ("AAA", "fixture", "1Min", parse_utc_timestamp_ns("2024-06-10T09:30:00Z"))
    assert store.as_of(identity, parse_utc_timestamp_ns("2024-06-10T09:30:00Z")) is None


def test_version_store_records_a_separate_version_for_a_correction():
    store = BarVersionStore()
    builder = TradeBarBuilder("AAA", "fixture", "1Min", FixtureBarRuleSet(), version_store=store)
    identity = ("AAA", "fixture", "1Min", parse_utc_timestamp_ns("2024-06-10T09:30:00Z"))

    builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10))
    builder.on_correction(TradeCorrectionEvent(
        **_COMMON, event_id="C1", instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns("2024-06-10T09:30:06Z"),
        available_at_ns=parse_utc_timestamp_ns("2024-06-10T09:30:06Z"),
        original_trade_id="T1", action="correct", corrected_price=Decimal(200), corrected_size=Decimal(10),
    ))

    history = store.history(identity)
    assert len(history) == 2
    assert history[0].close == Decimal(100)
    assert history[1].close == Decimal(200)
    before_correction = store.as_of(identity, parse_utc_timestamp_ns("2024-06-10T09:30:05Z"))
    assert before_correction is not None
    assert before_correction.close == Decimal(100)


# ---- C09/C10 regression: a correction's changed conditions reclassify eligibility ----

def test_correction_with_changed_conditions_reclassifies_eligibility():
    """C09 item 2 + C10: a correction that changes a trade's conditions must recompute field
    eligibility, not silently keep the original trade's stale classification."""
    builder = _builder()
    builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10, conditions=("ALL",)))
    snap = builder.on_correction(TradeCorrectionEvent(
        **_COMMON, event_id="C1", instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns("2024-06-10T09:30:06Z"),
        available_at_ns=parse_utc_timestamp_ns("2024-06-10T09:30:06Z"),
        original_trade_id="T1", action="correct", corrected_price=Decimal(150), corrected_size=Decimal(10),
        conditions=("VOLUME_ONLY",),
    ))
    # VOLUME_ONLY excludes every price field, so the bucket now has no eligible open/close/high/low.
    assert snap.open is None
    assert snap.close is None
    assert snap.volume == Decimal(10)


def test_correction_without_new_conditions_keeps_the_original_eligibility():
    builder = _builder()
    builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10, conditions=("ALL",)))
    snap = builder.on_correction(TradeCorrectionEvent(
        **_COMMON, event_id="C1", instrument_id="AAA",
        event_time_ns=parse_utc_timestamp_ns("2024-06-10T09:30:06Z"),
        available_at_ns=parse_utc_timestamp_ns("2024-06-10T09:30:06Z"),
        original_trade_id="T1", action="correct", corrected_price=Decimal(150), corrected_size=Decimal(10),
    ))
    assert snap.close == Decimal(150)  # price updated; eligibility (ALL) unchanged since untouched


# ---- IR-01 / R3I-10: a rejected correction leaves all state completely unchanged ----

def test_rejected_correction_has_no_side_effects():
    """R3I-10 regression: at 7084d4a, a correction with an unknown condition raised AFTER the
    live price/size had already been overwritten to the rejected replacement value. The
    contributor, its eligibility and the published history must be byte-for-byte identical
    before and after a rejected correction."""
    builder = _builder()
    before = builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10))

    with pytest.raises(Exception):  # noqa: B017 (UnknownConditionError from the rule set)
        builder.on_correction(TradeCorrectionEvent(
            **_COMMON, event_id="bad", instrument_id="AAA",
            event_time_ns=parse_utc_timestamp_ns("2024-06-10T09:30:06Z"),
            available_at_ns=parse_utc_timestamp_ns("2024-06-10T09:30:06Z"),
            original_trade_id="T1", action="correct", corrected_price=Decimal(999), corrected_size=Decimal(10),
            conditions=("SOME_UNDOCUMENTED_CODE",),
        ))

    after = builder.snapshot(before.bar_start_ns, parse_utc_timestamp_ns("2024-06-10T09:30:06Z"))
    assert after.close == Decimal(100)  # NOT 999 -- the rejected price never applied
    assert after.volume == Decimal(10)
    assert after.trade_count == 1


# ---- IR-01 / R3I-11: idempotent duplicate correction + explicit cross-bucket identity conflict ----

def test_duplicate_correction_and_identity_conflict_are_safe():
    builder = _builder()
    builder.on_trade(_trade("T1", "2024-06-10T09:30:05Z", "2024-06-10T09:30:05Z", 100, 10))
    builder.advance_clock(parse_utc_timestamp_ns("2024-06-10T09:31:00Z"))  # close the bucket

    cancel = _cancel("C1", "T1", "2024-06-10T09:31:05Z")
    first = builder.on_correction(cancel)
    assert first.revision == 1

    # Identical redelivery of the SAME cancel must be idempotent: no second revision.
    second = builder.on_correction(cancel)
    assert second.revision == 1
    assert second.volume == Decimal(0)

    # A bare trade_id reused in a DIFFERENT bucket is an explicit conflict, never a silent
    # redirect of future correction lookups away from the original bucket (R3I-11).
    with pytest.raises(ConflictingTradeError):
        builder.on_trade(_trade("T1", "2024-06-10T09:35:05Z", "2024-06-10T09:35:05Z", 500, 1))

    # The original bucket's correction index must still resolve to its own bucket.
    with pytest.raises(UnresolvedCorrectionError):
        builder.on_correction(_cancel("C2", "nonexistent", "2024-06-10T09:36:00Z"))
    still_cancelled = builder.snapshot(
        builder.bucket_start_ns(parse_utc_timestamp_ns("2024-06-10T09:30:05Z")),
        parse_utc_timestamp_ns("2024-06-10T09:31:05Z"),
    )
    assert still_cancelled.volume == Decimal(0)


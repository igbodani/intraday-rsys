from decimal import Decimal

import pytest

from rsys.data.models import (
    CorporateActionEvent,
    EventTimeMeaning,
    NormalizedBar,
    NormalizedBarIdentity,
    ProviderBarEvent,
    QualityState,
    QuoteEvent,
    TimerEvent,
    TimingLabel,
    TradeCorrectionEvent,
    TradeEvent,
)
from rsys.data.timestamps import ns_to_canonical_iso, parse_utc_timestamp_ns

COMMON = {
    "event_id": "evt-1",
    "instrument_id": "AAA",
    "provider": "alpaca",
    "feed": "fixture",
    "event_time_meaning": EventTimeMeaning.PROVIDER_MARKET_TIME,
    "session_version": "nyse-2024-v1",
    "quality_state": QualityState.OK,
    "timing_label": TimingLabel.RECORDED,
}


def _trade(**overrides):
    t = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    return TradeEvent(
        **COMMON,
        event_time_ns=t,
        available_at_ns=t,
        price=Decimal(100),
        size=Decimal(10),
        trade_id="t1",
        venue="V",
        **overrides,
    )


# ---- Nanosecond-precise timestamp parsing (BAR-002 groundwork) --------------

def test_parse_utc_timestamp_ns_preserves_nanosecond_precision():
    ns = parse_utc_timestamp_ns("2024-06-10T13:30:00.123456789Z")
    assert ns % 1_000_000_000 == 123456789


def test_parse_utc_timestamp_ns_accepts_plus_zero_offset_and_z_identically():
    assert parse_utc_timestamp_ns("2024-06-10T13:30:00Z") == parse_utc_timestamp_ns("2024-06-10T13:30:00+00:00")


@pytest.mark.parametrize(
    "raw",
    ["2024-06-10T13:30:00", "2024-06-10T13:30:00+05:00", "2024-06-10 13:30:00Z", "not-a-timestamp", ""],
)
def test_parse_utc_timestamp_ns_rejects_malformed_or_non_utc_input(raw):
    with pytest.raises(ValueError):
        parse_utc_timestamp_ns(raw)


def test_ns_to_canonical_iso_roundtrips():
    ns = parse_utc_timestamp_ns("2024-06-10T13:30:00.500000000Z")
    assert parse_utc_timestamp_ns(ns_to_canonical_iso(ns)) == ns


# ---- Market event envelope ---------------------------------------------------

def test_trade_event_requires_availability_not_before_event_time():
    t = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    with pytest.raises(ValueError):
        TradeEvent(
            **COMMON, event_time_ns=t, available_at_ns=t - 1,
            price=Decimal(100), size=Decimal(10), trade_id="t1", venue="V",
        )


def test_trade_event_is_frozen():
    trade = _trade()
    with pytest.raises(ValueError):
        trade.price = Decimal(200)


def test_trade_correction_requires_corrected_fields():
    with pytest.raises(ValueError):
        TradeCorrectionEvent(
            **COMMON,
            event_time_ns=1,
            available_at_ns=1,
            original_trade_id="t1",
            action="correct",
        )


def test_trade_cancel_must_not_carry_corrected_fields():
    with pytest.raises(ValueError):
        TradeCorrectionEvent(
            **COMMON,
            event_time_ns=1,
            available_at_ns=1,
            original_trade_id="t1",
            action="cancel",
            corrected_price=Decimal(1),
            corrected_size=Decimal(1),
        )


def test_valid_trade_cancel_is_accepted():
    event = TradeCorrectionEvent(
        **COMMON, event_time_ns=1, available_at_ns=1, original_trade_id="t1", action="cancel",
    )
    assert event.action == "cancel"


def test_quote_event_never_implies_trade_volume_fields_exist():
    quote = QuoteEvent(
        **COMMON, event_time_ns=1, available_at_ns=1, bid_price=Decimal(99), bid_size=Decimal(1),
        ask_price=Decimal(101), ask_size=Decimal(1), venue="V",
    )
    assert not hasattr(quote, "volume")


def test_provider_bar_event_rejects_nonpositive_duration():
    with pytest.raises(ValueError):
        ProviderBarEvent(
            **COMMON, event_time_ns=1, available_at_ns=1, bar_start_ns=100, bar_end_ns=100,
            interval="1Min", open=Decimal(1), high=Decimal(1), low=Decimal(1), close=Decimal(1),
            volume=Decimal(1), adjustment_mode="raw",
        )


# ---- C05 regression: validate the objects replay/builder actually consume ---

def test_completed_bar_available_at_its_own_start_is_rejected():
    """The original C05 defect: `available_at_ns` was only checked against `event_time_ns`
    (which a provider could set to bar_start), so a completed bar could claim to be visible
    before its own interval ended."""
    start = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")
    end = parse_utc_timestamp_ns("2024-06-10T13:31:00Z")
    with pytest.raises(ValueError, match="available"):
        ProviderBarEvent(
            **COMMON, event_time_ns=start, available_at_ns=start, bar_start_ns=start, bar_end_ns=end,
            interval="1Min", open=Decimal(100), high=Decimal(101), low=Decimal(99), close=Decimal(100),
            volume=Decimal(10), adjustment_mode="raw",
        )


def test_provider_bar_event_rejects_inconsistent_ohlc():
    start = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")
    end = parse_utc_timestamp_ns("2024-06-10T13:31:00Z")
    with pytest.raises(ValueError, match="OHLC"):
        ProviderBarEvent(
            **COMMON, event_time_ns=start, available_at_ns=end, bar_start_ns=start, bar_end_ns=end,
            interval="1Min", open=Decimal(100), high=Decimal(101), low=Decimal(99), close=Decimal(999),
            volume=Decimal(10), adjustment_mode="raw",
        )


def test_provider_bar_event_rejects_negative_volume():
    start = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")
    end = parse_utc_timestamp_ns("2024-06-10T13:31:00Z")
    with pytest.raises(ValueError, match="volume"):
        ProviderBarEvent(
            **COMMON, event_time_ns=start, available_at_ns=end, bar_start_ns=start, bar_end_ns=end,
            interval="1Min", open=Decimal(100), high=Decimal(101), low=Decimal(99), close=Decimal(100),
            volume=Decimal(-1), adjustment_mode="raw",
        )


@pytest.mark.parametrize(("price", "size"), [(Decimal(-1), Decimal(10)), (Decimal(0), Decimal(10)),
                                              (Decimal(100), Decimal(-1)), (Decimal(100), Decimal(0))])
def test_trade_event_rejects_nonpositive_price_or_size(price, size):
    t = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    with pytest.raises(ValueError):
        TradeEvent(**COMMON, event_time_ns=t, available_at_ns=t, price=price, size=size, trade_id="t1", venue="V")


def test_trade_correction_rejects_nonpositive_replacement_values():
    t = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    with pytest.raises(ValueError):
        TradeCorrectionEvent(
            **COMMON, event_time_ns=t, available_at_ns=t, original_trade_id="t1", action="correct",
            corrected_price=Decimal(-1), corrected_size=Decimal(10),
        )


def test_received_at_requires_recorded_timing_label():
    t = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    with pytest.raises(ValueError, match="received_at_ns"):
        TradeEvent(
            **{**COMMON, "timing_label": TimingLabel.MODELED}, event_time_ns=t, available_at_ns=t,
            received_at_ns=t, price=Decimal(100), size=Decimal(10), trade_id="t1", venue="V",
        )


def test_event_cannot_be_available_before_its_own_recorded_receipt():
    t = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    with pytest.raises(ValueError, match="received_at_ns"):
        TradeEvent(
            **COMMON, event_time_ns=t, available_at_ns=t, received_at_ns=t + 1,
            price=Decimal(100), size=Decimal(10), trade_id="t1", venue="V",
        )


def test_corporate_action_cannot_be_available_before_its_dispatch_bound():
    with pytest.raises(ValueError, match="action"):
        CorporateActionEvent(
            **COMMON, event_time_ns=1, available_at_ns=1, action_type="split", ratio=Decimal(2),
            effective_at_ns=1, known_at_ns=5,
        )


@pytest.mark.parametrize("field", ["event_time_ns", "available_at_ns", "source_sequence", "revision"])
def test_market_event_rejects_boolean_for_numeric_timing_fields(field):
    t = parse_utc_timestamp_ns("2024-06-10T13:30:05Z")
    kwargs = {"event_time_ns": t, "available_at_ns": t, field: True}
    with pytest.raises(ValueError):
        TradeEvent(**COMMON, price=Decimal(100), size=Decimal(10), trade_id="t1", venue="V", **kwargs)


def test_corporate_action_event_and_timer_event_construct():
    CorporateActionEvent(
        **COMMON, event_time_ns=1, available_at_ns=1, action_type="split", ratio=Decimal(2),
        effective_at_ns=1, known_at_ns=1,
    )
    TimerEvent(
        **{**COMMON, "event_time_meaning": EventTimeMeaning.SCHEDULED_BOUNDARY},
        event_time_ns=1, available_at_ns=1, boundary_name="session_close", calendar_version="nyse-2024-v1",
    )


# ---- NormalizedBar: identity, alignment, availability ------------------------

def _bar(**overrides) -> NormalizedBar:
    start = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")
    end = parse_utc_timestamp_ns("2024-06-10T13:31:00Z")
    defaults = {
        "instrument_id": "AAA", "feed": "fixture", "interval": "1Min", "bar_start_ns": start, "bar_end_ns": end,
        "available_at_ns": end, "open": Decimal(100), "high": Decimal(101), "low": Decimal(99), "close": Decimal(100),
        "volume": Decimal(10), "adjustment_mode": "raw", "price_unit_version": "data_rules_v1", "source": "fixture",
        "quality_state": QualityState.OK,
    }
    defaults.update(overrides)
    return NormalizedBar(**defaults)


def test_normalized_bar_identity_is_canonical_across_equivalent_timestamp_strings():
    bar_z = _bar(bar_start_ns=parse_utc_timestamp_ns("2024-06-10T13:30:00Z"))
    bar_offset = _bar(bar_start_ns=parse_utc_timestamp_ns("2024-06-10T13:30:00+00:00"))
    assert bar_z.identity() == bar_offset.identity()
    assert isinstance(bar_z.identity(), NormalizedBarIdentity)


def test_normalized_bar_rejects_duration_mismatching_declared_interval():
    start = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")
    with pytest.raises(ValueError):
        _bar(bar_start_ns=start, bar_end_ns=start + 30_000_000_000)  # 30s, not a full 1Min bucket


def test_normalized_bar_rejects_availability_before_bar_end():
    start = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")
    end = parse_utc_timestamp_ns("2024-06-10T13:31:00Z")
    with pytest.raises(ValueError):
        _bar(bar_start_ns=start, bar_end_ns=end, available_at_ns=start)


def test_normalized_bar_is_available_at_gate():
    bar = _bar()
    assert not bar.is_available_at(bar.available_at_ns - 1)
    assert bar.is_available_at(bar.available_at_ns)

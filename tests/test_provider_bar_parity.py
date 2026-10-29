"""Offline parity comparator tests (R3-B). All fixtures here are synthetic and declared as
such -- none of this proves Alpaca parity; it proves the comparator's own behavior."""
from decimal import Decimal

from rsys.data.bar_version_store import BarVersion
from rsys.data.events import (
    EventTimeMeaning,
    ProviderBarEvent,
    QualityState,
    TimingLabel,
)
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.parity import compare_bar_version_to_provider_bar

_COMMON = {
    "provider": "alpaca",
    "event_time_meaning": EventTimeMeaning.PROVIDER_MARKET_TIME,
    "session_version": "nyse-2024-v1",
    "quality_state": QualityState.OK,
    "timing_label": TimingLabel.RECORDED,
}

START = parse_utc_timestamp_ns("2024-06-10T09:30:00Z")
END = parse_utc_timestamp_ns("2024-06-10T09:31:00Z")


def _version(
    *, instrument_id="AAA", feed="fixture", interval="1Min", bar_start_ns=START, bar_end_ns=END,
    status="closed_as_of", revision=0, open_=100, high=102, low=100, close=101, volume=45,
    trade_count=4, vwap_numerator=4050, vwap_denominator=40,
):
    return BarVersion(
        instrument_id=instrument_id, feed=feed, interval=interval, bar_start_ns=bar_start_ns,
        bar_end_ns=bar_end_ns, available_at_ns=bar_end_ns, sequence=0, status=status, revision=revision,
        open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=Decimal(volume),
        trade_count=trade_count, vwap_numerator=Decimal(vwap_numerator), vwap_denominator=Decimal(vwap_denominator),
        rule_set_id="fixture_rules_v1", source_event_ids=("T1", "T4"),
    )


def _provider_bar(
    *, instrument_id="AAA", feed="fixture", interval="1Min", bar_start_ns=START, bar_end_ns=END,
    open_=100, high=102, low=100, close=101, volume=45, trade_count=4, vwap=None, bar_revision=1,
):
    return ProviderBarEvent(
        **_COMMON, event_id="pb1", instrument_id=instrument_id, feed=feed, event_time_ns=bar_end_ns,
        available_at_ns=bar_end_ns, bar_start_ns=bar_start_ns, bar_end_ns=bar_end_ns, interval=interval,
        open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=Decimal(volume),
        trade_count=trade_count, vwap=Decimal(vwap) if vwap is not None else None, adjustment_mode="raw",
        bar_revision=bar_revision,
    )


def test_matching_version_and_provider_bar_compare_as_a_match():
    result = compare_bar_version_to_provider_bar(_version(), _provider_bar())
    assert result.comparable is True
    assert result.match is True
    assert result.mismatches == ()


def test_mismatched_instrument_is_not_comparable():
    result = compare_bar_version_to_provider_bar(_version(instrument_id="AAA"), _provider_bar(instrument_id="BBB"))
    assert result.comparable is False
    assert "instrument_id" in result.not_comparable_reason


def test_mismatched_interval_is_not_comparable():
    result = compare_bar_version_to_provider_bar(_version(interval="1Min"), _provider_bar(interval="5Min"))
    assert result.comparable is False


def test_mismatched_bucket_boundary_is_not_comparable():
    next_bucket_end = parse_utc_timestamp_ns("2024-06-10T09:32:00Z")
    result = compare_bar_version_to_provider_bar(
        _version(bar_start_ns=START, bar_end_ns=END),
        _provider_bar(bar_start_ns=END, bar_end_ns=next_bucket_end),
    )
    assert result.comparable is False


def test_forming_version_is_never_compared_against_a_final_provider_bar():
    result = compare_bar_version_to_provider_bar(_version(status="forming"), _provider_bar())
    assert result.comparable is False
    assert "forming" in result.not_comparable_reason


def test_first_publication_version_vs_provider_revision_is_not_comparable():
    result = compare_bar_version_to_provider_bar(
        _version(revision=0), _provider_bar(bar_revision=2),
    )
    assert result.comparable is False
    assert "revision" in result.not_comparable_reason


def test_revised_version_can_compare_against_a_provider_revision():
    result = compare_bar_version_to_provider_bar(
        _version(revision=1, close=103, high=103), _provider_bar(bar_revision=2, close=103, high=103),
    )
    assert result.comparable is True
    assert result.match is True


def test_a_genuine_field_difference_is_reported_as_an_exact_mismatch():
    result = compare_bar_version_to_provider_bar(_version(close=101), _provider_bar(close=102))
    assert result.comparable is True
    assert result.match is False
    field_mismatch = result.mismatches[0]
    assert field_mismatch.field == "close"
    assert field_mismatch.builder_value == Decimal(101)
    assert field_mismatch.provider_value == Decimal(102)


def test_vwap_comparison_uses_a_declared_precision_not_a_wide_tolerance():
    version = _version(vwap_numerator=4051, vwap_denominator=40)  # 101.275
    provider = _provider_bar(vwap=Decimal("101.28"))
    result = compare_bar_version_to_provider_bar(version, provider, vwap_precision=2)
    assert result.comparable is True
    assert result.match is True  # 101.275 rounds to 101.28 (quantize uses ROUND_HALF_EVEN)


def test_vwap_comparison_catches_a_real_discrepancy_at_declared_precision():
    version = _version(vwap_numerator=4050, vwap_denominator=40)  # 101.25
    provider = _provider_bar(vwap=Decimal("101.50"))
    result = compare_bar_version_to_provider_bar(version, provider, vwap_precision=2)
    assert result.match is False
    assert any(m.field == "vwap" for m in result.mismatches)

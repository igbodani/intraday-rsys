import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from rsys.data.actions import (
    apply_split_to_position,
    bars_invalidated_by_action,
    convert_price_to_current_units,
    convert_volume_to_current_units,
    is_dispatched_by,
)
from rsys.data.bars import (
    check_adjustment_mode,
    check_required_intervals,
    check_timezone_present,
    reconcile_duplicates,
    validate_ohlcv,
)
from rsys.data.models import (
    CorporateAction,
    DataQualityCode,
    NormalizedBar,
    QualityState,
    RawBar,
    SetupStatus,
)
from rsys.data.timestamps import parse_utc_timestamp_ns

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_CASES = json.loads((REPO_ROOT / "fixtures" / "data_cases.json").read_text(encoding="utf-8"))
QUALITY_CASES = {c["id"]: c for c in DATA_CASES["quality_cases"]}


def _bar(payload: dict) -> RawBar:
    return RawBar(**payload)


def test_dq01_invalid_ohlc_ordering():
    case = QUALITY_CASES["DQ01"]
    assert validate_ohlcv(_bar(case["input"])) == DataQualityCode(case["expected"])


def test_dq02_invalid_volume():
    case = QUALITY_CASES["DQ02"]
    assert validate_ohlcv(_bar(case["input"])) == DataQualityCode(case["expected"])


def test_valid_bar_has_no_quality_code():
    assert validate_ohlcv(RawBar(open="100", high="101", low="99", close="100", volume="10")) is None


def test_dq03_exact_duplicate_payloads_deduplicate():
    case = QUALITY_CASES["DQ03"]
    resolution = reconcile_duplicates([_bar(p) for p in case["payloads"]])
    assert resolution.code == DataQualityCode(case["expected"])
    assert resolution.duplicate_count == case["expected_duplicate_count"]


def test_dq04_conflicting_duplicate_payloads_block():
    case = QUALITY_CASES["DQ04"]
    resolution = reconcile_duplicates([_bar(p) for p in case["payloads"]])
    assert resolution.code == DataQualityCode(case["expected"])


def test_dq05_missing_required_interval_marks_setup_unavailable():
    case = QUALITY_CASES["DQ05"]
    result = check_required_intervals(case["required_starts"], case["received_starts"])
    assert result.code == DataQualityCode(case["expected"])
    assert result.setup_status == SetupStatus(case["expected_setup_status"])
    assert result.missing_starts == ["09:31:00"]


def test_complete_coverage_has_no_missing_intervals():
    result = check_required_intervals(["09:30:00", "09:31:00"], ["09:30:00", "09:31:00"])
    assert result.code is None
    assert result.setup_status == SetupStatus.AVAILABLE


def test_dq06_naive_timestamp_without_source_zone_is_rejected():
    case = QUALITY_CASES["DQ06"]
    code = check_timezone_present(case["provider_timestamp"], case["source_timezone"])
    assert code == DataQualityCode(case["expected"])


def test_timestamp_with_explicit_source_timezone_is_accepted():
    assert check_timezone_present("2024-11-03T01:30:00", "America/New_York") is None


def test_timestamp_with_utc_offset_is_accepted():
    assert check_timezone_present("2024-11-03T01:30:00Z", None) is None


def test_dq07_adjustment_mode_mismatch_is_rejected():
    case = QUALITY_CASES["DQ07"]
    code = check_adjustment_mode(case["execution_required_adjustment"], case["input_adjustment"])
    assert code == DataQualityCode(case["expected"])


def test_matching_adjustment_mode_is_accepted():
    assert check_adjustment_mode("raw", "raw") is None


# ---- F17: 2-for-1 split, raw/as-of unit consistency --------------------------

def test_f17_split_converts_position_quantity_and_levels():
    converted = apply_split_to_position(
        quantity=Decimal(5), stop=Decimal(98), target=Decimal(104), ratio=Decimal(2),
    )
    assert converted.quantity == Decimal(10)
    assert converted.stop == Decimal(49)
    assert converted.target == Decimal(52)
    # Equity (quantity * price) is invariant under a consistent split conversion.
    assert Decimal(5) * Decimal(100) == converted.quantity * (Decimal(100) / Decimal(2))


def test_f17_prior_volume_doubles_in_current_units():
    assert convert_volume_to_current_units(Decimal(1000), Decimal(2)) == Decimal(2000)


def test_f17_historical_price_halves_in_current_units():
    assert convert_price_to_current_units(Decimal(100), Decimal(2)) == Decimal(50)


# ---- Corporate-action dispatch timing (future-action invariance) ------------

def test_action_dispatches_at_the_later_of_effective_and_known():
    action = CorporateAction(
        instrument_id="AAA", action_type="split", ratio=Decimal(2),
        effective_at=datetime(2024, 6, 10, tzinfo=UTC),
        known_at=datetime(2024, 6, 12, tzinfo=UTC),
        source="fixture_test",
    )
    assert action.dispatch_at == datetime(2024, 6, 12, tzinfo=UTC)


def test_future_split_does_not_change_an_earlier_published_decision():
    """A later-discovered action must never retroactively change an already-published output."""
    action = CorporateAction(
        instrument_id="AAA", action_type="split", ratio=Decimal(2),
        effective_at=datetime(2024, 6, 10, tzinfo=UTC),
        known_at=datetime(2024, 6, 20, tzinfo=UTC),  # discovered 10 days after it took effect
        source="fixture_test",
    )
    decision_before_known = datetime(2024, 6, 15, tzinfo=UTC)  # after effective, before known
    decision_after_known = datetime(2024, 6, 21, tzinfo=UTC)

    assert not is_dispatched_by(action, decision_before_known)
    assert is_dispatched_by(action, decision_after_known)

    # The earlier decision's price stays in old units; only the later one converts.
    raw_price = Decimal(100)
    price_at_earlier_decision = (
        convert_price_to_current_units(raw_price, action.ratio)
        if is_dispatched_by(action, decision_before_known)
        else raw_price
    )
    price_at_later_decision = (
        convert_price_to_current_units(raw_price, action.ratio)
        if is_dispatched_by(action, decision_after_known)
        else raw_price
    )
    assert price_at_earlier_decision == Decimal(100)
    assert price_at_later_decision == Decimal(50)


def test_action_rejects_nonpositive_ratio():
    with pytest.raises(ValueError):
        CorporateAction(
            instrument_id="AAA", action_type="split", ratio=Decimal(0),
            effective_at=datetime(2024, 6, 10, tzinfo=UTC),
            known_at=datetime(2024, 6, 10, tzinfo=UTC),
            source="fixture_test",
        )


def test_action_rejects_naive_timestamps():
    with pytest.raises(ValueError):
        CorporateAction(
            instrument_id="AAA", action_type="split", ratio=Decimal(2),
            effective_at=datetime(2024, 6, 10),  # naive, no tzinfo
            known_at=datetime(2024, 6, 10, tzinfo=UTC),
            source="fixture_test",
        )


def test_action_rejects_non_utc_offset_timestamps():
    from datetime import timedelta, timezone

    with pytest.raises(ValueError):
        CorporateAction(
            instrument_id="AAA", action_type="split", ratio=Decimal(2),
            effective_at=datetime(2024, 6, 10, tzinfo=timezone(timedelta(hours=-5))),
            known_at=datetime(2024, 6, 10, tzinfo=UTC),
            source="fixture_test",
        )


def _normalized_bar(bar_start_ns: int, bar_end_ns: int) -> NormalizedBar:
    return NormalizedBar(
        instrument_id="AAA", feed="fixture", interval="1Min", bar_start_ns=bar_start_ns, bar_end_ns=bar_end_ns,
        available_at_ns=bar_end_ns, open=Decimal(100), high=Decimal(101), low=Decimal(99), close=Decimal(100),
        volume=Decimal(10), adjustment_mode="raw", price_unit_version="data_rules_v1", source="fixture",
        quality_state=QualityState.OK,
    )


def test_bar_straddling_a_split_dispatch_is_invalidated():
    bar_start = parse_utc_timestamp_ns("2024-06-10T13:30:00Z")
    bar_end = parse_utc_timestamp_ns("2024-06-10T13:31:00Z")
    straddling_bar = _normalized_bar(bar_start, bar_end)
    clean_bar = _normalized_bar(bar_end, parse_utc_timestamp_ns("2024-06-10T13:32:00Z"))
    action = CorporateAction(
        instrument_id="AAA", action_type="split", ratio=Decimal(2),
        effective_at=datetime(2024, 6, 10, 13, 30, 30, tzinfo=UTC),  # mid-bar
        known_at=datetime(2024, 6, 10, 13, 30, 30, tzinfo=UTC),
        source="fixture_test",
    )

    invalidated = bars_invalidated_by_action(action, [straddling_bar, clean_bar])

    assert invalidated == [straddling_bar]

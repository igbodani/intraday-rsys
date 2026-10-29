import pytest

from rsys.data.events import TimingLabel
from rsys.data.manifest import CoverageExclusion, ExclusionReason, UniverseManifest


def _manifest(**overrides) -> UniverseManifest:
    defaults = {
        "universe_basis": "alpaca_active_2024_snapshot",
        "feed": "fixture",
        "calendar_version": "nyse-2024-v1",
        "rules_version": "rvol_then_gain_v1",
        "requested_instruments": ("AAA", "BBB"),
        "observed_instruments": ("AAA",),
        "exclusions": (
            CoverageExclusion(
                instrument_id="BBB", feed="fixture", session_date="2024-06-10",
                reason=ExclusionReason.MISSING_DELIVERY, detail="feed gap",
            ),
        ),
        "corporate_action_coverage": "splits_only_2024_v1",
        "timing_provenance": TimingLabel.RECORDED,
        "data_precision": "ns",
    }
    defaults.update(overrides)
    return UniverseManifest(**defaults)


def test_manifest_accepts_exclusions_covering_the_gap():
    manifest = _manifest()
    assert manifest.is_missing_delivery("BBB", "2024-06-10")
    assert not manifest.is_verified_no_trade("BBB", "2024-06-10")


def test_manifest_rejects_a_requested_instrument_neither_observed_nor_excluded():
    with pytest.raises(ValueError):
        _manifest(observed_instruments=("AAA",), exclusions=())


def test_verified_no_trade_is_distinct_from_missing_delivery():
    manifest = _manifest(
        observed_instruments=("AAA",),
        exclusions=(
            CoverageExclusion(
                instrument_id="BBB", feed="fixture", session_date="2024-06-10",
                reason=ExclusionReason.VERIFIED_NO_TRADE, detail="confirmed zero prints",
            ),
        ),
    )
    assert manifest.is_verified_no_trade("BBB", "2024-06-10")
    assert not manifest.is_missing_delivery("BBB", "2024-06-10")


def test_manifest_unknown_field_rejected():
    with pytest.raises(ValueError):
        UniverseManifest(
            universe_basis="x", feed="fixture", calendar_version="nyse-2024-v1", rules_version="v1",
            requested_instruments=(), observed_instruments=(), corporate_action_coverage="none",
            timing_provenance=TimingLabel.RECORDED, data_precision="ns", unexpected_field=1,
        )


def test_exclusion_reason_is_a_closed_declared_set():
    with pytest.raises(ValueError):
        CoverageExclusion(
            instrument_id="BBB", feed="fixture", session_date="2024-06-10",
            reason="made_up_reason", detail="x",
        )

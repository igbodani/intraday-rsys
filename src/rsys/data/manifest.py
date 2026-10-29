"""Universe/coverage manifest (DATA-002, docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md section 6).

Declares requested vs. observed coverage, the historical-universe basis,
exclusions and their reasons, calendar/rules versions, corporate-action
coverage, timing provenance and data precision. Exclusions are recorded as
structured data here, not left as log text, and a verified no-trade interval
is a distinct, explicit state from missing delivery -- neither becomes a
silently invented flat candle.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rsys.data.events import TimingLabel


class ExclusionReason(StrEnum):
    VERIFIED_NO_TRADE = "verified_no_trade"  # the provider confirms the interval traded nothing
    MISSING_DELIVERY = "missing_delivery"  # the feed simply did not deliver required coverage
    INSUFFICIENT_HISTORY = "insufficient_history"  # fewer comparable sessions than required
    UNSUPPORTED_ACTION = "unsupported_action"  # an action type/ratio this contract cannot convert
    INACTIVE_SYMBOL = "inactive_symbol"  # delisted/inactive at the requested date
    FEED_MISMATCH = "feed_mismatch"  # mixed-feed data that cannot be pooled under one threshold


class CoverageExclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    feed: str
    session_date: str  # ISO 8601 date
    reason: ExclusionReason
    detail: str


class UniverseManifest(BaseModel):
    """One manifest per experiment/run's data acquisition; frozen inputs for its evidence."""

    model_config = ConfigDict(extra="forbid")

    universe_basis: str  # e.g. "alpaca_active_2024_snapshot"; never a reconstructed historical universe
    feed: str
    calendar_version: str
    rules_version: str
    requested_instruments: tuple[str, ...]
    observed_instruments: tuple[str, ...]
    exclusions: tuple[CoverageExclusion, ...] = ()
    corporate_action_coverage: str  # declared coverage statement (e.g. "splits_only_2024_v1")
    timing_provenance: TimingLabel
    input_hashes: dict[str, str] = Field(default_factory=dict)
    data_precision: str  # e.g. "ns" | "us"
    gaps: tuple[str, ...] = ()  # declared gap descriptors; never silently absent

    @model_validator(mode="after")
    def _check_observed_subset_of_requested_or_excluded(self) -> UniverseManifest:
        requested = set(self.requested_instruments)
        observed = set(self.observed_instruments)
        excluded = {e.instrument_id for e in self.exclusions}
        missing = requested - observed - excluded
        if missing:
            raise ValueError(
                f"requested instrument(s) {sorted(missing)} are neither observed nor declared excluded"
            )
        return self

    def is_verified_no_trade(self, instrument_id: str, session_date: str) -> bool:
        return any(
            e.instrument_id == instrument_id and e.session_date == session_date
            and e.reason == ExclusionReason.VERIFIED_NO_TRADE
            for e in self.exclusions
        )

    def is_missing_delivery(self, instrument_id: str, session_date: str) -> bool:
        return any(
            e.instrument_id == instrument_id and e.session_date == session_date
            and e.reason == ExclusionReason.MISSING_DELIVERY
            for e in self.exclusions
        )

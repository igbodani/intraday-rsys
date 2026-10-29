"""Corporate-action contract: dispatch timing and raw/current-unit conversion
(docs/data_rules.md "Price and corporate-action units"; F17 split math).

An action is dispatched at `max(effective_at, known_at)`. A later-discovered
action (known_at after an earlier decision) must never change that earlier
decision's already-published output -- this module provides the boundary
check (`is_dispatched_by`); replaying a full event stream against it is
M1-02's concern.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, field_validator

from rsys.data.bars import NormalizedBar

# Pins which documented conversion formulas (data_rules.md "Price and corporate-action
# units") were applied; a NormalizedBar's price_unit_version should cite this.
CONVERSION_CONTRACT_VERSION = "data_rules_v1"


class CorporateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    action_type: Literal["split"]
    ratio: Decimal  # new_shares / old_shares, e.g. 2-for-1 -> 2
    effective_at: datetime  # UTC
    known_at: datetime  # UTC
    source: str
    revision: int = 1

    @field_validator("ratio")
    @classmethod
    def _check_ratio_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("ratio must be a positive value")
        return v

    @field_validator("effective_at", "known_at")
    @classmethod
    def _check_tz_aware_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("corporate-action timestamps must be explicit tz-aware UTC, not naive")
        if v.utcoffset().total_seconds() != 0:
            raise ValueError("corporate-action timestamps must be UTC (zero offset)")
        return v

    @property
    def dispatch_at(self) -> datetime:
        return max(self.effective_at, self.known_at)


def is_dispatched_by(action: CorporateAction, at: datetime) -> bool:
    """An action only applies at/after `max(effective_at, known_at)`; never earlier."""
    return at >= action.dispatch_at


def convert_price_to_current_units(old_price: Decimal, ratio: Decimal) -> Decimal:
    return old_price / ratio


def convert_volume_to_current_units(old_volume: Decimal, ratio: Decimal) -> Decimal:
    return old_volume * ratio


class ConvertedPosition(NamedTuple):
    quantity: Decimal
    stop: Decimal
    target: Decimal


def apply_split_to_position(quantity: Decimal, stop: Decimal, target: Decimal, ratio: Decimal) -> ConvertedPosition:
    """Seeded positions change quantity by `ratio`, per-share stop/target by `1/ratio` (F17)."""
    return ConvertedPosition(quantity=quantity * ratio, stop=stop / ratio, target=target / ratio)


def bars_invalidated_by_action(action: CorporateAction, bars: list[NormalizedBar]) -> list[NormalizedBar]:
    """A bar whose interval straddles the action's dispatch moment mixes old- and new-unit
    prices mid-bar; data_rules.md blocks that interval rather than settling it against an
    already-converted position. Returns the affected bars, as data, not log text."""
    dispatch_ns = int(action.dispatch_at.timestamp() * 1_000_000_000)
    return [bar for bar in bars if bar.bar_start_ns < dispatch_ns < bar.bar_end_ns]



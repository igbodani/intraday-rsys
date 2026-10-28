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
            raise ValueError(f"ratio must be a positive value, got {v!r}")
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


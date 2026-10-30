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

from rsys.data.models import ConvertedPosition, CorporateAction, NormalizedBar

# Pins which documented conversion formulas (data_rules.md "Price and corporate-action
# units") were applied; a NormalizedBar's price_unit_version should cite this.
CONVERSION_CONTRACT_VERSION = "data_rules_v1"


def is_dispatched_by(action: CorporateAction, at: datetime) -> bool:
    """An action only applies at/after `max(effective_at, known_at)`; never earlier."""
    return at >= action.dispatch_at


def convert_price_to_current_units(old_price: Decimal, ratio: Decimal) -> Decimal:
    return old_price / ratio


def convert_volume_to_current_units(old_volume: Decimal, ratio: Decimal) -> Decimal:
    return old_volume * ratio


def apply_split_to_position(
    quantity: Decimal, stop: Decimal, target: Decimal, ratio: Decimal
) -> ConvertedPosition:
    """Seeded positions change quantity by `ratio`, per-share stop/target by `1/ratio` (F17)."""
    return ConvertedPosition(
        quantity=quantity * ratio, stop=stop / ratio, target=target / ratio
    )


def bars_invalidated_by_action(
    action: CorporateAction, bars: list[NormalizedBar]
) -> list[NormalizedBar]:
    """A bar whose interval straddles the action's dispatch moment mixes old- and new-unit
    prices mid-bar; data_rules.md blocks that interval rather than settling it against an
    already-converted position. Returns the affected bars, as data, not log text."""
    dispatch_ns = int(action.dispatch_at.timestamp() * 1_000_000_000)
    return [bar for bar in bars if bar.bar_start_ns < dispatch_ns < bar.bar_end_ns]

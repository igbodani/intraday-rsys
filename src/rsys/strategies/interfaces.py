"""Strategies shared definitions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from rsys.account.models import Proposal


class StrategyLike(Protocol):
    def on_completed_bar(self, bar: Any, *, is_candidate: bool) -> Proposal | None: ...
    def on_filled(self, entry_fill: Decimal) -> Decimal: ...
    def on_rejected_or_expired(self) -> None: ...
    def on_position_closed(self) -> None: ...

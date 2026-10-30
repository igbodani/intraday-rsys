"""Data shared definitions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from rsys.data.models import HistorySession


class HistoryProvider(Protocol):
    """Prior-session observations for one instrument, already calendar-ordered newest
    first and in current split units. Implementations declare their own source/coverage;
    this interface carries no feed-entitlement guarantee by itself."""

    def sessions_before(self, symbol: str, decision_date: date) -> list[HistorySession]:
        """Newest-first prior sessions usable as RVOL lookback candidates."""
        ...

    def previous_session_close(
        self, symbol: str, decision_date: date
    ) -> Decimal | None:
        """Previous regular-session raw close, current split units; `None` if unknown."""
        ...

    def prior_session_dollar_volume_inputs(
        self, symbol: str, decision_date: date
    ) -> tuple[Decimal, Decimal] | None:
        """`(prior_session_volume, prior_session_close)`, current split units; `None` if
        unavailable (eligibility is then `False`, never defaulted to eligible)."""
        ...

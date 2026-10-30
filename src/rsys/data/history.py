"""Synthetic history provider and strict prior-session date selection."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from rsys.data.models import HistorySession, SyntheticSymbolHistory


class FixtureHistoryProvider:
    """A declared synthetic/fixture `HistoryProvider`. `data_cases.json`'s hand-calculated
    H01-H03 values are the kind of input this wraps; it is not a provider adapter and must
    never be presented as real historical coverage (D12/D13 remain Open)."""

    def __init__(self, history: dict[str, SyntheticSymbolHistory]):
        self._history = history

    def sessions_before(self, symbol: str, decision_date: date) -> list[HistorySession]:
        record = self._history.get(symbol)
        return prior_sessions(record.sessions if record else [], decision_date)

    def previous_session_close(
        self, symbol: str, decision_date: date
    ) -> Decimal | None:
        record = self._history.get(symbol)
        return record.previous_session_close if record else None

    def prior_session_dollar_volume_inputs(
        self, symbol: str, decision_date: date
    ) -> tuple[Decimal, Decimal] | None:
        record = self._history.get(symbol)
        if (
            record is None
            or record.prior_session_volume is None
            or record.prior_session_close_for_dollar_volume is None
        ):
            return None
        return record.prior_session_volume, record.prior_session_close_for_dollar_volume


def prior_sessions(
    sessions: list[HistorySession], decision_date: date
) -> list[HistorySession]:
    """Enforce the dated boundary even for a provider that returns unbounded history.

    Keep missing prior rows in the calendar selection: never backfill a missing selected
    observation with an older session. Duplicate dates are ambiguous, not extra samples.
    """
    prior = [s for s in sessions if s.session_date < decision_date]
    if len({s.session_date for s in prior}) != len(prior):
        raise ValueError("duplicate scanner history session date")
    return sorted(prior, key=lambda s: s.session_date, reverse=True)

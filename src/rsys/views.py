"""Bounded as-of views over a replayed event stream (R1).

Snapshot views are immutable: every query is bounded by a cutoff and returns a
fresh list, never a reference into live state. The default baseline strategy
context exposes only completed bars; an opted-in intrabar variant additionally
exposes the current forming bar, latest trade and latest quote -- the baseline
never silently gains forming-bar access, and an intrabar variant never silently
loses the closed-bar view.
"""
from __future__ import annotations

from collections.abc import Sequence

from rsys.data.events import MarketEvent, ProviderBarEvent, QuoteEvent, TradeEvent


class ReplayView:
    """Read-only access to a fixed, already-ordered event collection, bounded by availability."""

    def __init__(self, events: Sequence[MarketEvent]):
        self._events = tuple(events)

    def closed_bars(self, instrument_id: str, as_of_ns: int) -> list[ProviderBarEvent]:
        """Only a bar's first/closing publication, available at or before `as_of_ns`.
        Post-closure revisions (R2) are a separate, later concern -- never silently substituted here.
        """
        return [
            e for e in self._events
            if isinstance(e, ProviderBarEvent) and e.instrument_id == instrument_id
            and e.bar_revision == 1 and e.available_at_ns <= as_of_ns
        ]

    def latest_trade(self, instrument_id: str, as_of_ns: int) -> TradeEvent | None:
        candidates = [
            e for e in self._events
            if isinstance(e, TradeEvent) and e.instrument_id == instrument_id and e.available_at_ns <= as_of_ns
        ]
        return max(candidates, key=lambda e: e.available_at_ns, default=None)

    def latest_quote(self, instrument_id: str, as_of_ns: int) -> QuoteEvent | None:
        candidates = [
            e for e in self._events
            if isinstance(e, QuoteEvent) and e.instrument_id == instrument_id and e.available_at_ns <= as_of_ns
        ]
        return max(candidates, key=lambda e: e.available_at_ns, default=None)

    def forming_bar(self, instrument_id: str, as_of_ns: int) -> None:
        """Explicit, not silent: no trade-driven bar builder exists until R2."""
        raise NotImplementedError("forming_bar requires R2's trade-driven bar builder")


class ClosedOnlyContext:
    """Baseline strategy context: completed bars only (ORB/VWAP's current contract)."""

    def __init__(self, view: ReplayView, as_of_ns: int):
        self._view = view
        self._as_of_ns = as_of_ns

    def closed_bars(self, instrument_id: str) -> list[ProviderBarEvent]:
        return self._view.closed_bars(instrument_id, self._as_of_ns)


class IntrabarContext(ClosedOnlyContext):
    """An explicitly opted-in variant with additional, separately-evaluated capabilities."""

    def forming_bar(self, instrument_id: str):
        return self._view.forming_bar(instrument_id, self._as_of_ns)

    def latest_trade(self, instrument_id: str) -> TradeEvent | None:
        return self._view.latest_trade(instrument_id, self._as_of_ns)

    def latest_quote(self, instrument_id: str) -> QuoteEvent | None:
        return self._view.latest_quote(instrument_id, self._as_of_ns)

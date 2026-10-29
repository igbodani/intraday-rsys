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
from typing import TYPE_CHECKING

from rsys.data.bar_builder import BarSnapshot
from rsys.data.events import MarketEvent, ProviderBarEvent, QuoteEvent, TradeEvent

if TYPE_CHECKING:
    from rsys.replay_runtime import SeriesRouter


class ReplayView:
    """Read-only access to a fixed, already-ordered event collection, bounded by availability.

    `events` MUST already be in full replay dispatch order (e.g. the output of
    `rsys.replay.replay_order`/`ReplayStream`) -- `latest_trade`/`latest_quote` rely on that
    order to break availability ties correctly (C07): among several events sharing the same
    `available_at_ns`, the one actually dispatched LAST (by phase/sequence/stable tiebreak) is
    the true "latest", not merely the first one encountered.

    `router`, if given, must already reflect exactly the events processed so far (R3-B) -- this
    view never processes events itself and never peeks at anything beyond what the router was
    told to apply.
    """

    def __init__(self, events: Sequence[MarketEvent], *, router: SeriesRouter | None = None):
        self._events = tuple(events)
        self._router = router

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
        """The event actually dispatched LAST among those available at or before `as_of_ns`
        (C07): ties at the same `available_at_ns` are broken by declared dispatch order, not by
        whichever candidate is encountered first."""
        candidates = [
            e for e in self._events
            if isinstance(e, TradeEvent) and e.instrument_id == instrument_id and e.available_at_ns <= as_of_ns
        ]
        return candidates[-1] if candidates else None

    def latest_quote(self, instrument_id: str, as_of_ns: int) -> QuoteEvent | None:
        """See `latest_trade`'s docstring: the last-dispatched eligible event, not a timestamp-
        only `max()` which returns the first tie encountered."""
        candidates = [
            e for e in self._events
            if isinstance(e, QuoteEvent) and e.instrument_id == instrument_id and e.available_at_ns <= as_of_ns
        ]
        return candidates[-1] if candidates else None

    def forming_bar(self, instrument_id: str, as_of_ns: int) -> BarSnapshot | None:
        """The current forming bar for `instrument_id` at `as_of_ns`, or `None` if that
        interval has already closed or has no contributors yet (R3-B). Explicit, not silent,
        when no router was wired: a `bar_v1`/closed-only replay never silently gains forming-bar
        access."""
        if self._router is None:
            raise NotImplementedError("forming_bar requires a wired SeriesRouter (R3-B)")
        return self._router.forming_bar(instrument_id, as_of_ns)


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

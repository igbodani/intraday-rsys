"""Minimal R3-B integration structure: a series-identity router and a replay runner.

These are responsibility suggestions made concrete, not a framework. The router owns
per-series mutable builder/quote state (provider normalization and account mutation stay
outside it). The runner schedules events, advances the clock and dispatches phases, handing
each processed point to context factories that expose only what has actually been applied so
far -- never the full future input array.
"""
from __future__ import annotations

from collections.abc import Sequence

from rsys.data.bar_builder import BarSnapshot, TradeBarBuilder
from rsys.data.bar_rules import BarRuleSet
from rsys.data.bar_version_store import BarVersionStore
from rsys.data.events import (
    MarketEvent,
    ProviderBarEvent,
    QuoteEvent,
    TimerEvent,
    TradeCorrectionEvent,
    TradeEvent,
)
from rsys.data.quotes import QuoteFreshnessPolicy, QuoteState
from rsys.replay import Phase, ReplayProfile, ReplayStream, event_phase
from rsys.views import ClosedOnlyContext, IntrabarContext, ReplayView


class NoAuthoritativeBuilderError(Exception):
    """A trade/correction arrived for a series this router was not configured to build
    (bars.authority must name exactly one source per series; this router only owns builders
    for feeds/instruments it was told to build)."""


class SeriesRouter:
    """Owns one `TradeBarBuilder` (+ its `BarVersionStore`) and, if enabled, one `QuoteState`
    per `(instrument_id, feed)` key. A single declared `interval` and `rule_set_id`-backed
    `rules` apply to every series this router manages (matches the current single-authority,
    single-interval replay settings in `ReplayConfig`)."""

    def __init__(
        self,
        feed: str,
        interval: str,
        rules: BarRuleSet,
        *,
        retention_limit: int | None = None,
        quote_freshness_policy: QuoteFreshnessPolicy | None = None,
    ):
        self.feed = feed
        self.interval = interval
        self._rules = rules
        self._retention_limit = retention_limit
        self._quote_freshness_policy = quote_freshness_policy
        self._builders: dict[str, TradeBarBuilder] = {}
        self._version_stores: dict[str, BarVersionStore] = {}
        self._quotes: dict[str, QuoteState] = {}

    def _builder_for(self, instrument_id: str) -> TradeBarBuilder:
        if instrument_id not in self._builders:
            store = BarVersionStore()
            self._version_stores[instrument_id] = store
            self._builders[instrument_id] = TradeBarBuilder(
                instrument_id, self.feed, self.interval, self._rules,
                retention_limit=self._retention_limit, version_store=store,
            )
        return self._builders[instrument_id]

    def version_store_for(self, instrument_id: str) -> BarVersionStore:
        self._builder_for(instrument_id)
        return self._version_stores[instrument_id]

    def get_builder(self, instrument_id: str) -> TradeBarBuilder | None:
        """The existing builder for `instrument_id`, or `None` if nothing has been routed to it
        yet -- never implicitly creates one (unlike `_builder_for`, used only internally)."""
        return self._builders.get(instrument_id)

    def apply_trade(self, event: TradeEvent) -> BarSnapshot:
        if event.feed != self.feed:
            raise NoAuthoritativeBuilderError(f"router owns feed {self.feed!r}, not {event.feed!r}")
        return self._builder_for(event.instrument_id).on_trade(event)

    def apply_correction(self, event: TradeCorrectionEvent) -> BarSnapshot:
        if event.feed != self.feed:
            raise NoAuthoritativeBuilderError(f"router owns feed {self.feed!r}, not {event.feed!r}")
        return self._builder_for(event.instrument_id).on_correction(event)

    def apply_quote(self, event: QuoteEvent) -> None:
        if self._quote_freshness_policy is None:
            return  # quotes disabled for this router; silently ignoring is explicit, declared behavior
        if event.instrument_id not in self._quotes:
            self._quotes[event.instrument_id] = QuoteState(event.instrument_id, self.feed, self._quote_freshness_policy)
        self._quotes[event.instrument_id].apply(event)

    def advance_clock(self, now_ns: int) -> dict[str, list[BarSnapshot]]:
        return {instrument_id: b.advance_clock(now_ns) for instrument_id, b in self._builders.items()}

    def forming_bar(self, instrument_id: str, as_of_ns: int) -> BarSnapshot | None:
        builder = self._builders.get(instrument_id)
        if builder is None:
            return None
        return builder.forming_bar(as_of_ns)

    def quote_state_for(self, instrument_id: str) -> QuoteState | None:
        return self._quotes.get(instrument_id)


class ReplayRunner:
    """Validates nothing by itself (the resolved `ReplayConfig` is validated before
    construction); this class schedules/dispatches a known, finite event collection and keeps
    the router's mutable state advancing strictly in dispatch order, so every `forming_bar`/
    `latest_trade`/`latest_quote` query it exposes is only ever answered from events already
    processed -- never from the full future input array.
    """

    def __init__(self, events: Sequence[MarketEvent], profile: ReplayProfile, router: SeriesRouter | None = None):
        self._stream = ReplayStream(events, profile)
        self._profile = profile
        self._router = router
        self.processed: list[MarketEvent] = []
        # Parallel to `processed`: the BarSnapshot directly RETURNED by applying that one event
        # (trade/correction), or the per-instrument newly-closed-snapshot dict for a timer, or
        # `None` -- captured at dispatch time, never recomputed afterward from "current" state
        # (that recomputation is exactly the C06 bug this whole module exists to avoid).
        self.results: list[BarSnapshot | dict[str, list[BarSnapshot]] | None] = []

    def run_to_completion(self) -> list[MarketEvent]:
        """Dispatch every event, applying trades/corrections/quotes to the router (if any) and
        advancing its clock on every timer/scheduled-closure event. Returns the full processed
        order (for building a `ReplayView` over completed-bar/trade history after the fact)."""
        for event in self._stream:
            self._dispatch_one(event)
        return self.processed

    def _dispatch_one(self, event: MarketEvent) -> None:
        self.processed.append(event)
        result: BarSnapshot | dict[str, list[BarSnapshot]] | None = None
        if self._router is not None:
            if isinstance(event, TradeEvent):
                result = self._router.apply_trade(event)
            elif isinstance(event, TradeCorrectionEvent):
                result = self._router.apply_correction(event)
            elif isinstance(event, QuoteEvent):
                self._router.apply_quote(event)
            elif isinstance(event, TimerEvent):
                result = self._router.advance_clock(event.event_time_ns)
            elif isinstance(event, ProviderBarEvent):
                pass  # provider bars are a comparison/authority stream, not this router's input
        self.results.append(result)

    def context_after(self, as_of_ns: int, *, intrabar: bool) -> ClosedOnlyContext | IntrabarContext:
        """Build a context frozen at `as_of_ns`, using only events processed SO FAR (causality
        depends on calling this only after `run_through` has reached exactly this point, never
        after `run_to_completion`)."""
        view = ReplayView(self.processed, router=self._router)
        context_type = IntrabarContext if intrabar else ClosedOnlyContext
        return context_type(view, as_of_ns)

    def run_through(self, as_of_ns: int) -> None:
        """Dispatch every remaining event whose own `(available_at_ns, phase)` key is at or
        before the key corresponding to `as_of_ns`'s last phase, i.e. every event available at
        or before `as_of_ns`. Safe to call repeatedly with increasing cutoffs."""
        while True:
            upcoming = self._stream.peek()
            if upcoming is None or upcoming.available_at_ns > as_of_ns:
                return
            self._dispatch_one(next(self._stream))


def same_time_availability_points(ordered_events: Sequence[MarketEvent], profile: ReplayProfile) -> list[int]:
    """Every distinct `available_at_ns` at which a phase-40 observation could occur, in
    ascending order -- the declared availability-batch cadence (7.3): one evaluation per
    instant after all same-time inputs/derived publications at that instant, not per-trade."""
    seen: set[int] = set()
    points: list[int] = []
    for event in ordered_events:
        if (event_phase(event, profile) in (Phase.MARKET_DATA_APPLY, Phase.BAR_UPDATE_PUBLISH, Phase.BAR_PUBLISH)
                and event.available_at_ns not in seen):
            seen.add(event.available_at_ns)
            points.append(event.available_at_ns)
    return sorted(points)

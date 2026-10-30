"""Minimal R3-B integration structure: a series-identity router and a replay runner.

These are responsibility suggestions made concrete, not a framework. The router owns
per-series mutable builder/quote state (provider normalization and account mutation stay
outside it). The runner schedules events, advances the clock and dispatches phases, handing
each processed point to context factories that expose only what has actually been applied so
far -- never the full future input array.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Protocol, runtime_checkable

from rsys.data.bar_builder import TradeBarBuilder
from rsys.data.bar_rules import BarRuleSet
from rsys.data.bar_version_store import BarVersionStore
from rsys.data.keys import cutoff_key
from rsys.data.models import (
    BarCloseEvent,
    BarSnapshot,
    BarVersion,
    CorporateActionEvent,
    MarketEvent,
    ProviderBarEvent,
    QualityState,
    QuoteEvent,
    QuoteFreshnessPolicy,
    QuoteSnapshot,
    ReplayKey,
    SnapshotStatus,
    TimerEvent,
    TradeCorrectionEvent,
    TradeEvent,
)
from rsys.data.quotes import QuoteState
from rsys.data.timestamps import INTERVAL_DURATIONS_NS
from rsys.replay.clock import ReplayStream, event_key, event_phase
from rsys.replay.errors import NoAuthoritativeBuilderError
from rsys.replay.models import ObservationRecord, Phase, ReplayProfile
from rsys.replay.views import ClosedOnlyContext, IntrabarContext, ReplayView


@runtime_checkable
class Observer(Protocol):
    """A declared, separately-identified synthetic observer (IR-03): the runner invokes
    `decide` itself at the observer's own cadence, with a context already frozen at the
    decision key -- an observer never drives its own scheduling or reaches past its context."""

    name: str
    mode: str  # "intrabar" (forming-bar access) | "closed_only" (completed bars only)

    def decide(
        self, context: ClosedOnlyContext | IntrabarContext, instrument_id: str
    ) -> object | None: ...


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
        # Propagated to any builder created after the watermark has already advanced, so a
        # first-seen instrument's first late trade is not mistaken for currently forming
        # (IR-03/R3I-12).
        self._watermark_ns = 0
        self._series_sources: dict[str, tuple[str, str]] = {}

    def _builder_for(self, instrument_id: str) -> TradeBarBuilder:
        if instrument_id not in self._builders:
            store = BarVersionStore()
            self._version_stores[instrument_id] = store
            self._builders[instrument_id] = TradeBarBuilder(
                instrument_id,
                self.feed,
                self.interval,
                self._rules,
                retention_limit=self._retention_limit,
                version_store=store,
                initial_watermark_ns=self._watermark_ns,
            )
        return self._builders[instrument_id]

    def version_store_for(self, instrument_id: str) -> BarVersionStore:
        self._builder_for(instrument_id)
        return self._version_stores[instrument_id]

    def get_builder(self, instrument_id: str) -> TradeBarBuilder | None:
        """The existing builder for `instrument_id`, or `None` if nothing has been routed to it
        yet -- never implicitly creates one (unlike `_builder_for`, used only internally)."""
        return self._builders.get(instrument_id)

    @property
    def quote_policy(self):
        return self._quote_freshness_policy

    def frozen_versions(self) -> tuple[BarVersion, ...]:
        return tuple(
            v
            for instrument in sorted(self._version_stores)
            for v in self._version_stores[instrument].frozen_versions()
        )

    def observe_clock(self, now_ns: int) -> None:
        self._watermark_ns = max(self._watermark_ns, now_ns)
        for builder in self._builders.values():
            builder.observe_clock(now_ns)

    def _check_source(self, event):
        if event.feed != self.feed:
            raise NoAuthoritativeBuilderError("event feed does not match router feed")
        source = (event.provider, event.session_version)
        previous = self._series_sources.get(event.instrument_id)
        if previous is not None and previous != source:
            raise NoAuthoritativeBuilderError(
                "provider/session identity changed within a series"
            )
        if event.quality_state != QualityState.OK:
            raise ValueError("non-OK input cannot mutate a trade/action series")
        return source

    def apply_trade(self, event: TradeEvent) -> BarSnapshot:
        source = self._check_source(event)
        self.observe_clock(event.available_at_ns)
        result = self._builder_for(event.instrument_id).on_trade(event)
        self._series_sources[event.instrument_id] = source
        return result

    def apply_correction(self, event: TradeCorrectionEvent) -> BarSnapshot:
        self._check_source(event)
        self.observe_clock(event.available_at_ns)
        return self._builder_for(event.instrument_id).on_correction(event)

    def apply_quote(self, event: QuoteEvent) -> QuoteSnapshot | None:
        if self._quote_freshness_policy is None:
            return None
        if event.instrument_id not in self._quotes:
            self._quotes[event.instrument_id] = QuoteState(
                event.instrument_id, self.feed, self._quote_freshness_policy
            )
        return self._quotes[event.instrument_id].apply(event)

    def advance_clock(
        self, now_ns: int, *, publication_phase: int = 20
    ) -> dict[str, list[BarSnapshot]]:
        self.observe_clock(now_ns)
        return {
            instrument_id: b.advance_clock(now_ns, publication_phase=publication_phase)
            for instrument_id, b in sorted(self._builders.items())
        }

    def apply_action(
        self, event: CorporateActionEvent
    ) -> dict[str, BarSnapshot | None]:
        source = self._check_source(event)
        self.observe_clock(event.available_at_ns)
        # Keep the action even before this instrument's first trade. The effective
        # interval identifies affected units; availability determines visibility.
        builder = self._builder_for(event.instrument_id)
        result = builder.invalidate_if_straddled(
            event.effective_at_ns,
            available_at_ns=event.available_at_ns,
            reason=f"unsupported split-straddled interval ({event.event_id})",
            action_id=event.event_id,
        )
        self._series_sources[event.instrument_id] = source
        return {event.instrument_id: result}

    def forming_bar(
        self, instrument_id: str, as_of_ns: int | ReplayKey
    ) -> BarVersion | None:
        """An IMMUTABLE as-of forming-bar query (IR-02): answers only from already-published
        versions in the per-instrument `BarVersionStore`, never from live mutable builder state
        -- a context bound to an earlier `as_of_ns` stays correct forever, even after later
        trades, corrections or closures mutate the live builder."""
        store = self._version_stores.get(instrument_id)
        if store is None:
            return None
        duration_ns = INTERVAL_DURATIONS_NS[self.interval]
        bucket_start = (
            cutoff_key(as_of_ns).available_at_ns // duration_ns
        ) * duration_ns
        identity = (instrument_id, self.feed, self.interval, bucket_start)
        version = store.as_of(identity, as_of_ns)
        if version is None or version.status != SnapshotStatus.FORMING:
            return None
        return version

    def closed_bars(self, instrument_id: str, as_of_ns: int) -> list[BarVersion]:
        """The authoritative trade-built completed-interval history for `instrument_id`, as of
        `as_of_ns` (IR-02): every bucket whose selected as-of version had already closed by then,
        sourced only from the immutable version store."""
        store = self._version_stores.get(instrument_id)
        if store is None:
            return []
        results: list[BarVersion] = []
        for identity in store.bucket_identities():
            if identity[0] != instrument_id:
                continue
            version = store.as_of(identity, as_of_ns)
            if version is not None and version.status in (
                SnapshotStatus.CLOSED_AS_OF,
                SnapshotStatus.REVISED,
            ):
                results.append(version)
        return sorted(results, key=lambda v: v.bar_start_ns)

    def quote_state_for(self, instrument_id: str) -> QuoteState | None:
        return self._quotes.get(instrument_id)


class ReplayRunner:
    """Finite offline replay. Internal closures are scheduled independently of ticks.

    CLI callers declare a window; lower-level callers may use the observed availability
    range for scheduling only. Neither mode infers input completeness from a clock event.
    """

    def __init__(
        self,
        events: Sequence[MarketEvent],
        profile: ReplayProfile,
        router: SeriesRouter | None = None,
        *,
        authority: str = "provider_publication",
        quotes_enabled: bool = False,
        window_start_ns: int | None = None,
        window_end_ns: int | None = None,
    ):
        self._profile = profile
        self._router = router
        self._authority = authority
        self._quotes_enabled = quotes_enabled
        if quotes_enabled and (router is None or router.quote_policy is None):
            raise ValueError("enabled quotes require an explicit freshness policy")
        items = list(events)
        if router is not None and authority == "trade_builder":
            duration = INTERVAL_DURATIONS_NS[router.interval]
            start = (
                window_start_ns
                if window_start_ns is not None
                else min((e.available_at_ns for e in items), default=0)
            )
            end = (
                window_end_ns
                if window_end_ns is not None
                else max((e.available_at_ns for e in items), default=start)
            )
            if end < start:
                raise ValueError("replay window end precedes start")
            session_version = next((e.session_version for e in items), "nyse-2024-v1")
            for ns in range((start // duration + 1) * duration, end + 1, duration):
                items.append(
                    BarCloseEvent(
                        event_id=f"__bar_close__:{ns}",
                        instrument_id="__clock__",
                        provider="rsys",
                        feed=router.feed,
                        event_time_ns=ns,
                        available_at_ns=ns,
                        event_time_meaning="scheduled_boundary",
                        session_version=session_version,
                        calendar_version=session_version,
                        quality_state="ok",
                        timing_label="modeled",
                    )
                )
        self._stream = ReplayStream(items, profile)
        self.processed: list[MarketEvent] = []
        self.results: list = []
        self.evaluations: list[ObservationRecord] = []
        self._provider_revisions: dict[tuple, dict] = {}

    @staticmethod
    def _provider_revision_key(event: ProviderBarEvent) -> tuple:
        return (
            event.provider,
            event.feed,
            event.instrument_id,
            event.session_version,
            event.interval,
            event.bar_start_ns,
            event.bar_end_ns,
            event.bar_revision,
        )

    def _dispatch_one(self, event: MarketEvent) -> None:
        result = None
        if isinstance(event, ProviderBarEvent):
            identity = self._provider_revision_key(event)
            payload = event.model_dump(
                exclude={
                    "event_id",
                    "available_at_ns",
                    "received_at_ns",
                    "source_sequence",
                }
            )
            previous = self._provider_revisions.get(identity)
            if previous is not None and previous != payload:
                raise ValueError(
                    "conflicting provider publication for the same bar revision"
                )
            self._provider_revisions[identity] = payload
        if self._router is not None:
            self._router.observe_clock(event.available_at_ns)
            if isinstance(event, TradeEvent):
                result = self._router.apply_trade(event)
            elif isinstance(event, TradeCorrectionEvent):
                result = self._router.apply_correction(event)
            elif isinstance(event, QuoteEvent):
                result = self._router.apply_quote(event)
            elif isinstance(event, BarCloseEvent):
                result = self._router.advance_clock(
                    event.available_at_ns, publication_phase=20
                )
            elif (
                isinstance(event, TimerEvent) and event.quality_state == QualityState.OK
            ):
                result = self._router.advance_clock(
                    event.available_at_ns, publication_phase=37
                )
            elif isinstance(event, CorporateActionEvent):
                result = self._router.apply_action(event)
        # Failed application must never be recorded as accepted input.
        self.processed.append(event)
        self.results.append(result)

    def domain_engine(self, handler):
        """Bind synthetic execution to this runner's source clock and frozen contexts.

        Source and domain events share ReplayKey and one forward-only phase queue.
        The caller owns domain policy; only this runner applies market publications.
        Create once, before dispatch. The returned engine accepts later-phase timers.
        """
        from rsys.replay.engine import PhaseEngine
        from rsys.replay.models import PhaseEvent

        if self.processed or getattr(self, "_domain_bound", False):
            raise ValueError(
                "domain engine must bind a fresh replay runner exactly once"
            )
        self._domain_bound = True

        def dispatch(event):
            if event.kind == "replay_source":
                self._dispatch_one(event.payload["source"])
            return handler(event)

        engine = PhaseEngine(on_dispatch=dispatch)
        for source in self._stream:
            key = event_key(source, self._profile)
            engine.enqueue(
                PhaseEvent(
                    f"source:{source.event_id}",
                    key.available_at_ns,
                    key.phase,
                    "replay_source",
                    source.instrument_id,
                    {"source": source},
                    key.sequence,
                    key.tie,
                )
            )
        return engine

    def run_to_completion(self) -> list[MarketEvent]:
        for event in self._stream:
            self._dispatch_one(event)
        return self.processed

    def run_through_key(self, cutoff: ReplayKey) -> None:
        while (upcoming := self._stream.peek()) is not None and event_key(
            upcoming, self._profile
        ) <= cutoff:
            self._dispatch_one(next(self._stream))

    def run_through(self, as_of_ns: int) -> None:
        self.run_through_key(ReplayKey.end_of_time(as_of_ns))

    def context_after(self, as_of_ns: int | ReplayKey, *, intrabar: bool):
        view = ReplayView(
            self.processed,
            router=self._router,
            authority=self._authority,
            quotes_enabled=self._quotes_enabled,
            profile=self._profile,
        )
        return (IntrabarContext if intrabar else ClosedOnlyContext)(view, as_of_ns)

    def run_with_observers(
        self, observers: Sequence[Observer], instrument_ids: Sequence[str]
    ) -> list[ObservationRecord]:
        """Invoke observers after complete same-time batches, at the declared cadence.

        Return emitted decisions for compatibility; evaluations additionally retain None
        results and input-version evidence. Revisions trigger callbacks only by explicit opt-in.
        """
        for observer in observers:
            if observer.mode not in ("intrabar", "closed_only"):
                raise ValueError("unsupported observer mode")
            cadence = getattr(
                observer,
                "cadence",
                "completed_bar_boundary"
                if observer.mode == "closed_only"
                else "availability_batch",
            )
            if cadence not in ("completed_bar_boundary", "availability_batch"):
                raise ValueError("unsupported observer cadence")
            if observer.mode == "closed_only" and cadence != "completed_bar_boundary":
                raise ValueError("closed-only observers require completed-bar cadence")
        records = []
        while (upcoming := self._stream.peek()) is not None:
            point = upcoming.available_at_ns
            start_index = len(self.processed)
            previous_provider_revisions = set(self._provider_revisions)
            previous_versions = (
                {
                    (v.instrument_id, v.publication_key)
                    for v in self._router.frozen_versions()
                }
                if self._router
                else set()
            )
            self.run_through(point)
            batch = self.processed[start_index:]
            closed = set()
            revised = set()
            for event in batch:
                if (
                    self._authority == "provider_publication"
                    and isinstance(event, ProviderBarEvent)
                    and event.quality_state == QualityState.OK
                    and self._provider_revision_key(event)
                    not in previous_provider_revisions
                ):
                    (closed if event.bar_revision == 1 else revised).add(
                        event.instrument_id
                    )
            if self._authority == "trade_builder" and self._router:
                for version in self._router.frozen_versions():
                    if (
                        version.instrument_id,
                        version.publication_key,
                    ) in previous_versions:
                        continue
                    if version.status == SnapshotStatus.CLOSED_AS_OF:
                        closed.add(version.instrument_id)
                    elif version.status == SnapshotStatus.REVISED:
                        revised.add(version.instrument_id)
            # No source event currently dispatches after phase 37. The context key still
            # explicitly bounds publications at phase 40 for subsequent execution work.
            key = ReplayKey(point, int(Phase.SCAN_SIGNAL), 2**63 - 1)
            for observer in observers:
                cadence = getattr(
                    observer,
                    "cadence",
                    "completed_bar_boundary"
                    if observer.mode == "closed_only"
                    else "availability_batch",
                )
                eligible = (
                    set(instrument_ids) if cadence == "availability_batch" else closed
                )
                if getattr(observer, "on_revisions", False):
                    eligible = eligible | revised
                for instrument_id in sorted(set(instrument_ids) & eligible):
                    context = self.context_after(
                        key, intrabar=observer.mode == "intrabar"
                    )
                    result = deepcopy(observer.decide(context, instrument_id))
                    record = ObservationRecord(
                        observer.name,
                        instrument_id,
                        point,
                        observer.mode,
                        result,
                        context.input_references(),
                        cadence,
                    )
                    self.evaluations.append(record)
                    if result is not None:
                        records.append(record)
        return records


def same_time_availability_points(
    ordered_events: Sequence[MarketEvent], profile: ReplayProfile
) -> list[int]:
    return sorted(
        {
            e.available_at_ns
            for e in ordered_events
            if event_phase(e, profile)
            in (
                Phase.MARKET_DATA_APPLY,
                Phase.BAR_UPDATE_PUBLISH,
                Phase.BAR_PUBLISH,
                Phase.TIMER,
            )
        }
    )

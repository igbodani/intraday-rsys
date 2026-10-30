"""Frozen event/publication collections with explicit availability cutoffs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from rsys.data.keys import cutoff_key
from rsys.data.models import (
    CorporateActionEvent,
    MarketEvent,
    ProviderBarEvent,
    QualityState,
    QuoteCondition,
    QuoteEvent,
    QuoteFreshnessPolicy,
    ReplayKey,
    TradeEvent,
    classify_quote_condition,
)
from rsys.data.timestamps import INTERVAL_DURATIONS_NS
from rsys.replay.clock import event_key
from rsys.replay.models import QuoteObservation, ReplayProfile

if TYPE_CHECKING:
    from rsys.replay.runtime import SeriesRouter


class ReplayView:
    """Copy immutable references at construction; never retain a mutable router/builder.

    Timestamp cutoffs mean the end of that timestamp. ReplayKey additionally bounds
    phases and sequence. Production contexts use the runner's completed batch at phase 40.
    """

    def __init__(
        self,
        events: Sequence[MarketEvent],
        *,
        router: SeriesRouter | None = None,
        authority: str = "provider_publication",
        quotes_enabled: bool = True,
        profile: ReplayProfile = ReplayProfile.TRADE_REPLAY_V1,
        quote_freshness_policy: QuoteFreshnessPolicy | None = None,
    ):
        if authority not in ("provider_publication", "trade_builder"):
            raise ValueError("unsupported bars authority")
        self._events = tuple(events)
        self._profile = profile
        self._authority = authority
        self._quotes_enabled = quotes_enabled
        self._wired = router is not None
        self._versions = router.frozen_versions() if router else ()
        self._interval = router.interval if router else None
        self._quote_policy = quote_freshness_policy or (
            router.quote_policy if router else None
        )

    def _visible(self, event, cutoff):
        return event_key(event, self._profile) <= cutoff_key(cutoff)

    def _selected_versions(self, instrument_id, cutoff):
        selected = {}
        for version in self._versions:
            if (
                version.instrument_id != instrument_id
                or version.publication_key > cutoff_key(cutoff)
            ):
                continue
            identity = (
                version.instrument_id,
                version.feed,
                version.interval,
                version.bar_start_ns,
            )
            current = selected.get(identity)
            if current is None or version.publication_key > current.publication_key:
                selected[identity] = version
        return selected.values()

    def closed_bars(self, instrument_id: str, as_of_ns: int | ReplayKey) -> list:
        if self._authority == "trade_builder":
            if not self._wired:
                raise NotImplementedError(
                    "trade_builder authority requires a wired SeriesRouter"
                )
            return sorted(
                (
                    v
                    for v in self._selected_versions(instrument_id, as_of_ns)
                    if v.status in ("closed_as_of", "revised")
                ),
                key=lambda v: v.bar_start_ns,
            )
        selected = {}
        for event in self._events:
            if (
                isinstance(event, ProviderBarEvent)
                and event.instrument_id == instrument_id
                and self._visible(event, as_of_ns)
            ):
                identity = (
                    event.provider,
                    event.feed,
                    event.interval,
                    event.bar_start_ns,
                    event.bar_end_ns,
                )
                current = selected.get(identity)
                if current is None or (
                    event.bar_revision,
                    event_key(event, self._profile),
                ) >= (current.bar_revision, event_key(current, self._profile)):
                    selected[identity] = event
        actions = [
            e
            for e in self._events
            if isinstance(e, CorporateActionEvent)
            and e.instrument_id == instrument_id
            and self._visible(e, as_of_ns)
        ]
        return sorted(
            (
                e
                for e in selected.values()
                if e.quality_state == QualityState.OK
                and not any(
                    e.bar_start_ns < a.effective_at_ns < e.bar_end_ns for a in actions
                )
            ),
            key=lambda e: (e.bar_start_ns, e.provider, e.feed, e.interval),
        )

    def latest_trade(
        self, instrument_id: str, as_of_ns: int | ReplayKey
    ) -> TradeEvent | None:
        candidates = [
            e
            for e in self._events
            if isinstance(e, TradeEvent)
            and e.instrument_id == instrument_id
            and self._visible(e, as_of_ns)
            and e.quality_state == QualityState.OK
        ]
        return candidates[-1] if candidates else None

    def quote_observation(
        self, instrument_id: str, as_of_ns: int | ReplayKey
    ) -> QuoteObservation:
        if not self._quotes_enabled:
            return QuoteObservation(None, "disabled")
        candidates = [
            e
            for e in self._events
            if isinstance(e, QuoteEvent)
            and e.instrument_id == instrument_id
            and self._visible(e, as_of_ns)
        ]
        if not candidates:
            return QuoteObservation(None, "unavailable")
        event = candidates[-1]
        if event.quality_state != QualityState.OK:
            return QuoteObservation(event, "invalid")
        if self._quote_policy is not None:
            age = cutoff_key(as_of_ns).available_at_ns - event.available_at_ns
            if age > self._quote_policy.freshness_limit_ms * 1_000_000:
                return QuoteObservation(event, "stale")
        condition = classify_quote_condition(event.bid_price, event.ask_price)
        if condition != QuoteCondition.NORMAL:
            return QuoteObservation(event, condition.value)
        return QuoteObservation(event, "usable")

    def latest_quote(
        self, instrument_id: str, as_of_ns: int | ReplayKey
    ) -> QuoteEvent | None:
        quote = self.quote_observation(instrument_id, as_of_ns)
        if quote.state == "disabled":
            raise NotImplementedError(
                "quotes are disabled for this replay (quotes.enabled=false)"
            )
        return quote.event if quote.state == "usable" else None

    def forming_bar(self, instrument_id: str, as_of_ns: int | ReplayKey):
        if not self._wired:
            raise NotImplementedError("forming_bar requires a wired SeriesRouter")
        duration = INTERVAL_DURATIONS_NS[self._interval]
        start = (cutoff_key(as_of_ns).available_at_ns // duration) * duration
        return next(
            (
                v
                for v in self._selected_versions(instrument_id, as_of_ns)
                if v.bar_start_ns == start and v.status == "forming"
            ),
            None,
        )


class ClosedOnlyContext:
    """Completed-bar capability; retains immutable observation evidence for this cutoff."""

    def __init__(self, view: ReplayView, as_of_ns: int | ReplayKey):
        self._view = view
        self._as_of_ns = cutoff_key(as_of_ns)
        self._references: set[str] = set()

    def _remember(self, item):
        if item is not None:
            if isinstance(item, MarketEvent):
                ref = f"event:{item.provider}:{item.feed}:{item.instrument_id}:{item.event_id}"
            else:
                key = item.publication_key
                ref = (
                    f"bar:{item.instrument_id}:{item.feed}:{item.interval}:{item.bar_start_ns}:"
                    f"{key.available_at_ns}:{key.phase}:{key.sequence}"
                )
            self._references.add(ref)
        return item

    def input_references(self) -> tuple[str, ...]:
        return tuple(sorted(self._references))

    def closed_bars(self, instrument_id: str) -> list:
        return [
            self._remember(b)
            for b in self._view.closed_bars(instrument_id, self._as_of_ns)
        ]


class IntrabarContext(ClosedOnlyContext):
    def forming_bar(self, instrument_id: str):
        return self._remember(self._view.forming_bar(instrument_id, self._as_of_ns))

    def latest_trade(self, instrument_id: str):
        return self._remember(self._view.latest_trade(instrument_id, self._as_of_ns))

    def quote_observation(self, instrument_id: str) -> QuoteObservation:
        quote = self._view.quote_observation(instrument_id, self._as_of_ns)
        self._remember(quote.event)
        return quote

    def latest_quote(self, instrument_id: str):
        quote = self.quote_observation(instrument_id)
        if quote.state == "disabled":
            raise NotImplementedError(
                "quotes are disabled for this replay (quotes.enabled=false)"
            )
        return quote.event if quote.state == "usable" else None

"""Trade-driven incremental bar builder (R2).

Builds FORMING -> CLOSED_AS_OF -> REVISED bar snapshots from provider-rule-
classified trades. Every query recomputes OHLC/volume/VWAP from the live set
of active contributors (never patches incrementally), so correcting or
cancelling any contributor -- including the first/last/extreme one --
produces a correct result (BAR-007). Each returned `BarSnapshot` is an
immutable, freshly-built object; nothing about an earlier snapshot ever
changes after the fact (BAR-010).
"""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from rsys.data.actions import CorporateAction
from rsys.data.bar_rules import BarRuleSet, FieldEligibility, TradeClassificationContext
from rsys.data.bars import INTERVAL_DURATIONS_NS
from rsys.data.events import QualityState, TradeCorrectionEvent, TradeEvent

if TYPE_CHECKING:
    from rsys.data.bar_version_store import BarVersionStore


class SnapshotStatus(StrEnum):
    FORMING = "forming"
    CLOSED_AS_OF = "closed_as_of"
    REVISED = "revised"


class UnresolvedCorrectionError(Exception):
    """A correction/cancel references a trade_id this builder never accepted (or has evicted)."""


class ConflictingTradeError(Exception):
    """A second delivery under the same trade_id with a different payload, with no correction."""


class MismatchedSeriesError(Exception):
    """An event for a different instrument/feed was routed at this builder (C05): the builder
    independently rejects it before any mutation, regardless of what the caller believes."""


class RejectedQualityError(Exception):
    """An event whose quality_state is not 'ok' cannot mutate builder state (C05); suspect/
    invalid/unavailable observations are quarantined, never silently treated as valid."""


class EvictedBucketError(Exception):
    """The bucket's contributor detail was evicted under the retention policy; its effect on a
    late correction is explicitly unresolved, never silently applied or silently ignored."""


class UnsupportedSplitOverlapError(Exception):
    """A bucket interval straddles a split's dispatch moment, mixing old/new price units."""


def raise_if_bucket_straddles_action(bucket_start_ns: int, bucket_end_ns: int, action: CorporateAction) -> None:
    """A bucket whose interval straddles a split's effective moment cannot be finalized as a
    single consistent-unit bar; block it rather than mix pre/post-split contributions (BAR-012).
    """
    dispatch_ns = int(action.dispatch_at.timestamp() * 1_000_000_000)
    if bucket_start_ns < dispatch_ns < bucket_end_ns:
        raise UnsupportedSplitOverlapError(
            f"bucket [{bucket_start_ns}, {bucket_end_ns}) straddles action dispatch at {dispatch_ns}"
        )


class BarSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    feed: str
    interval: str
    bar_start_ns: int
    bar_end_ns: int
    available_at_ns: int
    status: SnapshotStatus
    revision: int  # number of post-closure revisions applied; 0 while FORMING or just closed
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal
    trade_count: int
    vwap_numerator: Decimal
    vwap_denominator: Decimal
    rule_set_id: str


class _Contributor:
    __slots__ = ("active", "eligibility", "event_time_ns", "price", "size", "trade_id")

    def __init__(self, trade_id: str, event_time_ns: int, price: Decimal, size: Decimal,
                 eligibility: FieldEligibility, active: bool = True):
        self.trade_id = trade_id
        self.event_time_ns = event_time_ns
        self.price = price
        self.size = size
        self.eligibility = eligibility
        self.active = active


class TradeBarBuilder:
    def __init__(
        self,
        instrument_id: str,
        feed: str,
        interval: str,
        rules: BarRuleSet,
        *,
        retention_limit: int | None = None,
        version_store: BarVersionStore | None = None,
    ):
        if interval not in INTERVAL_DURATIONS_NS:
            raise ValueError(f"no declared duration for interval {interval!r}")
        self.instrument_id = instrument_id
        self.feed = feed
        self.interval = interval
        self._duration_ns = INTERVAL_DURATIONS_NS[interval]
        self._rules = rules
        self._retention_limit = retention_limit
        self._version_store = version_store
        self._buckets: dict[int, dict[str, _Contributor]] = {}
        self._closed_order: list[int] = []  # closed bucket starts, oldest first
        self._closed: set[int] = set()
        self._post_close_revisions: dict[int, int] = {}
        self._evicted: set[int] = set()
        self._trade_id_bucket: dict[str, int] = {}  # survives eviction, unlike self._buckets

    def bucket_start_ns(self, event_time_ns: int) -> int:
        """Half-open `[start, start + duration)`; a trade exactly on a boundary belongs to the
        bucket it starts, not the one it closes (BAR-002)."""
        return (event_time_ns // self._duration_ns) * self._duration_ns

    def _check_series_and_quality(self, event: TradeEvent | TradeCorrectionEvent) -> None:
        """C05: the builder independently rejects a mismatched-series or non-OK-quality event
        before any mutation -- a wrong-instrument/feed or invalid/suspect event must never
        alter this series' state, regardless of what the caller believes it is routing."""
        if event.instrument_id != self.instrument_id or event.feed != self.feed:
            raise MismatchedSeriesError(
                f"event for ({event.instrument_id!r}, {event.feed!r}) cannot be applied to builder "
                f"for ({self.instrument_id!r}, {self.feed!r})"
            )
        if event.quality_state != QualityState.OK:
            raise RejectedQualityError(
                f"event {event.event_id!r} has quality_state {event.quality_state.value!r}; only "
                "'ok' events may mutate builder state (suspect/invalid/unavailable are quarantined)"
            )

    def on_trade(self, event: TradeEvent) -> BarSnapshot:
        self._check_series_and_quality(event)
        bucket_start = self.bucket_start_ns(event.event_time_ns)
        if bucket_start in self._evicted:
            raise EvictedBucketError(f"bucket {bucket_start} was evicted under the retention policy")
        eligibility = self._rules.classify(
            TradeClassificationContext(conditions=event.conditions, tape=event.tape, interval=self.interval)
        )
        contributors = self._buckets.setdefault(bucket_start, {})
        existing = contributors.get(event.trade_id)
        if existing is not None:
            if (existing.active and existing.price == event.price and existing.size == event.size
                    and existing.eligibility == eligibility):
                return self._snapshot(bucket_start, event.available_at_ns)  # idempotent redelivery (BAR-005)
            raise ConflictingTradeError(
                f"trade_id {event.trade_id!r} redelivered with a different payload; use a correction"
            )
        contributors[event.trade_id] = _Contributor(
            event.trade_id, event.event_time_ns, event.price, event.size, eligibility,
        )
        self._trade_id_bucket[event.trade_id] = bucket_start
        self._register_post_close_update(bucket_start)
        snapshot = self._snapshot(bucket_start, event.available_at_ns)
        self._publish_version(bucket_start, snapshot)
        return snapshot

    def on_correction(self, event: TradeCorrectionEvent) -> BarSnapshot:
        self._check_series_and_quality(event)
        bucket_start = self._find_bucket(event.original_trade_id)
        contributor = self._buckets[bucket_start][event.original_trade_id]
        if event.action == "cancel":
            contributor.active = False
        else:
            contributor.price = event.corrected_price
            contributor.size = event.corrected_size
            if event.conditions:  # C09: recompute eligibility only when conditions actually change
                contributor.eligibility = self._rules.classify(
                    TradeClassificationContext(conditions=event.conditions, tape=event.tape, interval=self.interval)
                )
        self._register_post_close_update(bucket_start)
        snapshot = self._snapshot(bucket_start, event.available_at_ns)
        self._publish_version(bucket_start, snapshot)
        return snapshot

    def on_cancel(self, event: TradeCorrectionEvent) -> BarSnapshot:
        return self.on_correction(event)

    def advance_clock(self, now_ns: int) -> list[BarSnapshot]:
        """Close every bucket whose end boundary has passed, independent of receiving a trade
        in it (BAR-009): a quiet bucket still gets its scheduled closure."""
        newly_closed: list[BarSnapshot] = []
        for bucket_start in sorted(self._buckets):
            bucket_end = bucket_start + self._duration_ns
            if bucket_end <= now_ns and bucket_start not in self._closed:
                self._closed.add(bucket_start)
                self._closed_order.append(bucket_start)
                snapshot = self._snapshot(bucket_start, now_ns)
                self._publish_version(bucket_start, snapshot)
                newly_closed.append(snapshot)
                self._enforce_retention()
        return newly_closed

    def snapshot(self, bucket_start_ns: int, as_of_ns: int) -> BarSnapshot:
        """The bucket's CURRENT (live) state, stamped with `as_of_ns` as its availability --
        this always reflects every change accepted so far, including ones that happened after
        `as_of_ns` in wall-clock terms. It is NOT a historical query and must never be treated
        as one (C06): use this builder's `BarVersionStore` (if wired) for an as-of lookup that
        only considers versions actually published by the cutoff."""
        return self._snapshot(bucket_start_ns, as_of_ns)

    def forming_bar(self, as_of_ns: int) -> BarSnapshot | None:
        """The CURRENT forming bar containing `as_of_ns` -- `None` if that interval has
        already closed (an old interval revised now remains a revised COMPLETED interval, never
        the current forming bar; see `closed_bars`/the version store for that) or if nothing has
        been accepted into it yet (never a fabricated empty bar).

        Correct causality depends entirely on the CALLER: this reflects every trade/correction
        already applied to this builder, so it must only be queried after processing events
        through `as_of_ns` and no further -- never after processing the whole input stream."""
        bucket_start = self.bucket_start_ns(as_of_ns)
        if bucket_start in self._closed or bucket_start not in self._buckets:
            return None
        return self._snapshot(bucket_start, as_of_ns)

    def _find_bucket(self, trade_id: str) -> int:
        bucket_start = self._trade_id_bucket.get(trade_id)
        if bucket_start is None:
            raise UnresolvedCorrectionError(f"no accepted trade with trade_id {trade_id!r}")
        if bucket_start in self._evicted:
            raise EvictedBucketError(f"original trade {trade_id!r}'s bucket was evicted")
        return bucket_start

    def _register_post_close_update(self, bucket_start: int) -> None:
        if bucket_start in self._closed:
            self._post_close_revisions[bucket_start] = self._post_close_revisions.get(bucket_start, 0) + 1

    def _publish_version(self, bucket_start: int, snapshot: BarSnapshot) -> None:
        if self._version_store is None:
            return
        source_event_ids = tuple(sorted(c.trade_id for c in self._buckets.get(bucket_start, {}).values() if c.active))
        self._version_store.publish(snapshot, source_event_ids=source_event_ids)

    def _enforce_retention(self) -> None:
        if self._retention_limit is None:
            return
        while len(self._closed_order) > self._retention_limit:
            oldest = self._closed_order.pop(0)
            self._buckets.pop(oldest, None)
            self._evicted.add(oldest)

    def _snapshot(self, bucket_start: int, available_at_ns: int) -> BarSnapshot:
        contributors = [c for c in self._buckets.get(bucket_start, {}).values() if c.active]
        is_closed = bucket_start in self._closed
        revision = self._post_close_revisions.get(bucket_start, 0) if is_closed else 0
        status = SnapshotStatus.FORMING if not is_closed else (
            SnapshotStatus.REVISED if revision > 0 else SnapshotStatus.CLOSED_AS_OF
        )

        # C09/C10: a declared total order -- (event_time_ns, trade_id) -- so ties never depend
        # on accidental dict/list iteration order; open/close/high/low are each computed from
        # their OWN eligible subset (C10: a single combined `price` flag cannot describe every
        # provider rule, e.g. a late-reported print eligible for close but not high/low).
        def _ordered(eligible: list[_Contributor]) -> list[_Contributor]:
            return sorted(eligible, key=lambda c: (c.event_time_ns, c.trade_id))

        open_contributors = _ordered([c for c in contributors if c.eligibility.open])
        close_contributors = _ordered([c for c in contributors if c.eligibility.close])
        high_contributors = [c for c in contributors if c.eligibility.high]
        low_contributors = [c for c in contributors if c.eligibility.low]

        open_ = open_contributors[0].price if open_contributors else None
        close = close_contributors[-1].price if close_contributors else None
        high = max((c.price for c in high_contributors), default=None)
        low = min((c.price for c in low_contributors), default=None)

        volume = sum((c.size for c in contributors if c.eligibility.volume), start=Decimal(0))
        trade_count = sum(1 for c in contributors if c.eligibility.count)

        vwap_contributors = [c for c in contributors if c.eligibility.vwap]
        vwap_numerator = sum((c.price * c.size for c in vwap_contributors), start=Decimal(0))
        vwap_denominator = sum((c.size for c in vwap_contributors), start=Decimal(0))


        return BarSnapshot(
            instrument_id=self.instrument_id, feed=self.feed, interval=self.interval,
            bar_start_ns=bucket_start, bar_end_ns=bucket_start + self._duration_ns,
            available_at_ns=available_at_ns, status=status, revision=revision,
            open=open_, high=high, low=low, close=close, volume=volume, trade_count=trade_count,
            vwap_numerator=vwap_numerator, vwap_denominator=vwap_denominator, rule_set_id=self._rules.rule_set_id,
        )

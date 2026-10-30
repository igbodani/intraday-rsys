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
from typing import TYPE_CHECKING

from rsys.data.bar_rules import BarRuleSet
from rsys.data.errors import (
    ConflictingTradeError,
    EvictedBucketError,
    MismatchedSeriesError,
    RejectedQualityError,
    UnresolvedCorrectionError,
    UnsupportedSplitOverlapError,
)
from rsys.data.models import (
    BarSnapshot,
    CorporateAction,
    QualityState,
    SnapshotStatus,
    TradeClassificationContext,
    TradeCorrectionEvent,
    TradeEvent,
    _Contributor,
)
from rsys.data.timestamps import INTERVAL_DURATIONS_NS

if TYPE_CHECKING:
    from rsys.data.bar_version_store import BarVersionStore


def raise_if_bucket_straddles_action(
    bucket_start_ns: int, bucket_end_ns: int, action: CorporateAction
) -> None:
    """A bucket whose interval straddles a split's effective moment cannot be finalized as a
    single consistent-unit bar; block it rather than mix pre/post-split contributions (BAR-012).
    """
    dispatch_ns = int(action.dispatch_at.timestamp() * 1_000_000_000)
    if bucket_start_ns < dispatch_ns < bucket_end_ns:
        raise UnsupportedSplitOverlapError(
            f"bucket [{bucket_start_ns}, {bucket_end_ns}) straddles action dispatch at {dispatch_ns}"
        )


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
        initial_watermark_ns: int = 0,
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
        # A monotonic processed watermark (IR-03/R3I-12): a bucket whose end has already
        # elapsed by the time its FIRST contributor arrives must close immediately, not sit as
        # a currently-forming bucket merely because this builder is new to it.
        self._watermark_ns = initial_watermark_ns
        self._last_publication_ns = 0
        self._actions: dict[str, tuple[int, int, str]] = {}
        self._trade_payloads: dict[str, tuple] = {}
        self._source_identity: tuple[str, str] | None = None
        self._buckets: dict[int, dict[str, _Contributor]] = {}
        self._closed_order: list[int] = []  # closed bucket starts, oldest first
        self._closed: set[int] = set()
        self._post_close_revisions: dict[int, int] = {}
        self._evicted: set[int] = set()
        self._trade_id_bucket: dict[
            str, int
        ] = {}  # survives eviction, unlike self._buckets
        # (original_trade_id, action, price, size, conditions) keyed by the correction's OWN
        # event_id, so a redelivered correction is detected as idempotent rather than counted as
        # a second revision (R3I-11), and a same-ID/different-payload redelivery is a conflict.
        self._applied_corrections: dict[str, tuple] = {}
        # bucket_start -> reason (IR-03/R3I-07): a bucket straddling a corporate action's
        # dispatch moment is explicitly invalidated, never silently treated as a normal bar.
        self._invalidated_buckets: dict[int, str] = {}

    def bucket_start_ns(self, event_time_ns: int) -> int:
        """Half-open `[start, start + duration)`; a trade exactly on a boundary belongs to the
        bucket it starts, not the one it closes (BAR-002)."""
        return (event_time_ns // self._duration_ns) * self._duration_ns

    def _check_series_and_quality(
        self, event: TradeEvent | TradeCorrectionEvent
    ) -> None:
        """C05: the builder independently rejects a mismatched-series or non-OK-quality event
        before any mutation -- a wrong-instrument/feed or invalid/suspect event must never
        alter this series' state, regardless of what the caller believes it is routing."""
        if event.instrument_id != self.instrument_id or event.feed != self.feed:
            raise MismatchedSeriesError(
                f"event for ({event.instrument_id!r}, {event.feed!r}) cannot be applied to builder "
                f"for ({self.instrument_id!r}, {self.feed!r})"
            )
        if self._source_identity is not None and self._source_identity != (
            event.provider,
            event.session_version,
        ):
            raise MismatchedSeriesError(
                "provider/session identity changed within the builder"
            )
        if event.quality_state != QualityState.OK:
            raise RejectedQualityError(
                f"event {event.event_id!r} has quality_state {event.quality_state.value!r}; only "
                "'ok' events may mutate builder state (suspect/invalid/unavailable are quarantined)"
            )

    def on_trade(self, event: TradeEvent) -> BarSnapshot:
        self._check_series_and_quality(event)
        self._check_publication_time(event.available_at_ns)
        bucket_start = self.bucket_start_ns(event.event_time_ns)
        if bucket_start in self._evicted:
            raise EvictedBucketError(
                f"bucket {bucket_start} was evicted under the retention policy"
            )
        eligibility = self._rules.classify(
            TradeClassificationContext(
                conditions=event.conditions, tape=event.tape, interval=self.interval
            )
        )
        payload = (
            event.provider,
            event.feed,
            event.instrument_id,
            event.event_time_ns,
            event.price,
            event.size,
            event.venue,
            event.conditions,
            event.tape,
            event.session_version,
        )
        previous_payload = self._trade_payloads.get(event.trade_id)
        if previous_payload is not None and previous_payload != payload:
            raise ConflictingTradeError(
                "trade identity redelivered with different market payload"
            )
        is_new_bucket = bucket_start not in self._buckets
        contributors = self._buckets.get(bucket_start, {})
        existing = contributors.get(event.trade_id)
        if existing is not None:
            if (
                existing.active
                and existing.price == event.price
                and existing.size == event.size
                and existing.eligibility == eligibility
            ):
                return self._snapshot(
                    bucket_start, event.available_at_ns
                )  # idempotent redelivery (BAR-005)
            raise ConflictingTradeError(
                f"trade_id {event.trade_id!r} redelivered with a different payload; use a correction"
            )
        existing_bucket = self._trade_id_bucket.get(event.trade_id)
        if existing_bucket is not None and existing_bucket != bucket_start:
            # IR-01/R3I-11: a bare trade_id reused in a different bucket is an explicit identity
            # conflict -- it must never silently redirect future correction lookups away from
            # the original bucket.
            raise ConflictingTradeError(
                f"trade_id {event.trade_id!r} already belongs to bucket {existing_bucket}; cannot "
                f"also claim bucket {bucket_start} (declared identity scope is per-builder, not per-bucket)"
            )
        self._buckets[bucket_start] = contributors
        self._trade_payloads[event.trade_id] = payload
        self._source_identity = (event.provider, event.session_version)
        contributors[event.trade_id] = _Contributor(
            event.trade_id,
            event.event_time_ns,
            event.price,
            event.size,
            eligibility,
        )
        self._trade_id_bucket[event.trade_id] = bucket_start
        for effective_ns, _, reason in self._actions.values():
            if bucket_start < effective_ns < bucket_start + self._duration_ns:
                self._invalidated_buckets[bucket_start] = reason
        # IR-03/R3I-12: the first contributor into a bucket whose end has already elapsed per
        # the watermark closes it immediately (as CLOSED_AS_OF, revision 0) -- it never appears
        # as currently forming, and does not itself count as a post-close revision.
        closed_immediately_by_watermark = False
        if is_new_bucket and bucket_start not in self._closed:
            bucket_end = bucket_start + self._duration_ns
            if bucket_end <= self._watermark_ns:
                self._closed.add(bucket_start)
                self._closed_order.append(bucket_start)
                closed_immediately_by_watermark = True
        if not closed_immediately_by_watermark:
            self._register_post_close_update(bucket_start)
        snapshot = self._snapshot(bucket_start, event.available_at_ns)
        snapshot = self._publish_version(bucket_start, snapshot)
        if closed_immediately_by_watermark:
            self._enforce_retention()
        return snapshot

    def on_correction(self, event: TradeCorrectionEvent) -> BarSnapshot:
        self._check_series_and_quality(event)
        self._check_publication_time(event.available_at_ns)
        bucket_start = self._find_bucket(event.original_trade_id)
        payload = (
            event.original_trade_id,
            event.action,
            event.corrected_price,
            event.corrected_size,
            event.conditions,
            event.tape,
        )
        previously_applied = self._applied_corrections.get(event.event_id)
        if previously_applied is not None:
            if previously_applied != payload:
                raise ConflictingTradeError(
                    f"correction event_id {event.event_id!r} redelivered with a different payload"
                )
            return self._snapshot(
                bucket_start, event.available_at_ns
            )  # idempotent redelivery (R3I-11)
        contributor = self._buckets[bucket_start][event.original_trade_id]
        # IR-01: compute every candidate value BEFORE mutating anything, so a classification
        # error (e.g. an unknown condition) leaves the contributor completely untouched --
        # never a partially-applied price/size with a stale eligibility, or vice versa.
        if event.action == "cancel":
            new_active, new_price, new_size, new_eligibility = (
                False,
                contributor.price,
                contributor.size,
                contributor.eligibility,
            )
        else:
            new_eligibility = (
                self._rules.classify(
                    TradeClassificationContext(
                        conditions=event.conditions,
                        tape=event.tape,
                        interval=self.interval,
                    )
                )
                if event.conditions
                is not None  # None = conditions unchanged by this correction
                else contributor.eligibility
            )
            new_active, new_price, new_size = (
                True,
                event.corrected_price,
                event.corrected_size,
            )
        contributor.active = new_active
        contributor.price = new_price
        contributor.size = new_size
        contributor.eligibility = new_eligibility
        self._applied_corrections[event.event_id] = payload
        self._register_post_close_update(bucket_start)
        snapshot = self._snapshot(bucket_start, event.available_at_ns)
        snapshot = self._publish_version(bucket_start, snapshot)
        return snapshot

    def on_cancel(self, event: TradeCorrectionEvent) -> BarSnapshot:
        return self.on_correction(event)

    def _check_publication_time(self, now_ns: int, phase: int = 37) -> None:
        if now_ns < self._last_publication_ns:
            raise ValueError("publication cannot move backward in availability")
        if self._version_store is not None:
            self._version_store.check_publication(now_ns, phase)

    def observe_clock(self, now_ns: int) -> None:
        """Advance elapsed time without fabricating a publication."""
        self._watermark_ns = max(self._watermark_ns, now_ns)

    def invalidate_if_straddled(
        self,
        effective_ns: int,
        *,
        reason: str,
        available_at_ns: int | None = None,
        action_id: str | None = None,
    ) -> BarSnapshot | None:
        available_at_ns = effective_ns if available_at_ns is None else available_at_ns
        self._check_publication_time(available_at_ns, 25)
        action_id = action_id or reason
        payload = (effective_ns, available_at_ns, reason)
        if action_id in self._actions:
            if self._actions[action_id] != payload:
                raise ValueError("conflicting corporate action identity")
            return None
        bucket_start = self.bucket_start_ns(effective_ns)
        if (
            bucket_start in self._evicted
            and bucket_start < effective_ns < bucket_start + self._duration_ns
        ):
            raise EvictedBucketError(
                "cannot resolve action against evicted contributors"
            )
        self._actions[action_id] = payload
        if not bucket_start < effective_ns < bucket_start + self._duration_ns:
            return None
        if bucket_start not in self._buckets:
            return None  # retain the action for a first late contributor
        self._invalidated_buckets[bucket_start] = reason
        if bucket_start not in self._closed:
            self._closed.add(bucket_start)
            self._closed_order.append(bucket_start)
        snapshot = self._snapshot(bucket_start, available_at_ns).model_copy(
            update={"publication_phase": 25}
        )
        snapshot = self._publish_version(bucket_start, snapshot)
        self._enforce_retention()
        return snapshot

    def advance_clock(
        self, now_ns: int, *, publication_phase: int = 20
    ) -> list[BarSnapshot]:
        """Close elapsed buckets using the publication clock, never a delayed boundary time."""
        self._check_publication_time(now_ns, publication_phase)
        self.observe_clock(now_ns)
        newly_closed = []
        for bucket_start in sorted(self._buckets):
            if (
                bucket_start + self._duration_ns <= self._watermark_ns
                and bucket_start not in self._closed
            ):
                self._closed.add(bucket_start)
                self._closed_order.append(bucket_start)
                snapshot = self._snapshot(bucket_start, now_ns).model_copy(
                    update={"publication_phase": publication_phase}
                )
                snapshot = self._publish_version(bucket_start, snapshot)
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
            raise UnresolvedCorrectionError(
                f"no accepted trade with trade_id {trade_id!r}"
            )
        if bucket_start in self._evicted:
            raise EvictedBucketError(
                f"original trade {trade_id!r}'s bucket was evicted"
            )
        return bucket_start

    def _register_post_close_update(self, bucket_start: int) -> None:
        if bucket_start in self._closed:
            self._post_close_revisions[bucket_start] = (
                self._post_close_revisions.get(bucket_start, 0) + 1
            )

    def _publish_version(self, bucket_start: int, snapshot: BarSnapshot) -> BarSnapshot:
        self._check_publication_time(
            snapshot.available_at_ns, snapshot.publication_phase
        )
        self._last_publication_ns = snapshot.available_at_ns
        if self._version_store is None:
            return snapshot
        source_event_ids = tuple(
            sorted(
                c.trade_id
                for c in self._buckets.get(bucket_start, {}).values()
                if c.active
            )
        )
        version = self._version_store.publish(
            snapshot, source_event_ids=source_event_ids
        )
        return snapshot.model_copy(update={"publication_sequence": version.sequence})

    def _enforce_retention(self) -> None:
        if self._retention_limit is None:
            return
        while len(self._closed_order) > self._retention_limit:
            oldest = self._closed_order.pop(0)
            self._buckets.pop(oldest, None)
            self._evicted.add(oldest)

    def _snapshot(self, bucket_start: int, available_at_ns: int) -> BarSnapshot:
        contributors = [
            c for c in self._buckets.get(bucket_start, {}).values() if c.active
        ]
        is_closed = bucket_start in self._closed
        revision = self._post_close_revisions.get(bucket_start, 0) if is_closed else 0
        if bucket_start in self._invalidated_buckets:
            status = SnapshotStatus.INVALIDATED
        else:
            status = (
                SnapshotStatus.FORMING
                if not is_closed
                else (
                    SnapshotStatus.REVISED
                    if revision > 0
                    else SnapshotStatus.CLOSED_AS_OF
                )
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

        volume = sum(
            (c.size for c in contributors if c.eligibility.volume), start=Decimal(0)
        )
        trade_count = sum(1 for c in contributors if c.eligibility.count)

        vwap_contributors = [c for c in contributors if c.eligibility.vwap]
        vwap_numerator = sum(
            (c.price * c.size for c in vwap_contributors), start=Decimal(0)
        )
        vwap_denominator = sum((c.size for c in vwap_contributors), start=Decimal(0))

        return BarSnapshot(
            instrument_id=self.instrument_id,
            feed=self.feed,
            interval=self.interval,
            bar_start_ns=bucket_start,
            bar_end_ns=bucket_start + self._duration_ns,
            available_at_ns=available_at_ns,
            status=status,
            revision=revision,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            trade_count=trade_count,
            vwap_numerator=vwap_numerator,
            vwap_denominator=vwap_denominator,
            rule_set_id=self._rules.rule_set_id,
            provider=self._source_identity[0] if self._source_identity else None,
            session_version=self._source_identity[1] if self._source_identity else None,
        )

"""Immutable, append-only publication history for bar snapshots (C06).

The builder's live `snapshot()` query always answers "what does this bucket look
like right now" -- it is not, and must never be used as, a historical query.
Before this module existed, the only way to ask "what did this bucket look
like as of second 5" was to recompute `BarSnapshot` from the CURRENT
contributor set and merely stamp the result with an earlier `available_at_ns`
-- so a correction or a later trade silently leaked into an answer that
claimed to be from the past (the C06 defect: "querying a builder snapshot as
of second 5 returns T2's price stamped with the earlier time").

`BarVersionStore` fixes this by recording a brand-new, immutable `BarVersion`
every time the builder accepts a change. A historical `as_of` query walks the
already-published versions and returns the last one whose publication became
visible at or before the cutoff -- it never recomputes from current state.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from rsys.data.bar_builder import BarSnapshot


class BarVersion(BaseModel):
    """One immutable, published state of a bucket. `publication_key` orders versions for the
    same bucket identity; `sequence` breaks ties between versions published at the same
    `available_at_ns` (e.g. a trade and a same-instant correction), preserving publication
    order without claiming any particular wall-clock meaning for the tie-break itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    feed: str
    interval: str
    bar_start_ns: int
    bar_end_ns: int
    available_at_ns: int
    sequence: int
    status: str
    revision: int
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal
    trade_count: int
    vwap_numerator: Decimal
    vwap_denominator: Decimal
    rule_set_id: str
    source_event_ids: tuple[str, ...]

    @property
    def publication_key(self) -> tuple[int, int]:
        return (self.available_at_ns, self.sequence)

    @classmethod
    def from_snapshot(cls, snapshot: BarSnapshot, *, sequence: int, source_event_ids: tuple[str, ...]) -> BarVersion:
        return cls(
            instrument_id=snapshot.instrument_id, feed=snapshot.feed, interval=snapshot.interval,
            bar_start_ns=snapshot.bar_start_ns, bar_end_ns=snapshot.bar_end_ns,
            available_at_ns=snapshot.available_at_ns, sequence=sequence, status=snapshot.status.value,
            revision=snapshot.revision, open=snapshot.open, high=snapshot.high, low=snapshot.low,
            close=snapshot.close, volume=snapshot.volume, trade_count=snapshot.trade_count,
            vwap_numerator=snapshot.vwap_numerator, vwap_denominator=snapshot.vwap_denominator,
            rule_set_id=snapshot.rule_set_id, source_event_ids=source_event_ids,
        )


BucketIdentity = tuple[str, str, str, int]  # (instrument_id, feed, interval, bar_start_ns)


class BarVersionStore:
    """Append-only per-bucket version history. Never mutates or re-derives a published
    version; a historical lookup only ever selects among what was actually published."""

    def __init__(self) -> None:
        self._versions: dict[BucketIdentity, list[BarVersion]] = {}
        self._next_sequence = 0

    def publish(self, snapshot: BarSnapshot, *, source_event_ids: tuple[str, ...]) -> BarVersion:
        identity: BucketIdentity = (snapshot.instrument_id, snapshot.feed, snapshot.interval, snapshot.bar_start_ns)
        version = BarVersion.from_snapshot(snapshot, sequence=self._next_sequence, source_event_ids=source_event_ids)
        self._next_sequence += 1
        self._versions.setdefault(identity, []).append(version)
        return version

    def as_of(self, identity: BucketIdentity, cutoff_ns: int) -> BarVersion | None:
        """The last published version visible at or before `cutoff_ns`, or `None` if no
        version had been published by then (a typed "unavailable" result -- never a
        fabricated empty bar standing in for missing history)."""
        versions = self._versions.get(identity, [])
        visible = [v for v in versions if v.available_at_ns <= cutoff_ns]
        if not visible:
            return None
        return max(visible, key=lambda v: v.publication_key)

    def latest(self, identity: BucketIdentity) -> BarVersion | None:
        versions = self._versions.get(identity, [])
        if not versions:
            return None
        return versions[-1]

    def history(self, identity: BucketIdentity) -> tuple[BarVersion, ...]:
        return tuple(self._versions.get(identity, ()))

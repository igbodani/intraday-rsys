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

from rsys.data.keys import cutoff_key
from rsys.data.models import BarSnapshot, BarVersion, BucketIdentity, ReplayKey


class BarVersionStore:
    """Append-only per-bucket version history. Never mutates or re-derives a published
    version; a historical lookup only ever selects among what was actually published."""

    def __init__(self) -> None:
        self._versions: dict[BucketIdentity, list[BarVersion]] = {}
        self._next_sequence = 0
        self._last_key: ReplayKey | None = None

    def check_publication(self, available_at_ns: int, phase: int) -> None:
        candidate = ReplayKey(available_at_ns, phase, self._next_sequence)
        if self._last_key is not None and candidate < self._last_key:
            raise ValueError("cannot backdate a bar publication")

    def publish(
        self, snapshot: BarSnapshot, *, source_event_ids: tuple[str, ...]
    ) -> BarVersion:
        identity: BucketIdentity = (
            snapshot.instrument_id,
            snapshot.feed,
            snapshot.interval,
            snapshot.bar_start_ns,
        )
        version = BarVersion.from_snapshot(
            snapshot, sequence=self._next_sequence, source_event_ids=source_event_ids
        )
        if self._last_key is not None and version.publication_key < self._last_key:
            raise ValueError("cannot backdate a bar publication")
        self._last_key = version.publication_key
        self._next_sequence += 1
        self._versions.setdefault(identity, []).append(version)
        return version

    def as_of(
        self, identity: BucketIdentity, cutoff_ns: int | ReplayKey
    ) -> BarVersion | None:
        """The last published version visible at or before `cutoff_ns`, or `None` if no
        version had been published by then (a typed "unavailable" result -- never a
        fabricated empty bar standing in for missing history)."""
        versions = self._versions.get(identity, [])
        visible = [v for v in versions if v.publication_key <= cutoff_key(cutoff_ns)]
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

    def bucket_identities(self) -> tuple[BucketIdentity, ...]:
        """Every bucket identity any version has ever been published under -- a read of
        already-published keys, never live mutable builder state (IR-02)."""
        return tuple(self._versions.keys())

    def frozen_versions(self) -> tuple[BarVersion, ...]:
        return tuple(v for history in self._versions.values() for v in history)

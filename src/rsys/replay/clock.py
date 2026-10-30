"""Replay clock: deterministic phase ordering over a declared profile (R1).

One availability clock sorts every event by `(available_at_ns, phase, source_sequence,
stable tiebreak)`. Two profiles are supported: the existing completed-bar `bar_v1`
contract (docs/contracts.md), and a new `trade_replay_v1` profile that adds phases for
market-data application and forming/revised bar publication. Neither profile changes
the other's event timing.

This module only orders/classifies events; building bars from trades is R2, and
risk/execution (phases 10, 60, 70) stay inactive in this data-only slice.
"""

from __future__ import annotations

from collections.abc import Sequence

from rsys.data.models import BarCloseEvent, EventKind, MarketEvent, ReplayKey
from rsys.replay.errors import (
    IncompleteReplayError,
    LateInsertionError,
    UnsupportedPhaseError,
)
from rsys.replay.models import Phase, ReplayProfile

_PROFILE_PHASES: dict[ReplayProfile, frozenset[Phase]] = {
    ReplayProfile.BAR_V1: frozenset(
        {
            Phase.INTERVAL_SETTLE,
            Phase.BAR_PUBLISH,
            Phase.CORPORATE_ACTION,
            Phase.CASH_RELEASE,
            Phase.TIMER,
            Phase.SCAN_SIGNAL,
            Phase.ACCEPT,
            Phase.OPEN_EXIT,
            Phase.OPEN_ENTRY,
            Phase.RECORD,
        }
    ),
    ReplayProfile.TRADE_REPLAY_V1: frozenset(
        {
            Phase.INTERVAL_SETTLE,
            Phase.BAR_PUBLISH,
            Phase.CORPORATE_ACTION,
            Phase.CASH_RELEASE,
            Phase.TIMER,
            Phase.MARKET_DATA_APPLY,
            Phase.BAR_UPDATE_PUBLISH,
            Phase.SCAN_SIGNAL,
            Phase.ACCEPT,
            Phase.OPEN_EXIT,
            Phase.OPEN_ENTRY,
            Phase.RECORD,
        }
    ),
}


def event_phase(event: MarketEvent, profile: ReplayProfile) -> Phase:
    """Classify an event into its replay phase. A `ProviderBarEvent` with `bar_revision > 1`
    is a post-closure revision (phase 37 in the trade profile); its first/closing version
    publishes at phase 20, same as `bar_v1`.
    """
    if isinstance(event, BarCloseEvent):
        phase = Phase.BAR_PUBLISH
    elif event.kind in (EventKind.TRADE, EventKind.TRADE_CORRECTION, EventKind.QUOTE):
        phase = Phase.MARKET_DATA_APPLY
    elif event.kind == EventKind.PROVIDER_BAR:
        is_revision = getattr(event, "bar_revision", 1) > 1
        phase = Phase.BAR_UPDATE_PUBLISH if is_revision else Phase.BAR_PUBLISH
    elif event.kind == EventKind.CORPORATE_ACTION:
        phase = Phase.CORPORATE_ACTION
    elif event.kind == EventKind.TIMER:
        phase = Phase.TIMER
    else:  # pragma: no cover - EventKind is a closed set
        raise UnsupportedPhaseError(f"no phase mapping for event kind {event.kind!r}")

    if phase not in _PROFILE_PHASES[profile]:
        raise UnsupportedPhaseError(
            f"{profile.value} does not support phase {phase.name} ({event.kind.value})"
        )
    return phase


def _tiebreak(event: MarketEvent) -> tuple[str, str, str, str, str]:
    """Declared stable tie key used only when `source_sequence` is absent."""
    return (
        event.provider,
        event.feed,
        event.instrument_id,
        event.kind.value,
        event.event_id,
    )


# Events without a recorded source_sequence sort before any that have one at the same
# (available_at_ns, phase); real sequence numbers are assumed nonnegative.
_NO_SEQUENCE = -1


def sort_key(event: MarketEvent, profile: ReplayProfile) -> tuple:
    """`(availability, phase, source_sequence_or_sentinel, stable_tiebreak)`. Depends only on the
    event's own fields, never on its position in an input file/page -- permuting input order
    cannot change the resulting order as long as `event_id` is unique (REPLAY-001).
    """
    phase = event_phase(event, profile)
    seq = event.source_sequence if event.source_sequence is not None else _NO_SEQUENCE
    return (event.available_at_ns, int(phase), seq, _tiebreak(event))


def replay_order(
    events: Sequence[MarketEvent], profile: ReplayProfile
) -> list[MarketEvent]:
    """The full deterministic dispatch order for a known, finite event collection."""
    return sorted(events, key=lambda e: sort_key(e, profile))


def same_time_batch(
    ordered_events: Sequence[MarketEvent], available_at_ns: int
) -> list[MarketEvent]:
    """Every event sharing one availability instant, in phase order -- the complete batch a
    baseline cross-symbol ranking (phase 40) must see before it runs, regardless of how many
    symbols had an event at that instant."""
    return [e for e in ordered_events if e.available_at_ns == available_at_ns]


def assert_stream_covers_boundary(
    events: Sequence[MarketEvent],
    required_boundary_ns: int,
    *,
    required_boundary_name: str | None = None,
    required_session_version: str | None = None,
) -> None:
    """A required closure/coverage boundary (e.g. the session-close timer) must actually appear;
    EOF before it marks the run incomplete rather than silently treating it as closed.

    IR-03/R3I-06: when given, `required_boundary_name`/`required_session_version` must ALSO
    match -- an unrelated later timer (wrong boundary identity or wrong calendar/session) must
    never satisfy this check merely by sharing `EventKind.TIMER` and a late enough time.
    """

    def _satisfies(e: MarketEvent) -> bool:
        if e.kind != EventKind.TIMER or e.event_time_ns < required_boundary_ns:
            return False
        if (
            required_boundary_name is not None
            and getattr(e, "boundary_name", None) != required_boundary_name
        ):
            return False
        return (
            required_session_version is None
            or e.session_version == required_session_version
        )

    if not any(_satisfies(e) for e in events):
        raise IncompleteReplayError(
            f"stream ended before required boundary {required_boundary_ns} "
            f"(boundary_name={required_boundary_name!r}, session_version={required_session_version!r})"
        )


class ReplayStream:
    """An ordered, insert-guarded view over a profile's events. Dispatch only moves forward:
    once a `(available_at_ns, phase)` bucket has been dispatched, no derived event may be
    inserted into it (the queue rule phases 35/37 will need once R2's bar builder exists).
    """

    def __init__(self, events: Sequence[MarketEvent], profile: ReplayProfile):
        self._profile = profile
        self._items = replay_order(events, profile)
        self._dispatched_count = 0

    def __iter__(self):
        return self

    def __next__(self) -> MarketEvent:
        if self._dispatched_count >= len(self._items):
            raise StopIteration
        item = self._items[self._dispatched_count]
        self._dispatched_count += 1
        return item

    @property
    def last_dispatched_key(self) -> tuple | None:
        if self._dispatched_count == 0:
            return None
        return sort_key(self._items[self._dispatched_count - 1], self._profile)

    def peek(self) -> MarketEvent | None:
        """The next not-yet-dispatched event without consuming it, or `None` at end of stream."""
        if self._dispatched_count >= len(self._items):
            return None
        return self._items[self._dispatched_count]

    def all_items(self) -> tuple[MarketEvent, ...]:
        """The full dispatch-ordered event collection, including already-dispatched items --
        a read of the declared schedule, not a way to bypass availability bounding."""
        return tuple(self._items)

    def insert_derived(self, event: MarketEvent) -> None:
        """Insert a derived event (e.g. a bar revision produced while processing a correction).

        C07: only a strictly later `(available_at_ns, phase)` pair is ever accepted -- never the
        same phase at the same availability (even if a full sort key, including sequence/tiebreak,
        would have sorted it after what was already dispatched) and never an earlier one. A
        derived publication at phase 37 (BAR_UPDATE_PUBLISH) produced while dispatching phase 35
        (MARKET_DATA_APPLY) at the SAME availability instant is explicitly allowed by this rule,
        since phase strictly increases even though availability does not.
        """
        key = sort_key(event, self._profile)
        last_key = self.last_dispatched_key
        if last_key is not None and (key[0], key[1]) <= (last_key[0], last_key[1]):
            raise LateInsertionError(
                f"derived event {event.event_id!r} targets an already-dispatched "
                f"(availability, phase) bucket"
            )
        remaining = self._items[self._dispatched_count :]
        remaining.append(event)
        remaining.sort(key=lambda e: sort_key(e, self._profile))
        self._items = self._items[: self._dispatched_count] + remaining


def event_key(event: MarketEvent, profile: ReplayProfile) -> ReplayKey:
    ns, phase, sequence, tie = sort_key(event, profile)
    return ReplayKey(ns, phase, sequence, tie)

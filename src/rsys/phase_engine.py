"""Standalone phase-ordering scheduler proving docs/contracts.md's declared phase table
(10..80) and the ordering invariants from docs/M2_IMPLEMENTATION_HANDOFF.md step 1
(M2-01a): same-time batching, frozen decision views, settlement-before-decision, a closed
phase rejecting a derived event, and idempotent/conflicting event redelivery.

Honesty note: this is a new, self-contained scheduler built on top of the already-tested
`rsys.acceptance`/`rsys.portfolio` modules. It does NOT extend `ReplayRunner`/`SeriesRouter`
and shares no code or state with the existing data-only replay runtime in
`replay_runtime.py`. It demonstrates the ordering contract in isolation; wiring F01-F17 and
a real multi-symbol data feed through it remains future work -- see
docs/M2_IMPLEMENTATION_STATUS.md.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PHASES = (10, 20, 25, 26, 30, 40, 50, 60, 70, 80)


class PhaseOrderError(Exception):
    """A derived event targeted a (timestamp, phase) slot that is already closed, or was
    redelivered under the same event_id with a conflicting payload."""


@dataclass(frozen=True)
class PhaseEvent:
    event_id: str
    available_at_ns: int
    phase: int
    kind: str
    symbol: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def sort_key(self) -> tuple[int, int, str]:
        return (self.available_at_ns, self.phase, self.event_id)


@dataclass
class DispatchRecord:
    event_id: str
    available_at_ns: int
    phase: int
    kind: str
    symbol: str | None
    result: Any


class PhaseEngine:
    """Drains enqueued events in strict `(available_at_ns, phase, event_id)` order. The
    caller supplies one callback per handled phase; this module only enforces ordering,
    same-time batching (via the frozen view passed into `on_decision`), idempotency and the
    closed-phase invariant -- it carries no market-data or ledger logic of its own."""

    def __init__(
        self, *,
        on_settle: Callable[[PhaseEvent], Any],
        on_publish: Callable[[PhaseEvent], Any],
        on_decision: Callable[[PhaseEvent, dict[str, Any]], Any],
        on_fill: Callable[[PhaseEvent], Any],
    ):
        self._queue: list[PhaseEvent] = []
        self._dispatched_ids: set[str] = set()
        self._applied_payloads: dict[str, dict[str, Any]] = {}
        self._high_water: tuple[int, int] = (-1, -1)
        self.closes: dict[str, Any] = {}
        self.transitions: list[DispatchRecord] = []
        self._on_settle = on_settle
        self._on_publish = on_publish
        self._on_decision = on_decision
        self._on_fill = on_fill

    def enqueue(self, event: PhaseEvent) -> None:
        key = (event.available_at_ns, event.phase)
        if key < self._high_water:
            raise PhaseOrderError(
                f"event {event.event_id!r} targets ({event.available_at_ns}, phase {event.phase}), "
                f"already closed at {self._high_water}"
            )
        self._queue.append(event)

    def run(self) -> None:
        while self._queue:
            self._queue.sort(key=lambda e: e.sort_key)
            event = self._queue.pop(0)
            self._high_water = (event.available_at_ns, event.phase)
            self._dispatch(event)

    def _dispatch(self, event: PhaseEvent) -> None:
        if event.event_id in self._dispatched_ids:
            previous = self._applied_payloads.get(event.event_id)
            if previous is not None and previous != event.payload:
                raise PhaseOrderError(f"event {event.event_id!r} redelivered with a conflicting payload")
            return  # identical redelivery: idempotent no-op, never a second effect
        self._dispatched_ids.add(event.event_id)
        self._applied_payloads[event.event_id] = event.payload

        if event.phase == 10:
            result = self._on_settle(event)
        elif event.phase == 20:
            result = self._on_publish(event)
            if event.symbol is not None and "close" in event.payload:
                self.closes[event.symbol] = event.payload["close"]
        elif event.phase == 40:
            frozen_view = dict(self.closes)  # a copy: no later mutation can leak backward into it
            result = self._on_decision(event, frozen_view)
        elif event.phase in (60, 70):
            result = self._on_fill(event)
        else:
            result = None
        self.transitions.append(DispatchRecord(
            event_id=event.event_id, available_at_ns=event.available_at_ns, phase=event.phase,
            kind=event.kind, symbol=event.symbol, result=result,
        ))

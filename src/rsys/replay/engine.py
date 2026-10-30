"""Domain phase dispatch using the replay runtime's full nanosecond ordering key.

ReplayRunner attaches its source dispatch to this queue for synthetic execution.
Legacy callbacks remain useful for isolated account fixtures and scheduler tests.
"""

from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType

from rsys.replay.errors import PhaseOrderError
from rsys.replay.models import DispatchRecord


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(v) for v in value)
    return deepcopy(value)


class PhaseEngine:
    def __init__(
        self,
        *,
        on_settle=None,
        on_publish=None,
        on_decision=None,
        on_fill=None,
        on_dispatch=None,
    ):
        self._queue = []
        self._applied = {}
        self._high_water = None
        self._running = False
        self.closes = {}
        self.transitions = []
        self._handlers = {
            10: on_settle,
            20: on_publish,
            40: on_decision,
            60: on_fill,
            70: on_fill,
        }
        self._on_dispatch = on_dispatch

    def enqueue(self, event):
        if (
            event.event_id not in self._applied
            and self._high_water is not None
            and (event.available_at_ns, event.phase)
            <= (self._high_water.available_at_ns, self._high_water.phase)
        ):
            raise PhaseOrderError(
                "derived event targets an already completed key/phase"
            )
        self._queue.append(deepcopy(event))

    def run(self):
        self._running = True
        try:
            while self._queue:
                self._queue.sort(key=lambda e: e.sort_key)
                event = self._queue.pop(0)
                if event.event_id in self._applied:
                    if self._applied[event.event_id] != event:
                        raise PhaseOrderError("conflicting event identity or payload")
                    continue
                if self._high_water is not None and event.sort_key < self._high_water:
                    raise PhaseOrderError("event would rewind replay")
                self._high_water = event.sort_key
                original = deepcopy(event)
                if self._on_dispatch is not None:
                    result = self._on_dispatch(event)
                else:
                    handler = self._handlers.get(event.phase)
                    result = (
                        handler(event, _freeze(self.closes))
                        if event.phase == 40 and handler
                        else handler(event)
                        if handler
                        else None
                    )
                if (
                    event.phase == 20
                    and event.symbol is not None
                    and "close" in event.payload
                ):
                    self.closes[event.symbol] = deepcopy(event.payload["close"])
                self._applied[event.event_id] = original
                self.transitions.append(
                    DispatchRecord(
                        event.event_id,
                        event.available_at_ns,
                        event.phase,
                        event.kind,
                        event.symbol,
                        deepcopy(result),
                    )
                )
        finally:
            self._running = False

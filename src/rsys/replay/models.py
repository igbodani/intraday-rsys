"""Replay data models and their intrinsic validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from rsys.data.models import QuoteEvent, ReplayKey
from rsys.replay.errors import PhaseOrderError


class Phase(IntEnum):
    """Numbers and meanings fixed by docs/contracts.md; 35/37 are the trade-profile addition."""

    INTERVAL_SETTLE = 10
    BAR_PUBLISH = 20
    CORPORATE_ACTION = 25
    CASH_RELEASE = 26
    TIMER = 30
    MARKET_DATA_APPLY = 35  # trade_replay_v1 only: trades/quotes/corrections
    BAR_UPDATE_PUBLISH = (
        37  # trade_replay_v1 only: immutable forming/revised bar snapshots
    )
    SCAN_SIGNAL = 40
    ACCEPT = 50
    OPEN_EXIT = 60
    OPEN_ENTRY = 70
    RECORD = 80


PHASES = tuple(int(p) for p in Phase)


@dataclass(frozen=True)
class PhaseEvent:
    event_id: str
    available_at_ns: int
    phase: int
    kind: str
    symbol: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    sequence: int = -1
    tie: tuple[str, ...] = ()

    def __post_init__(self):
        if (
            self.phase not in PHASES
            or type(self.available_at_ns) is not int
            or self.available_at_ns < 0
        ):
            raise PhaseOrderError("unsupported phase or invalid timestamp")

    @property
    def sort_key(self):
        return ReplayKey(
            self.available_at_ns,
            self.phase,
            self.sequence,
            self.tie or (self.event_id,),
        )


@dataclass
class DispatchRecord:
    event_id: str
    available_at_ns: int
    phase: int
    kind: str
    symbol: str | None
    result: Any


class ReplayProfile(StrEnum):
    BAR_V1 = "bar_v1"
    TRADE_REPLAY_V1 = "trade_replay_v1"


@dataclass(frozen=True)
class ObservationRecord:
    """One observer evaluation, including None results and the inputs it consulted."""

    observer_name: str
    instrument_id: str
    decision_key_ns: int
    mode: str
    result: object
    input_versions: tuple[str, ...] = ()
    cadence: str = "availability_batch"
    decision_phase: int = 40


@dataclass(frozen=True)
class QuoteObservation:
    event: QuoteEvent | None
    state: str  # usable, disabled, unavailable, invalid, stale, crossed, locked, missing side

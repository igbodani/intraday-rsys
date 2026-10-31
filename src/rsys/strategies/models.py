"""Strategies data models and their intrinsic validation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


@dataclass(frozen=True)
class CandidateInput:
    symbol: str
    rvol: Decimal | None
    gain: Decimal | None
    dollar_volume_eligible: bool = True


@dataclass(frozen=True)
class ScanResult:
    top: list[str]
    excluded: dict[str, str]
    ranked: list[str]  # the full eligible ranking, independent of top_n
    ranked_candidates: list[
        CandidateInput
    ]  # same order as `ranked`, full rows for evidence


@dataclass(frozen=True)
class HistoryScan:
    result: ScanResult
    evidence: dict


class OrbState(str, Enum):
    COLLECT_RANGE = "COLLECT_RANGE"
    ARMED = "ARMED"
    PROPOSED = "PROPOSED"
    IN_POSITION = "IN_POSITION"
    DONE = "DONE"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class CompletedBar:
    bar_start_ms: int
    available_at_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


TICK = Decimal("0.01")


@dataclass
class OrbConfig:
    range_minutes: int = 15
    breakout_buffer_ticks: int = 1
    base_interval_minutes: int = 1
    entry_deadline_ms: int | None = (
        None  # exchange-local ms-of-day; None disables the deadline
    )
    target_r: Decimal = Decimal(2)
    tick: Decimal = TICK


class VwapState(str, Enum):
    WARMUP = "WARMUP"
    IDLE = "IDLE"
    ARMED = "ARMED"
    PULLBACK = "PULLBACK"
    PROPOSED = "PROPOSED"
    IN_POSITION = "IN_POSITION"
    DONE = "DONE"
    EXPIRED = "EXPIRED"


@dataclass
class VwapConfig:
    warmup_bars: int = 20
    slope_lookback_bars: int = 5
    pullback_max_bars: int = 5
    confirmation_buffer_ticks: int = 1
    base_interval_minutes: int = 1
    entry_deadline_ms: int | None = None
    target_r: Decimal = Decimal(2)
    tick: Decimal = TICK

"""`orb_close_cross_v1` (docs/strategies/orb.md, STRAT-ORB-001).

Standalone completed-bar state machine: COLLECT_RANGE -> ARMED -> PROPOSED ->
IN_POSITION -> DONE/EXPIRED. Produces `rsys.orders.Proposal` objects; sizing, acceptance
and execution are external concerns (`rsys.sizing`/`rsys.acceptance`/`rsys.fixture_engine`).

Honesty note: not wired into `PhaseEngine` or a real multi-symbol replay feed; the scanner
candidate flag is supplied by the caller per bar, not computed here -- see
`rsys.scanner` and docs/M2_IMPLEMENTATION_STATUS.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import Enum

from rsys.orders import Proposal

STRATEGY_ID = "orb_close_cross_v1"
TICK = Decimal("0.01")


def _floor_tick(value: Decimal, tick: Decimal = TICK) -> Decimal:
    return value.quantize(tick, rounding=ROUND_FLOOR)


def _ceil_tick(value: Decimal, tick: Decimal = TICK) -> Decimal:
    return value.quantize(tick, rounding=ROUND_CEILING)


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


@dataclass
class OrbConfig:
    range_minutes: int = 15
    breakout_buffer_ticks: int = 1
    base_interval_minutes: int = 1
    entry_deadline_ms: int | None = None  # exchange-local ms-of-day; None disables the deadline
    target_r: Decimal = Decimal(2)
    tick: Decimal = TICK


class OrbStrategy:
    """One instrument, one session. Call `on_completed_bar` for every completed bar in
    chronological order; a non-None return is a new proposal."""

    def __init__(self, symbol: str, session_open_ms: int, config: OrbConfig | None = None):
        self.symbol = symbol
        self.config = config or OrbConfig()
        self.state = OrbState.COLLECT_RANGE
        self._range_end_ms = session_open_ms + self.config.range_minutes * 60_000
        self.range_high: Decimal | None = None
        self.range_low: Decimal | None = None
        self.previous_close: Decimal | None = None
        self.trigger: Decimal | None = None
        self.stop: Decimal | None = None
        self.filled_today = False
        self.proposals: list[Proposal] = []

    def on_completed_bar(self, bar: CompletedBar, *, is_candidate: bool) -> Proposal | None:
        if self.state in (OrbState.DONE, OrbState.EXPIRED, OrbState.PROPOSED, OrbState.IN_POSITION):
            return None  # position/pending-intent management happens outside this strategy

        if self.state == OrbState.COLLECT_RANGE:
            if bar.bar_start_ms < self._range_end_ms:
                self.range_high = bar.high if self.range_high is None else max(self.range_high, bar.high)
                self.range_low = bar.low if self.range_low is None else min(self.range_low, bar.low)
                self.previous_close = bar.close
                return None
            self.state = OrbState.ARMED  # this bar is the first possible breakout bar

        if self.config.entry_deadline_ms is not None and bar.available_at_ms >= self.config.entry_deadline_ms:
            self.state = OrbState.EXPIRED
            return None

        assert self.range_high is not None and self.range_low is not None
        trigger = self.range_high + self.config.breakout_buffer_ticks * self.config.tick
        crossed = (
            self.previous_close is not None and self.previous_close <= trigger
            and bar.close > trigger and is_candidate
        )
        self.previous_close = bar.close
        if not crossed:
            return None

        self.trigger = trigger
        self.stop = _floor_tick(self.range_low, self.config.tick)
        self.state = OrbState.PROPOSED
        expiry_ms = 2 * self.config.base_interval_minutes * 60_000
        if self.config.entry_deadline_ms is not None:
            expiry_ms = min(expiry_ms, self.config.entry_deadline_ms - bar.available_at_ms)
        proposal = Proposal(
            symbol=self.symbol, quantity=Decimal(0), signal_price=bar.close, stop=self.stop, target=None,
            decision_time_ms=bar.available_at_ms, strategy_id=STRATEGY_ID, expiry_ms=expiry_ms,
        )
        self.proposals.append(proposal)
        return proposal

    def on_rejected_or_expired(self) -> None:
        """Returns to ARMED; a fresh crossing (previous close falling back to/below trigger,
        then a later strict re-cross) is required before another proposal."""
        if self.state == OrbState.PROPOSED:
            self.state = OrbState.ARMED

    def on_filled(self, entry_fill: Decimal) -> Decimal:
        """Target is computed from the actual fill, never the signal close."""
        risk = entry_fill - self.stop
        target = _ceil_tick(entry_fill + self.config.target_r * risk, self.config.tick)
        self.state = OrbState.IN_POSITION
        self.filled_today = True
        return target

    def on_position_closed(self) -> None:
        self.state = OrbState.DONE

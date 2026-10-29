"""`vwap_touch_hold_v1` (docs/strategies/vwap.md, STRAT-VWAP-001).

Standalone completed-bar state machine: WARMUP -> IDLE -> ARMED -> PULLBACK -> PROPOSED ->
IN_POSITION -> DONE/EXPIRED. Produces `rsys.orders.Proposal` objects; sizing, acceptance and
execution are external concerns. Bar-price proxy `p = (high + low + close) / 3`; session
VWAP proxy is the cumulative `sum(p * volume) / sum(volume)`, reset each session.

Honesty note: not wired into `PhaseEngine` or a real multi-symbol replay feed; the scanner
candidate flag is supplied by the caller per bar -- see `rsys.scanner` and
docs/M2_IMPLEMENTATION_STATUS.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import Enum

from rsys.orders import Proposal

STRATEGY_ID = "vwap_touch_hold_v1"
TICK = Decimal("0.01")


def _floor_tick(value: Decimal, tick: Decimal = TICK) -> Decimal:
    return value.quantize(tick, rounding=ROUND_FLOOR)


def _ceil_tick(value: Decimal, tick: Decimal = TICK) -> Decimal:
    return value.quantize(tick, rounding=ROUND_CEILING)


class VwapState(str, Enum):
    WARMUP = "WARMUP"
    IDLE = "IDLE"
    ARMED = "ARMED"
    PULLBACK = "PULLBACK"
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
class VwapConfig:
    warmup_bars: int = 20
    slope_lookback_bars: int = 5
    pullback_max_bars: int = 5
    confirmation_buffer_ticks: int = 1
    base_interval_minutes: int = 1
    entry_deadline_ms: int | None = None
    target_r: Decimal = Decimal(2)
    tick: Decimal = TICK


class VwapStrategy:
    """One instrument, one session. Call `on_completed_bar` for every completed bar in
    chronological order; a non-None return is a new proposal."""

    def __init__(self, symbol: str, config: VwapConfig | None = None):
        self.symbol = symbol
        self.config = config or VwapConfig()
        self.state = VwapState.WARMUP
        self.cumulative_pv = Decimal(0)
        self.cumulative_volume = Decimal(0)
        self._vwap_history: list[Decimal | None] = []
        self._bar_count = 0
        self._previous_high: Decimal | None = None
        self.touch_low: Decimal | None = None
        self.window_lows: list[Decimal] = []
        self.age = 0
        self.stop: Decimal | None = None
        self.proposals: list[Proposal] = []

    def _update_vwap(self, bar: CompletedBar) -> Decimal | None:
        proxy = (bar.high + bar.low + bar.close) / 3
        self.cumulative_pv += proxy * bar.volume
        self.cumulative_volume += bar.volume
        vwap = self.cumulative_pv / self.cumulative_volume if self.cumulative_volume > 0 else None
        self._vwap_history.append(vwap)
        return vwap

    def _slope_rising(self) -> bool:
        lookback = self.config.slope_lookback_bars
        if len(self._vwap_history) <= lookback:
            return False
        current, past = self._vwap_history[-1], self._vwap_history[-1 - lookback]
        return current is not None and past is not None and current > past

    def on_completed_bar(self, bar: CompletedBar, *, is_candidate: bool) -> Proposal | None:
        if self.state in (VwapState.DONE, VwapState.EXPIRED, VwapState.PROPOSED, VwapState.IN_POSITION):
            return None

        previous_high = self._previous_high
        vwap = self._update_vwap(bar)
        self._bar_count += 1
        self._previous_high = bar.high

        if self.config.entry_deadline_ms is not None and bar.available_at_ms >= self.config.entry_deadline_ms:
            self.state = VwapState.EXPIRED
            return None

        if self.state == VwapState.WARMUP:
            if self._bar_count >= self.config.warmup_bars and self.cumulative_volume > 0:
                self.state = VwapState.IDLE
            return None

        if vwap is None:
            return None  # zero cumulative volume: unavailable, never treated as zero

        if self.state == VwapState.IDLE:
            if bar.close > vwap and self._slope_rising():
                self.state = VwapState.ARMED
            return None

        if self.state == VwapState.ARMED:
            if bar.low <= vwap and bar.close >= vwap:
                self.state = VwapState.PULLBACK
                self.touch_low = bar.low
                self.window_lows = [bar.low]
                self.age = 1
                return None
            if bar.close < vwap or not self._slope_rising():
                self.state = VwapState.IDLE
            return None

        if self.state == VwapState.PULLBACK:
            return self._on_pullback_bar(bar, vwap, previous_high, is_candidate=is_candidate)

        return None

    def _on_pullback_bar(
        self, bar: CompletedBar, vwap: Decimal, previous_high: Decimal | None, *, is_candidate: bool,
    ) -> Proposal | None:
        self.age += 1
        self.window_lows.append(bar.low)

        if bar.close < vwap or not self._slope_rising():
            self.state = VwapState.IDLE
            return None

        buffer = self.config.confirmation_buffer_ticks * self.config.tick
        confirmed = (
            2 <= self.age <= self.config.pullback_max_bars and bar.close > vwap + buffer
            and previous_high is not None and bar.close > previous_high + buffer and is_candidate
        )
        if confirmed:
            self.stop = _floor_tick(min(self.window_lows) - self.config.tick, self.config.tick)
            self.state = VwapState.PROPOSED
            expiry_ms = 2 * self.config.base_interval_minutes * 60_000
            if self.config.entry_deadline_ms is not None:
                expiry_ms = min(expiry_ms, self.config.entry_deadline_ms - bar.available_at_ms)
            proposal = Proposal(
                symbol=self.symbol, quantity=Decimal(0), signal_price=bar.close, stop=self.stop, target=None,
                decision_time_ms=bar.available_at_ms, strategy_id=STRATEGY_ID, expiry_ms=expiry_ms,
            )
            self.proposals.append(proposal)
            return proposal

        if self.age >= self.config.pullback_max_bars:
            self.state = VwapState.IDLE  # window expired without confirmation
        return None

    def on_rejected_or_expired(self) -> None:
        if self.state == VwapState.PROPOSED:
            self.state = VwapState.IDLE  # require a new arm and later touch

    def on_filled(self, entry_fill: Decimal) -> Decimal:
        risk = entry_fill - self.stop
        target = _ceil_tick(entry_fill + self.config.target_r * risk, self.config.tick)
        self.state = VwapState.IN_POSITION
        return target

    def on_position_closed(self) -> None:
        self.state = VwapState.DONE

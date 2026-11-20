"""Intraminute entries using committed history and a provisional view of one bar."""

from bisect import bisect_right
from copy import copy
from dataclasses import replace
from decimal import Decimal

from rsys.data.features import compute_dollar_volume_eligible
from rsys.strategies.models import CandidateInput, OrbState, VwapState
from rsys.strategies.orb import OrbStrategy
from rsys.strategies.scanner import rank_candidates
from rsys.strategies.vwap import VwapStrategy

BASE_STRATEGIES = {
    "orb_forming_cross_v1": "orb_close_cross_v1",
    "vwap_forming_confirm_v1": "vwap_touch_hold_v1",
}


class TapeVolumeHistory:
    """Cumulative eligible volume at the same elapsed instant on a prior day."""

    def __init__(self, prints, duration_ms):
        self.times, self.volumes = [], []
        self.close = None
        self.duration_ns = duration_ms * 1_000_000
        volume = Decimal(0)
        for p in prints:
            if p.updates_price:
                self.close = p.price
            if p.updates_volume:
                self.times.append(p.offset_ns)
                volume += p.size
                self.volumes.append(volume)

    def volume_at(self, elapsed_ns):
        if elapsed_ns >= self.duration_ns:
            return None
        i = bisect_right(self.times, elapsed_ns)
        return self.volumes[i - 1] if i else Decimal(0)


class TradeScanner:
    def __init__(self, history, *, lookback, top_n, max_age_ms, min_dollar_volume):
        self.history = history  # newest prior session first, independently per symbol
        self.top_n, self.max_age_ns = top_n, max_age_ms * 1_000_000
        self.lookback = lookback
        self.eligible = {}
        for symbol, days in history.items():
            prior = days[0]
            self.eligible[symbol] = compute_dollar_volume_eligible(
                prior_session_volume=prior.volumes[-1] if prior.volumes else None,
                prior_session_close=prior.close,
                min_dollar_volume=min_dollar_volume,
            )
        self.volume = dict.fromkeys(history, Decimal(0))
        self.opens, self.prices, self.last = {}, {}, {}

    def observe(self, p):
        if p.updates_volume:
            self.volume[p.symbol] += p.size
        if p.updates_price:
            self.opens.setdefault(p.symbol, p.price)
            self.prices[p.symbol] = p.price
            self.last[p.symbol] = p.offset_ns

    def rank(self, elapsed_ns):
        rows, inputs = {}, []
        for symbol, history in self.history.items():
            prev = history[0]
            price, opening = self.prices.get(symbol), self.opens.get(symbol)
            selected = [h for h in history if elapsed_ns < h.duration_ns][
                : self.lookback
            ]
            samples = [h.volume_at(elapsed_ns) for h in selected]
            mean = (
                sum(samples, Decimal(0)) / self.lookback
                if len(samples) == self.lookback
                else None
            )
            gain = price / prev.close - 1 if price and prev.close else None
            gap = opening / prev.close - 1 if opening and prev.close else None
            rvol = self.volume[symbol] / mean if mean else None
            fresh = (
                price is not None and elapsed_ns - self.last[symbol] <= self.max_age_ns
            )
            liquid = self.eligible[symbol]
            rows[symbol] = {
                "price": price,
                "opening_gap": gap,
                "gain": gain,
                "volume": self.volume[symbol],
                "prior_mean_volume": mean,
                "rvol": rvol,
                "fresh": fresh,
                "dollar_volume_eligible": liquid,
            }
            inputs.append(CandidateInput(symbol, rvol if fresh else None, gain, liquid))
        return rank_candidates(inputs, top_n=self.top_n), rows


def preview_entry(strategy, bar, previous_tick, available_at_ns):
    """Run the strategy with this partial bar as a prospective close.

    Only an emitted proposal commits its lifecycle. Warmup, VWAP, range and
    pullback history advance once per genuinely completed minute.
    """
    trial = copy(strategy)
    trial.proposals = []
    if isinstance(trial, OrbStrategy):
        variant = "orb_forming_cross_v1"
        if (
            trial.state not in (OrbState.ARMED, OrbState.COLLECT_RANGE)
            or bar.bar_start_ms < trial._range_end_ms
        ):
            return None
        if previous_tick is not None:
            trial.previous_close = previous_tick
    elif isinstance(trial, VwapStrategy):
        if trial.state != VwapState.PULLBACK:
            return None
        variant = "vwap_forming_confirm_v1"
        trial._vwap_history = strategy._vwap_history[
            -strategy.config.slope_lookback_bars :
        ]
        trial.window_lows = strategy.window_lows.copy()
    else:
        raise TypeError("Unsupported intrabar strategy")
    proposal = trial.on_completed_bar(bar, is_candidate=True)
    if proposal is None:
        return None
    proposal = replace(
        proposal,
        strategy_id=variant,
        proposal_id=f"{variant}:{available_at_ns}:{strategy.symbol}",
    )
    strategy.state, strategy.stop = trial.state, trial.stop
    strategy.proposals.append(proposal)
    return proposal

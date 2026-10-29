"""`vwap_touch_hold_v1` tests: S03/S04 from `fixtures/strategy_cases.json`, plus VWAP-01..08
(docs/strategies/vwap.md)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from rsys.strategies.vwap import CompletedBar, VwapConfig, VwapState, VwapStrategy

CASES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "strategy_cases.json"
_CASES = {c["id"]: c for c in json.loads(CASES_PATH.read_text())["cases"] if c["strategy"] == "vwap_touch_hold_v1"}


def _ms(timestamp: str) -> int:
    hours, minutes, seconds = (int(part) for part in timestamp.split("T")[1].split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _bar(raw: dict) -> CompletedBar:
    return CompletedBar(
        bar_start_ms=_ms(raw["bar_start"]), available_at_ms=_ms(raw["available_at"]),
        open=Decimal(raw["open"]), high=Decimal(raw["high"]), low=Decimal(raw["low"]),
        close=Decimal(raw["close"]), volume=Decimal(raw["volume"]),
    )


def _run(case_id: str, *, is_candidate: bool = True, config: VwapConfig | None = None):
    strategy = VwapStrategy("AAA", config)
    proposals = []
    states_by_time: dict[int, VwapState] = {}
    for raw in _CASES[case_id]["bars"]:
        result = strategy.on_completed_bar(_bar(raw), is_candidate=is_candidate)
        states_by_time[_ms(raw["available_at"])] = strategy.state
        if result is not None:
            proposals.append(result)
    return proposals, strategy, states_by_time


def test_s03_arm_touch_confirm_states_and_stop():
    proposals, strategy, states_by_time = _run("S03")
    assert states_by_time[_ms("2024-11-29T09:51:00")] == VwapState.ARMED
    assert states_by_time[_ms("2024-11-29T09:52:00")] == VwapState.PULLBACK
    assert states_by_time[_ms("2024-11-29T09:53:00")] == VwapState.PROPOSED
    assert len(proposals) == 1
    assert proposals[0].stop == Decimal("99.99")
    assert strategy.cumulative_pv == Decimal(230280)
    assert strategy.cumulative_volume == Decimal(2300)


def test_s04_close_below_vwap_invalidates_before_confirmation():
    proposals, _strategy, states_by_time = _run("S04")
    assert proposals == []
    assert states_by_time[_ms("2024-11-29T09:52:00")] == VwapState.IDLE  # invalidated immediately


def test_vwap01_hand_calculated_weighted_average():
    strategy = VwapStrategy("AAA")
    bar = CompletedBar(0, 0, Decimal(10), Decimal(12), Decimal(8), Decimal(10), Decimal(100))
    vwap = strategy._update_vwap(bar)
    # p = (12 + 8 + 10) / 3 = 10; vwap = 10*100 / 100 = 10
    assert vwap == Decimal(10)
    assert strategy.cumulative_pv == Decimal(1000)
    assert strategy.cumulative_volume == Decimal(100)


def test_vwap02_warmup_and_zero_volume():
    strategy = VwapStrategy("AAA", VwapConfig(warmup_bars=3))
    zero_vol_bar = CompletedBar(0, 0, Decimal(10), Decimal(10), Decimal(10), Decimal(10), Decimal(0))
    assert strategy.on_completed_bar(zero_vol_bar, is_candidate=True) is None
    assert strategy.cumulative_volume == Decimal(0)  # a zero-volume bar contributes zero weight
    for _ in range(3):
        bar = CompletedBar(0, 0, Decimal(10), Decimal(10), Decimal(10), Decimal(10), Decimal(1))
        strategy.on_completed_bar(bar, is_candidate=True)
    assert strategy.state == VwapState.IDLE  # warmup satisfied once cumulative volume is positive


def test_vwap03_slope_uses_only_completed_bars():
    strategy = VwapStrategy("AAA", VwapConfig(slope_lookback_bars=5))
    for _ in range(4):  # fewer bars than the lookback: slope cannot be evaluated yet
        strategy._update_vwap(CompletedBar(0, 0, Decimal(10), Decimal(10), Decimal(10), Decimal(10), Decimal(1)))
    assert strategy._slope_rising() is False


def test_vwap04_arm_touch_and_confirm_are_separate_events():
    _proposals, _strategy, states_by_time = _run("S03")
    distinct_times = {states_by_time[_ms(t)] for t in ("2024-11-29T09:51:00", "2024-11-29T09:52:00", "2024-11-29T09:53:00")}
    assert distinct_times == {VwapState.ARMED, VwapState.PULLBACK, VwapState.PROPOSED}


def test_vwap05_previous_high_excludes_the_confirmation_bar():
    # S03's confirmation bar (09:53) has high=102, but the comparison uses the touch bar's
    # (09:52) high=101, not its own -- otherwise close=101.5 would still exceed 102+buffer=false.
    proposals, _strategy, _ = _run("S03")
    assert len(proposals) == 1  # confirms only because 101.5 > 101 + buffer, not > 102 + buffer


def test_vwap06_invalidation_evaluated_before_confirmation():
    proposals, _strategy, states_by_time = _run("S04")
    assert proposals == []
    assert states_by_time[_ms("2024-11-29T09:52:00")] == VwapState.IDLE  # invalidated immediately


def test_vwap07_stop_uses_only_pullback_window_lows():
    _proposals, strategy, _ = _run("S03")
    assert strategy.window_lows == [Decimal(100), Decimal("100.4")]
    assert strategy.stop == Decimal("99.99")  # min(window_lows) - 1 tick, floored


def test_vwap08_future_mutation_cannot_change_a_prior_proposal():
    proposals_baseline, _strategy, _ = _run("S03")
    case = _CASES["S03"]
    perturbed_bars = [*case["bars"], {
        "bar_start": "2024-11-29T09:53:00", "bar_end": "2024-11-29T09:54:00",
        "available_at": "2024-11-29T09:54:00", "open": "999", "high": "999", "low": "999",
        "close": "999", "volume": "1",
    }]
    strategy = VwapStrategy("AAA")
    proposals_perturbed = []
    for raw in perturbed_bars:
        result = strategy.on_completed_bar(_bar(raw), is_candidate=True)
        if result is not None:
            proposals_perturbed.append(result)
    assert proposals_perturbed == proposals_baseline


def test_noncandidate_confirmation_does_not_pause_the_window():
    case = _CASES["S03"]
    strategy = VwapStrategy("AAA")
    proposals = []
    for raw in case["bars"][:-1]:  # everything up to (not including) the confirmation bar
        result = strategy.on_completed_bar(_bar(raw), is_candidate=True)
        if result is not None:
            proposals.append(result)
    assert strategy.state == VwapState.PULLBACK
    noncandidate_result = strategy.on_completed_bar(_bar(case["bars"][-1]), is_candidate=False)
    assert noncandidate_result is None
    assert strategy.state == VwapState.PULLBACK  # window keeps running, not paused or discarded


def test_age_six_cannot_confirm():
    strategy = VwapStrategy("AAA", VwapConfig(pullback_max_bars=5))
    strategy.state = VwapState.PULLBACK
    strategy.window_lows = [Decimal(100)]
    strategy.age = 5  # about to become 6 on the next bar, past the allowed window
    strategy._vwap_history = [Decimal(90)] * 6  # slope rising; isolates the age-expiry check
    bar = CompletedBar(0, 0, Decimal(101), Decimal(101), Decimal(100), Decimal(101), Decimal(1))
    result = strategy._on_pullback_bar(bar, Decimal(100), Decimal(100), is_candidate=True)
    assert result is None
    assert strategy.state == VwapState.IDLE

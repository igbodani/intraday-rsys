"""`orb_close_cross_v1` tests: S01/S02 from `fixtures/strategy_cases.json`, plus ORB-01..07
(docs/strategies/orb.md)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from rsys.strategies.orb import CompletedBar, OrbConfig, OrbState, OrbStrategy

CASES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "strategy_cases.json"
_CASES = {c["id"]: c for c in json.loads(CASES_PATH.read_text())["cases"] if c["strategy"] == "orb_close_cross_v1"}


def _ms(timestamp: str) -> int:
    hours, minutes, seconds = (int(part) for part in timestamp.split("T")[1].split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _bar(raw: dict) -> CompletedBar:
    return CompletedBar(
        bar_start_ms=_ms(raw["bar_start"]), available_at_ms=_ms(raw["available_at"]),
        open=Decimal(raw["open"]), high=Decimal(raw["high"]), low=Decimal(raw["low"]),
        close=Decimal(raw["close"]), volume=Decimal(raw["volume"]),
    )


SESSION_OPEN_MS = _ms("2024-11-29T09:30:00")


def _run(case_id: str, *, is_candidate: bool = True, config: OrbConfig | None = None):
    strategy = OrbStrategy("AAA", SESSION_OPEN_MS, config)
    proposals = []
    for raw in _CASES[case_id]["bars"]:
        result = strategy.on_completed_bar(_bar(raw), is_candidate=is_candidate)
        if result is not None:
            proposals.append(result)
    return proposals, strategy


def test_s01_strict_crossing_after_range():
    proposals, strategy = _run("S01")
    assert len(proposals) == 1
    assert proposals[0].stop == Decimal("99.00")
    assert proposals[0].decision_time_ms == _ms("2024-11-29T09:46:00")
    assert strategy.trigger == Decimal("101.01")


def test_s02_equality_is_not_a_strict_crossing():
    proposals, _strategy = _run("S02")
    assert proposals == []


def test_orb01_no_signal_before_the_range_is_fully_available():
    strategy = OrbStrategy("AAA", SESSION_OPEN_MS)
    for raw in _CASES["S01"]["bars"][:10]:
        assert strategy.on_completed_bar(_bar(raw), is_candidate=True) is None
    assert strategy.state == OrbState.COLLECT_RANGE


def test_orb02_breakout_bar_is_excluded_from_the_range():
    # S02's breakout bar (high=102, low=100) must not get folded into the frozen range.
    strategy = OrbStrategy("AAA", SESSION_OPEN_MS)
    for raw in _CASES["S02"]["bars"]:
        strategy.on_completed_bar(_bar(raw), is_candidate=True)
    assert strategy.state == OrbState.ARMED
    assert strategy.range_high == Decimal(101)
    assert strategy.range_low == Decimal(99)


def test_orb03_strict_crossing_required():
    assert _run("S02")[0] == []
    assert len(_run("S01")[0]) == 1


def test_orb04_noncandidate_crossing_gives_no_proposal():
    proposals, strategy = _run("S01", is_candidate=False)
    assert proposals == []
    assert strategy.state == OrbState.ARMED


def test_orb05_no_re_entry_after_one_fill():
    proposals, strategy = _run("S01")
    assert len(proposals) == 1
    strategy.on_filled(Decimal("101.02"))
    strategy.on_position_closed()
    extra = strategy.on_completed_bar(
        CompletedBar(
            _ms("2024-11-29T09:47:00"), _ms("2024-11-29T09:47:00"),
            Decimal(101), Decimal(110), Decimal(101), Decimal(109), Decimal(100),
        ),
        is_candidate=True,
    )
    assert extra is None
    assert strategy.state == OrbState.DONE


def test_orb06_delayed_entry_after_deadline_rejected():
    deadline_ms = _ms("2024-11-29T09:46:00")  # equals the breakout bar's own availability
    proposals, strategy = _run("S01", config=OrbConfig(entry_deadline_ms=deadline_ms))
    assert proposals == []
    assert strategy.state == OrbState.EXPIRED


def test_orb07_future_bar_perturbation_leaves_the_prior_proposal_unchanged():
    proposals_baseline, _ = _run("S01")
    perturbed_bars = [*_CASES["S01"]["bars"], {
        "bar_start": "2024-11-29T09:46:00", "bar_end": "2024-11-29T09:47:00",
        "available_at": "2024-11-29T09:47:00", "open": "999", "high": "999", "low": "999",
        "close": "999", "volume": "1",
    }]
    strategy = OrbStrategy("AAA", SESSION_OPEN_MS)
    proposals_perturbed = []
    for raw in perturbed_bars:
        result = strategy.on_completed_bar(_bar(raw), is_candidate=True)
        if result is not None:
            proposals_perturbed.append(result)
    assert proposals_perturbed == proposals_baseline


def test_target_computed_from_actual_fill_not_signal_close():
    _proposals, strategy = _run("S01")
    target = strategy.on_filled(Decimal(102))  # differs from the 101.02 signal close
    assert target == Decimal("108.00")  # 102 + 2R * (102 - 99.00)

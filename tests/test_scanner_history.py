"""Historical scanner-mode composition tests (M2-02a, Step 3 continuation):
`HistoryProvider`/`FixtureHistoryProvider` feeding `features.py`'s causal math into the
exact `scanner_inputs` shape the real runners already consume, replacing supplied scores
with calendar-aware history while keeping the supplied-score fixture mode untouched."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from rsys.features import HistorySession
from rsys.scanner import NOT_ELIGIBLE, rank_candidates
from rsys.scanner_history import (
    FixtureHistoryProvider,
    SyntheticSymbolHistory,
    build_scanner_inputs,
    candidate_input,
)
from rsys.standalone_run import SizingPolicy, StandaloneRunner
from rsys.strategies.orb import OrbStrategy

DECISION_DATE = date(2024, 11, 29)


def _ms(timestamp: str) -> int:
    hours, minutes, seconds = (int(part) for part in timestamp.split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


@dataclass(frozen=True)
class _Bar:
    available_at_ms: int
    close: Decimal
    volume: Decimal


def test_candidate_input_matches_the_h01_hand_calculation():
    provider = FixtureHistoryProvider(
        {
            "AAA": SyntheticSymbolHistory(
                sessions=[
                    HistorySession(date(2024, 12, 2), 390, Decimal(800), True),
                    HistorySession(date(2024, 11, 29), 210, Decimal(400), True),
                    HistorySession(date(2024, 11, 27), 390, Decimal(600), True),
                ],
                previous_session_close=Decimal(100),
                prior_session_volume=Decimal(2000000),
                prior_session_close_for_dollar_volume=Decimal(50),
            )
        }
    )
    row = candidate_input(
        symbol="AAA", decision_date=date(2024, 12, 3), elapsed_minutes=60,
        current_volume=Decimal(1200), latest_close=Decimal(103), provider=provider,
        lookback_sessions=2, min_dollar_volume=Decimal(1000000),
    )
    assert row.rvol == Decimal(2)
    assert row.gain == Decimal("0.03")
    assert row.dollar_volume_eligible is True


def test_candidate_input_excludes_on_insufficient_dollar_volume():
    provider = FixtureHistoryProvider(
        {
            "BBB": SyntheticSymbolHistory(
                sessions=[HistorySession(date(2024, 11, 27), 390, Decimal(600), True)],
                previous_session_close=Decimal(10),
                prior_session_volume=Decimal(1000),
                prior_session_close_for_dollar_volume=Decimal(10),
            )
        }
    )
    row = candidate_input(
        symbol="BBB", decision_date=DECISION_DATE, elapsed_minutes=60,
        current_volume=Decimal(1200), latest_close=Decimal("10.3"), provider=provider,
        lookback_sessions=1, min_dollar_volume=Decimal(1000000),
    )
    assert row.dollar_volume_eligible is False
    result = rank_candidates([row], top_n=10)
    assert result.excluded["BBB"] == NOT_ELIGIBLE


def test_build_scanner_inputs_matches_the_runner_scanner_inputs_shape():
    provider = FixtureHistoryProvider(
        {
            "AAA": SyntheticSymbolHistory(
                sessions=[HistorySession(date(2024, 11, 27), 390, Decimal(600), True)],
                previous_session_close=Decimal(100),
                prior_session_volume=Decimal(2000000),
                prior_session_close_for_dollar_volume=Decimal(50),
            )
        }
    )
    bars = {
        "AAA": [
            _Bar(_ms("09:31:00"), Decimal(101), Decimal(600)),
            _Bar(_ms("09:46:00"), Decimal(103), Decimal(600)),
        ]
    }
    scanner_inputs = build_scanner_inputs(
        bars_by_symbol=bars, provider=provider, decision_date=DECISION_DATE,
        lookback_sessions=1, min_dollar_volume=Decimal(1000000),
    )
    row = scanner_inputs[_ms("09:46:00")]["AAA"]
    assert row[2] is True  # dollar-volume eligible
    assert row[0] == Decimal(2)  # 1200 cumulative / 600 mean
    assert row[1] == Decimal("0.03")  # 103/100 - 1


def test_build_scanner_inputs_is_causal_a_later_bar_never_changes_an_earlier_decision():
    provider = FixtureHistoryProvider(
        {
            "AAA": SyntheticSymbolHistory(
                sessions=[HistorySession(date(2024, 11, 27), 390, Decimal(600), True)],
                previous_session_close=Decimal(100),
                prior_session_volume=Decimal(2000000),
                prior_session_close_for_dollar_volume=Decimal(50),
            )
        }
    )
    early_bar = _Bar(_ms("09:46:00"), Decimal(103), Decimal(600))
    base = build_scanner_inputs(
        bars_by_symbol={"AAA": [early_bar]}, provider=provider, decision_date=DECISION_DATE,
        lookback_sessions=1, min_dollar_volume=Decimal(1000000),
    )
    with_future = build_scanner_inputs(
        bars_by_symbol={
            "AAA": [early_bar, _Bar(_ms("09:47:00"), Decimal(500), Decimal(999999))]
        },
        provider=provider, decision_date=DECISION_DATE,
        lookback_sessions=1, min_dollar_volume=Decimal(1000000),
    )
    assert with_future[_ms("09:46:00")]["AAA"] == base[_ms("09:46:00")]["AAA"]


def test_historical_scanner_mode_composes_through_a_real_standalone_session():
    """The historical mode's output plugs into `StandaloneRunner.run_session` exactly like
    the supplied-score fixture mode: AAA (sufficient dollar volume) is accepted, BBB (same
    breakout, insufficient dollar volume) is excluded -- without changing the runner."""
    import json
    from pathlib import Path

    from rsys.standalone_run import ExecutionPolicy
    from rsys.strategies.orb import CompletedBar

    cases_path = Path(__file__).resolve().parents[1] / "fixtures" / "strategy_cases.json"
    s01 = next(c for c in json.loads(cases_path.read_text())["cases"] if c["id"] == "S01")

    def bar(raw):
        return CompletedBar(
            bar_start_ms=_ms(raw["bar_start"].split("T")[1]),
            available_at_ms=_ms(raw["available_at"].split("T")[1]),
            open=Decimal(raw["open"]), high=Decimal(raw["high"]), low=Decimal(raw["low"]),
            close=Decimal(raw["close"]), volume=Decimal(raw["volume"]),
        )

    next_open_bar = {
        "bar_start": "2024-11-29T09:46:00", "available_at": "2024-11-29T09:47:00",
        "open": "102", "high": "103", "low": "101.5", "close": "102.5", "volume": "100",
    }
    bars_by_symbol = {
        "AAA": [bar(raw) for raw in [*s01["bars"], next_open_bar]],
        "BBB": [bar(raw) for raw in [*s01["bars"], next_open_bar]],
    }
    simple_history = SyntheticSymbolHistory(
        sessions=[HistorySession(date(2024, 11, 27), 390, Decimal(600), True)],
        previous_session_close=Decimal(97), prior_session_volume=Decimal(0),
        prior_session_close_for_dollar_volume=Decimal(0),
    )
    provider = FixtureHistoryProvider(
        {
            "AAA": SyntheticSymbolHistory(
                sessions=simple_history.sessions,
                previous_session_close=simple_history.previous_session_close,
                prior_session_volume=Decimal(2000000),
                prior_session_close_for_dollar_volume=Decimal(50),
            ),
            "BBB": simple_history,  # zero prior dollar volume: ineligible
        }
    )
    scanner_inputs = build_scanner_inputs(
        bars_by_symbol=bars_by_symbol, provider=provider, decision_date=DECISION_DATE,
        lookback_sessions=1, min_dollar_volume=Decimal(1000000),
    )

    strategies = {"AAA": OrbStrategy("AAA", _ms("09:30:00")), "BBB": OrbStrategy("BBB", _ms("09:30:00"))}
    from rsys.portfolio import Portfolio

    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    runner = StandaloneRunner(
        strategies, portfolio, SizingPolicy(max_quantity=Decimal(10)), execution=ExecutionPolicy()
    )
    runner.run_session(bars_by_symbol, scanner_inputs)

    decision = next(d for d in runner.decisions if d.available_at_ms == _ms("09:46:00"))
    assert decision.proposed == ["AAA"]
    assert decision.accepted == ["AAA"]
    assert all(f.symbol != "BBB" for f in runner.fills)

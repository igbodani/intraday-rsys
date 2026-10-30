"""End-to-end `TradeInputRunner` integration tests (M2-01T wired into a real engine, Step 2
of docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md): scanner -> ORB proposal -> acceptance -> a later
eligible TRADE print fills the entry/exit, never an invented bar open, through the real
`SeriesRouter`/`ReplayRunner`/`Portfolio`/`TradeExecutionAdapter` modules."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from rsys.account.models import SizingPolicy
from rsys.account.portfolio import Portfolio
from rsys.execution.errors import IncompleteSessionError
from rsys.execution.models import ExecutionPolicy
from rsys.execution.trade import TradeInputRunner
from rsys.strategies.orb import OrbStrategy

CASES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "strategy_cases.json"
_S01 = next(c for c in json.loads(CASES_PATH.read_text())["cases"] if c["id"] == "S01")


def _ms(timestamp: str) -> int:
    hours, minutes, seconds = (int(part) for part in timestamp.split("T")[1].split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


SESSION_OPEN_MS = _ms("2024-11-29T09:30:00")
_NEXT_OPEN_BAR = {
    "bar_start": "2024-11-29T09:46:00", "bar_end": "2024-11-29T09:47:00",
    "available_at": "2024-11-29T09:47:00", "open": "102", "high": "103", "low": "101.5",
    "close": "102.5", "volume": "100",
}


class _Trade:
    __slots__ = ("available_at_ms", "event_time_ms", "price", "trade_id")

    def __init__(self, trade_id, event_time_ms, available_at_ms, price):
        self.trade_id = trade_id
        self.event_time_ms = event_time_ms
        self.available_at_ms = available_at_ms
        self.price = Decimal(price)


def _bar_to_trades(raw: dict, symbol: str) -> list[_Trade]:
    """Four trades per minute bucket -- at +0/15/30/45s -- so the trade-built bar's own
    open/high/low/close exactly match the declared fixture bar (open=earliest, close=latest,
    high=max, low=min price among the bucket's contributing trades)."""
    start = _ms(raw["bar_start"])
    prices = [raw["open"], raw["high"], raw["low"], raw["close"]]
    trades = []
    for i, price in enumerate(prices):
        t = start + i * 15_000
        trades.append(_Trade(f"{symbol}:{t}:{i}", t, t, price))
    return trades


def _build_runner(*, cash="10000", risk_budget=None, max_quantity="10", execution=None):
    strategies = {"AAA": OrbStrategy("AAA", SESSION_OPEN_MS)}
    portfolio_cash = Decimal(cash)
    portfolio = Portfolio(
        total_cash=portfolio_cash, spendable_cash=portfolio_cash, unsettled_cash=Decimal(0)
    )
    sizing = SizingPolicy(
        risk_budget=Decimal(risk_budget) if risk_budget is not None else None,
        max_quantity=Decimal(max_quantity) if max_quantity is not None else None,
    )
    runner = TradeInputRunner(strategies, portfolio, sizing, execution=execution)
    trades = {
        "AAA": [
            t for raw in [*_S01["bars"], _NEXT_OPEN_BAR] for t in _bar_to_trades(raw, "AAA")
        ]
    }
    scanner_inputs = {_ms("2024-11-29T09:46:00"): {"AAA": (Decimal(2), Decimal("0.03"))}}
    return runner, {"trades": trades, "scanner": scanner_inputs}


def test_entry_fills_on_a_later_eligible_trade_not_an_invented_bar_open():
    runner, session = _build_runner()
    runner.run_session(session["trades"], session["scanner"])

    decision = next(
        d for d in runner.decisions if d.available_at_ms == _ms("2024-11-29T09:46:00")
    )
    assert decision.proposed == ["AAA"]
    assert decision.accepted == ["AAA"]

    fill = next(f for f in runner.fills if f.symbol == "AAA" and f.status == "FILLED")
    # The next bucket's first trade (the modeled "open" analogue) is at 09:46:00 itself,
    # which is the proposal's own decision instant -- not yet ready. The entry must fill on
    # a genuinely later print (09:46:15, the modeled "high" trade), never the bucket's own
    # first trade reused as if it were a bar open.
    assert fill.event_time_ms == _ms("2024-11-29T09:46:00") + 15_000
    assert fill.price == Decimal(103)
    assert fill.target is not None


def test_stop_trade_print_exits_the_position_at_its_own_reference_price():
    runner, session = _build_runner()
    # Append a bar whose trades walk straight through the ORB stop (range low) after entry.
    stop_bar = {
        "bar_start": "2024-11-29T09:47:00", "bar_end": "2024-11-29T09:48:00",
        "available_at": "2024-11-29T09:48:00", "open": "102.5", "high": "102.6",
        "low": "97", "close": "97.5", "volume": "50",
    }
    session["trades"]["AAA"].extend(_bar_to_trades(stop_bar, "AAA"))
    runner.run_session(session["trades"], session["scanner"])

    exit_fill = next(f for f in runner.fills if f.status == "EXIT")
    assert exit_fill.reason == "STOP_TRADE"
    assert exit_fill.price == Decimal(97)  # the adverse print reference, not the bar low
    assert "AAA" not in runner.portfolio.positions


def test_flatten_timer_uses_the_next_eligible_trade_not_a_bar_open():
    runner, session = _build_runner(
        execution=ExecutionPolicy(flatten_at_ms=_ms("2024-11-29T09:47:00") + 5_000)
    )
    trailer_bar = {
        "bar_start": "2024-11-29T09:47:00", "bar_end": "2024-11-29T09:48:00",
        "available_at": "2024-11-29T09:48:00", "open": "102.5", "high": "102.6",
        "low": "102.4", "close": "102.5", "volume": "10",
    }
    session["trades"]["AAA"].extend(_bar_to_trades(trailer_bar, "AAA"))
    runner.run_session(session["trades"], session["scanner"])

    exit_fill = next(f for f in runner.fills if f.status == "EXIT")
    assert exit_fill.reason == "FLATTEN"
    # First eligible trade strictly after the flatten boundary (09:47:15), not the bucket's
    # own open trade at 09:47:00 (which precedes the boundary).
    assert exit_fill.event_time_ms == _ms("2024-11-29T09:47:00") + 15_000


def test_missing_closure_trade_is_an_incomplete_attempt_not_a_silent_success():
    runner, session = _build_runner(
        execution=ExecutionPolicy(flatten_at_ms=_ms("2024-11-29T09:47:00") + 5_000)
    )
    # No further trades after the flatten boundary: the open position can never close.
    try:
        runner.run_session(session["trades"], session["scanner"])
        raised = False
    except IncompleteSessionError:
        raised = True
    assert raised
    assert "AAA" in runner.portfolio.positions

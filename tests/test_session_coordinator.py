"""Multi-session composition tests (Step 4 of docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md):
`SessionCoordinator` carries only persistent account state across sessions; daily
scanner/strategy/one-fill/timer state must reset every session, a position cannot silently
carry overnight, and only a fully-specified split is a supported corporate action."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from rsys.calendar import (
    CalendarCoverageError,
    session_duration_minutes,
    session_open_local,
    to_exchange_utc,
)
from rsys.portfolio import Portfolio
from rsys.session_coordinator import (
    IncompleteSessionError,
    SessionCoordinator,
    SplitAction,
    UnreconciledPositionError,
    UnsupportedCorporateActionError,
)
from rsys.standalone_run import ExecutionPolicy, SizingPolicy
from rsys.strategies.orb import CompletedBar, OrbStrategy

CASES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "strategy_cases.json"
_S01 = next(c for c in json.loads(CASES_PATH.read_text())["cases"] if c["id"] == "S01")

EARLY_CLOSE_DATE = date(2024, 11, 29)  # declared NYSE early close (calendar.py)
REGULAR_DATE = date(2024, 12, 2)
HOLIDAY_DATE = date(2024, 11, 28)  # Thanksgiving


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
_NEXT_OPEN_BAR = {
    "bar_start": "2024-11-29T09:46:00", "available_at": "2024-11-29T09:47:00",
    "open": "102", "high": "103", "low": "101.5", "close": "102.5", "volume": "100",
}
_SCANNER_AT_0946 = {_ms("2024-11-29T09:46:00"): {"AAA": (Decimal(2), Decimal("0.03"))}}


def _daily_bars() -> dict[str, list[CompletedBar]]:
    return {"AAA": [_bar(raw) for raw in [*_S01["bars"], _NEXT_OPEN_BAR]]}


def _daily_bars_with_followup() -> dict[str, list[CompletedBar]]:
    """Adds one flat follow-up bar (open at 09:48) so a flatten timer at 09:48 has an
    eligible open to exit the already-filled position against."""
    followup = {
        "bar_start": "2024-11-29T09:48:00", "bar_end": "2024-11-29T09:49:00",
        "available_at": "2024-11-29T09:49:00",
        "open": "102", "high": "102", "low": "102", "close": "102", "volume": "100",
    }
    bars = _daily_bars()
    bars["AAA"].append(_bar(followup))
    return bars


def _coordinator(portfolio: Portfolio, *, execution_factory=None) -> SessionCoordinator:
    return SessionCoordinator(
        strategy_factory=lambda _d: {"AAA": OrbStrategy("AAA", SESSION_OPEN_MS)},
        portfolio=portfolio,
        sizing=SizingPolicy(max_quantity=Decimal(10)),
        execution_factory=execution_factory,
    )


def test_consecutive_sessions_independently_detect_the_same_breakout():
    """A fresh `OrbStrategy` every session means day 2's identical bar sequence produces its
    own fill, proving day 1's ARMED/PROPOSED/DONE state did not leak forward."""
    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    flatten_each_day = lambda _d: ExecutionPolicy(flatten_at_ms=_ms("2024-11-29T09:48:00"))
    coordinator = _coordinator(portfolio, execution_factory=flatten_each_day)

    coordinator.run_session(EARLY_CLOSE_DATE, _daily_bars_with_followup(), _SCANNER_AT_0946)
    assert "AAA" not in portfolio.positions  # flattened before the early close
    fills_day1 = [f for f in coordinator.results[0].fills if f.symbol == "AAA"]
    assert any(f.status == "FILLED" for f in fills_day1)
    assert any(f.status == "EXIT" for f in fills_day1)

    coordinator.run_session(REGULAR_DATE, _daily_bars_with_followup(), _SCANNER_AT_0946)
    fills_day2 = [f for f in coordinator.results[1].fills if f.symbol == "AAA"]
    assert any(f.status == "FILLED" for f in fills_day2)
    assert any(f.status == "EXIT" for f in fills_day2)


def test_cash_and_realized_pnl_accumulate_exactly_once_per_session():
    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    flatten_each_day = lambda _d: ExecutionPolicy(flatten_at_ms=_ms("2024-11-29T09:48:00"))
    coordinator = _coordinator(portfolio, execution_factory=flatten_each_day)

    coordinator.run_session(EARLY_CLOSE_DATE, _daily_bars_with_followup(), _SCANNER_AT_0946)
    pnl_after_day1 = portfolio.realized_pnl
    exit_day1 = next(f for f in coordinator.results[0].fills if f.status == "EXIT")
    assert pnl_after_day1 == exit_day1.net_pnl  # exactly one day's realized P&L so far

    coordinator.run_session(REGULAR_DATE, _daily_bars_with_followup(), _SCANNER_AT_0946)
    exit_day2 = next(f for f in coordinator.results[1].fills if f.status == "EXIT")
    # Day 2 adds exactly its own realized P&L to the running total -- never double-counted,
    # never dropped.
    assert portfolio.realized_pnl == pnl_after_day1 + exit_day2.net_pnl


def test_missing_data_symbol_never_fabricates_a_proposal():
    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    coordinator = SessionCoordinator(
        strategy_factory=lambda _d: {
            "AAA": OrbStrategy("AAA", SESSION_OPEN_MS),
            "BBB": OrbStrategy("BBB", SESSION_OPEN_MS),
        },
        portfolio=portfolio,
        sizing=SizingPolicy(max_quantity=Decimal(10)),
    )
    bars = _daily_bars()  # BBB has no entry at all: a genuine no-data symbol
    coordinator.run_session(EARLY_CLOSE_DATE, bars, _SCANNER_AT_0946)
    assert all("BBB" not in d.proposed for d in coordinator.results[0].decisions)
    assert all(f.symbol != "BBB" for f in coordinator.results[0].fills)


def test_early_close_flatten_timer_uses_the_actual_declared_close():
    """`execution_factory` derives the flatten instant from `session_duration_minutes`, so
    the early-close day's computed flatten point is strictly earlier than the same
    buffer-before-close rule would compute for a regular session."""
    buffer_minutes = 2

    def _flatten_ms_since_midnight(session_date: date) -> int:
        open_local = session_open_local(session_date)
        open_minutes = open_local.hour * 60 + open_local.minute
        return (open_minutes + session_duration_minutes(session_date) - buffer_minutes) * 60_000

    def flatten_before_close(session_date: date) -> ExecutionPolicy:
        return ExecutionPolicy(flatten_at_ms=_flatten_ms_since_midnight(session_date))

    regular_elapsed_ms = _flatten_ms_since_midnight(REGULAR_DATE)
    early_elapsed_ms = _flatten_ms_since_midnight(EARLY_CLOSE_DATE)
    assert early_elapsed_ms < regular_elapsed_ms  # the early close really is earlier

    # A flat follow-up bar exactly at the computed flatten instant gives the timer an
    # eligible open to exit against, proving the *early-close-derived* instant is what
    # actually fires (not some other fixed clock time).
    flatten_followup = CompletedBar(
        bar_start_ms=early_elapsed_ms, available_at_ms=early_elapsed_ms + 60_000,
        open=Decimal(102), high=Decimal(102), low=Decimal(102), close=Decimal(102),
        volume=Decimal(100),
    )
    bars = _daily_bars()
    bars["AAA"].append(flatten_followup)

    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    coordinator = _coordinator(portfolio, execution_factory=flatten_before_close)
    coordinator.run_session(EARLY_CLOSE_DATE, bars, _SCANNER_AT_0946)

    exit_fill = next(f for f in coordinator.results[0].fills if f.status == "EXIT")
    assert exit_fill.available_at_ms == early_elapsed_ms


def test_open_position_cannot_silently_carry_into_the_next_session():
    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    coordinator = _coordinator(portfolio)  # no flatten: position stays open at session end
    coordinator.run_session(EARLY_CLOSE_DATE, _daily_bars(), _SCANNER_AT_0946)
    assert "AAA" in portfolio.positions

    with pytest.raises(UnreconciledPositionError):
        coordinator.run_session(REGULAR_DATE, _daily_bars(), _SCANNER_AT_0946)


def test_required_flatten_with_no_eligible_open_remains_incomplete():
    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    # Flatten scheduled after every supplied bar: no eligible open exists to close it.
    coordinator = _coordinator(
        portfolio, execution_factory=lambda _d: ExecutionPolicy(flatten_at_ms=_ms("2024-11-29T09:59:00"))
    )
    bars = _daily_bars()  # entry fills at 09:47; no bar after that for the 09:59 flatten
    with pytest.raises(IncompleteSessionError):
        coordinator.run_session(EARLY_CLOSE_DATE, bars, _SCANNER_AT_0946)


def test_non_trading_day_is_rejected_before_any_session_state_is_built():
    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    coordinator = _coordinator(portfolio)
    with pytest.raises(CalendarCoverageError):
        coordinator.run_session(HOLIDAY_DATE, _daily_bars(), _SCANNER_AT_0946)
    assert coordinator.results == []


def test_a_saved_prior_session_context_is_unchanged_after_the_next_session_runs():
    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    flatten_each_day = lambda _d: ExecutionPolicy(flatten_at_ms=_ms("2024-11-29T09:48:00"))
    coordinator = _coordinator(portfolio, execution_factory=flatten_each_day)

    coordinator.run_session(EARLY_CLOSE_DATE, _daily_bars_with_followup(), _SCANNER_AT_0946)
    saved_context = coordinator.results[0].contexts[-1]
    before = saved_context.closed_bars("AAA")

    coordinator.run_session(REGULAR_DATE, _daily_bars_with_followup(), _SCANNER_AT_0946)
    after = saved_context.closed_bars("AAA")
    assert before == after


def test_exact_split_converts_position_units_without_changing_cash():
    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    coordinator = _coordinator(portfolio)  # no flatten: position stays open
    coordinator.run_session(EARLY_CLOSE_DATE, _daily_bars(), _SCANNER_AT_0946)
    position = portfolio.positions["AAA"]
    quantity_before, entry_before, stop_before = (
        position.quantity, position.entry_price, position.stop,
    )
    cash_before = portfolio.total_cash

    known_at_ns = int(to_exchange_utc(datetime(2024, 11, 29, 9, 0)).timestamp() * 1_000_000_000)
    coordinator.apply_split(
        SplitAction(symbol="AAA", ratio=Decimal(2), effective_date=REGULAR_DATE, known_at_ns=known_at_ns)
    )

    assert position.quantity == quantity_before * 2
    assert position.entry_price == entry_before / 2
    assert position.stop == (stop_before / 2).quantize(Decimal("0.01"))
    assert portfolio.total_cash == cash_before  # unchanged by a consistent conversion


def test_late_known_split_is_an_explicit_unsupported_result():
    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    coordinator = _coordinator(portfolio)
    coordinator.run_session(EARLY_CLOSE_DATE, _daily_bars(), _SCANNER_AT_0946)

    late_known_at_ns = int(to_exchange_utc(
        datetime(2024, 12, 2, 10, 0)
    ).timestamp() * 1_000_000_000)  # discovered after REGULAR_DATE's own session open
    with pytest.raises(UnsupportedCorporateActionError):
        coordinator.apply_split(
            SplitAction(
                symbol="AAA", ratio=Decimal(2), effective_date=REGULAR_DATE,
                known_at_ns=late_known_at_ns,
            )
        )


def test_fractional_split_quantity_is_an_explicit_unsupported_result():
    cash = Decimal(10000)
    portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
    coordinator = _coordinator(portfolio)
    coordinator.run_session(EARLY_CLOSE_DATE, _daily_bars(), _SCANNER_AT_0946)

    known_at_ns = int(to_exchange_utc(datetime(2024, 11, 29, 9, 0)).timestamp() * 1_000_000_000)
    # The filled position's quantity (10, capped by SizingPolicy.max_quantity) is not a
    # multiple of 3, so a 1-for-3 reverse split produces a fractional share count.
    with pytest.raises(UnsupportedCorporateActionError):
        coordinator.apply_split(
            SplitAction(
                symbol="AAA", ratio=Decimal(1) / Decimal(3), effective_date=REGULAR_DATE,
                known_at_ns=known_at_ns,
            )
        )

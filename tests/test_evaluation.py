"""Reconciled metrics and run-comparison tests (docs/M2_IMPLEMENTATION_HANDOFF.md step 9,
metrics subset)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from rsys.evaluation import (
    NOT_DEFINED_NO_LOSSES,
    UNAVAILABLE_NO_TRADES,
    ComparisonError,
    RunAssumptions,
    compare_runs,
    compute_metrics,
    reconstruct_completed_trades,
)


@dataclass
class _Fill:
    symbol: str
    status: str
    price: Decimal
    quantity: Decimal


def test_zero_trades_leaves_win_rate_and_expectancy_unavailable_not_zero():
    metrics = compute_metrics(Decimal(1000), Decimal(1000), Decimal(0), [])
    assert metrics.completed_trade_count == 0
    assert metrics.win_rate == UNAVAILABLE_NO_TRADES
    assert metrics.expectancy == UNAVAILABLE_NO_TRADES
    assert metrics.profit_factor == UNAVAILABLE_NO_TRADES
    assert metrics.net_change == Decimal(0)


def test_no_losses_gives_an_explicit_not_defined_profit_factor_not_infinity():
    fills = [
        _Fill("AAA", "FILLED", Decimal(100), Decimal(5)),
        _Fill("AAA", "EXIT", Decimal(110), Decimal(5)),
    ]
    metrics = compute_metrics(Decimal(1000), Decimal(1050), Decimal(50), fills)
    assert metrics.completed_trade_count == 1
    assert metrics.win_count == 1
    assert metrics.loss_count == 0
    assert metrics.profit_factor == NOT_DEFINED_NO_LOSSES
    assert metrics.gross_loss == Decimal(0)


def test_mixed_wins_and_losses_hand_calculated():
    fills = [
        _Fill("AAA", "FILLED", Decimal(100), Decimal(5)),
        _Fill("AAA", "EXIT", Decimal(110), Decimal(5)),   # +50
        _Fill("BBB", "FILLED", Decimal(50), Decimal(10)),
        _Fill("BBB", "EXIT", Decimal(45), Decimal(10)),   # -50
        _Fill("CCC", "FILLED", Decimal(20), Decimal(2)),
        _Fill("CCC", "EXIT", Decimal(30), Decimal(2)),    # +20
    ]
    metrics = compute_metrics(Decimal(1000), Decimal(1020), Decimal(20), fills)
    assert metrics.completed_trade_count == 3
    assert metrics.win_count == 2
    assert metrics.loss_count == 1
    assert metrics.win_rate == Decimal(2) / Decimal(3)
    assert metrics.gross_profit == Decimal(70)
    assert metrics.gross_loss == Decimal(50)
    assert metrics.profit_factor == Decimal(70) / Decimal(50)
    assert metrics.expectancy == Decimal(20) / Decimal(3)
    assert metrics.net_change == Decimal(20)


def test_unmatched_exit_without_a_recorded_entry_is_skipped_not_zero_scored():
    fills = [_Fill("AAA", "EXIT", Decimal(110), Decimal(5))]  # seeded position, no FILLED record
    trades = reconstruct_completed_trades(fills)
    assert trades == []


def _assumptions(**overrides) -> RunAssumptions:
    base = {
        "universe": ("AAA", "BBB"), "cost_profile": "fixture_v1", "risk_profile": "fixture_v1",
        "execution_mode": "bar_v1", "date_window": ("2024-11-29", "2024-11-29"),
    }
    base.update(overrides)
    return RunAssumptions(**base)


def test_identical_assumptions_compare_with_no_differences():
    assert compare_runs(_assumptions(), _assumptions()) == {}


def test_a_declared_permitted_difference_is_reported_not_rejected():
    diffs = compare_runs(_assumptions(), _assumptions(execution_mode="trade_v1"), permitted_difference="execution_mode")
    assert diffs == {"execution_mode": ("bar_v1", "trade_v1")}


def test_an_unexplained_difference_raises_instead_of_silently_comparing():
    with pytest.raises(ComparisonError):
        compare_runs(_assumptions(), _assumptions(cost_profile="other_v1"))


def test_an_unexplained_difference_alongside_a_permitted_one_still_raises():
    with pytest.raises(ComparisonError):
        compare_runs(
            _assumptions(), _assumptions(execution_mode="trade_v1", cost_profile="other_v1"),
            permitted_difference="execution_mode",
        )

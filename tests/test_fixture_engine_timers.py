"""Expiry, max-hold and cancel-and-rearm tests for `FixtureEngine` (bounded additions on
top of F01-F17; docs/M2_IMPLEMENTATION_HANDOFF.md step 3's timer requirements). These use
synthetic event sequences, not `fixtures/cases.json`, since none of F01-F17 exercise expiry
or max-hold.
"""
from __future__ import annotations

from decimal import Decimal

from rsys.fixture_engine import run_case


def test_pending_intent_expires_without_a_fill():
    case = {
        "id": "T-expiry", "initial_cash": "1000",
        "events": [
            {
                "type": "proposal", "time_local": "09:31:00", "symbol": "AAA", "quantity": "1",
                "signal_price": "100", "stop": "98", "target": "104",
                "decision_latency_ms": 0, "expiry_ms": 60000,
            },
            {"type": "bar_open", "time_local": "09:31:00", "symbol": "AAA", "open": "100"},
            {"type": "bar_open", "time_local": "09:33:00", "symbol": "AAA", "open": "101"},
        ],
    }
    # decision_latency_ms is case-level in the real schema; patch it onto the case directly.
    case["decision_latency_ms"] = 120000
    result = run_case(case)
    assert result.rejection == "EXPIRED"
    assert result.reserved_cash == Decimal(0)
    assert result.final_cash == Decimal(1000)
    assert result.positions == {}


def test_max_hold_flattens_after_scheduled_duration():
    case = {
        "id": "T-max-hold", "initial_cash": "1000",
        "events": [
            {
                "type": "proposal", "time_local": "09:31:00", "symbol": "AAA", "quantity": "5",
                "signal_price": "100", "stop": "90", "target": "110", "max_hold_ms": 60000,
            },
            {"type": "bar_open", "time_local": "09:31:00", "symbol": "AAA", "open": "100"},
            {"type": "bar_open", "time_local": "09:32:00", "symbol": "AAA", "open": "101"},
        ],
    }
    result = run_case(case)
    assert result.rejection is None
    assert result.positions == {}
    assert result.final_cash == Decimal(1000) - Decimal(500) + Decimal(505)
    assert result.exit_price == Decimal(101)


def test_rejected_proposal_frees_the_symbol_for_a_fresh_proposal():
    case = {
        "id": "T-rearm", "initial_cash": "1000",
        "events": [
            {
                "type": "proposal", "time_local": "09:31:00", "symbol": "AAA", "quantity": "5",
                "signal_price": "100", "stop": "98", "target": "104",
            },
            {"type": "bar_open", "time_local": "09:31:00", "symbol": "AAA", "open": "97"},  # ENTRY_THROUGH_STOP
            {
                "type": "proposal", "time_local": "09:32:00", "symbol": "AAA", "quantity": "5",
                "signal_price": "100", "stop": "98", "target": "104",
            },
            {"type": "bar_open", "time_local": "09:32:00", "symbol": "AAA", "open": "100"},
        ],
    }
    result = run_case(case)
    assert result.rejection is None
    assert result.positions["AAA"]["quantity"] == Decimal(5)
    assert result.entry_fill == Decimal(100)


def test_max_fills_per_symbol_day_blocks_a_second_entry():
    case = {
        "id": "T-max-fills", "initial_cash": "1000", "max_fills_per_symbol_day": 1,
        "events": [
            {
                "type": "proposal", "time_local": "09:31:00", "symbol": "AAA", "quantity": "5",
                "signal_price": "100", "stop": "98", "target": "104",
            },
            {"type": "bar_open", "time_local": "09:31:00", "symbol": "AAA", "open": "100"},
            {"type": "time_exit_request", "time_local": "09:32:00", "symbol": "AAA"},
            {"type": "bar_open", "time_local": "09:32:00", "symbol": "AAA", "open": "101"},
            {
                "type": "proposal", "time_local": "09:33:00", "symbol": "AAA", "quantity": "5",
                "signal_price": "101", "stop": "99", "target": "105",
            },
        ],
    }
    result = run_case(case)
    assert result.rejection == "MAX_FILLS_PER_DAY"
    assert result.positions == {}

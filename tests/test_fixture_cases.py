"""F01-F17 fixture-case acceptance tests (docs/M2_IMPLEMENTATION_HANDOFF.md step 4).

Runs every case in `fixtures/cases.json` through the real `FixtureEngine` (Portfolio +
sizing), not hand-copied expected values, and asserts exact Decimal equality against the
fixture's own checkpoints/final-state fields.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from rsys.fixture_engine import run_case

CASES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "cases.json"
_CASES = {c["id"]: c for c in json.loads(CASES_PATH.read_text())["cases"]}


def _dec(value) -> Decimal:
    return Decimal(str(value))


@pytest.mark.parametrize("case_id", sorted(_CASES))
def test_fixture_case(case_id: str) -> None:
    case = _CASES[case_id]
    result = run_case(case)

    if "expected" in case:
        expected = case["expected"]
        pointer = 0
        for want in expected:
            while pointer < len(result.checkpoints):
                actual = result.checkpoints[pointer]
                pointer += 1
                if (
                    actual.cash == _dec(want["cash"])
                    and actual.quantity == _dec(want["quantity"])
                    and actual.mark == _dec(want["mark"])
                    and actual.equity == _dec(want["equity"])
                    and actual.realized_pnl == _dec(want["realized_pnl"])
                ):
                    break
            else:
                pytest.fail(
                    f"{case_id}: no remaining checkpoint matches {want['checkpoint']!r} "
                    f"(seen: {[c.label for c in result.checkpoints]})"
                )

    if "rejection" in case:
        assert result.rejection == case["rejection"]
    if "first_rejection" in case:
        assert result.rejection is None  # second proposal succeeded, clearing the first rejection

    if "expected_reserved_cash" in case:
        assert result.reserved_cash == _dec(case["expected_reserved_cash"])
    if "expected_reservation" in case:
        assert result.sized_reservation == _dec(case["expected_reservation"])
    if "expected_quantity" in case:
        assert result.sized_quantity == _dec(case["expected_quantity"])
    if "expected_initial_stop_risk" in case:
        assert result.sized_initial_risk == _dec(case["expected_initial_stop_risk"])
    if "expected_final_unsettled_cash" in case:
        assert result.final_unsettled_cash == _dec(case["expected_final_unsettled_cash"])
    if "entry_fill" in case:
        assert result.entry_fill == _dec(case["entry_fill"])
    if "total_fees" in case:
        assert result.total_fees == _dec(case["total_fees"])

    if "expected_positions" in case:
        for symbol, pos in case["expected_positions"].items():
            assert result.positions[symbol]["quantity"] == _dec(pos["quantity"]), symbol
            assert result.positions[symbol]["entry"] == _dec(pos["entry"]), symbol
        assert result.final_cash == _dec(case["expected_cash"])
        assert result.final_equity == _dec(case["expected_equity"])
        assert result.acceptance_order == case["expected_order"]

    if "expected_position" in case:
        ((symbol, _pos),) = result.positions.items() if len(result.positions) == 1 else (("AAA", None),)
        want = case["expected_position"]
        got = result.positions[symbol]
        assert got["quantity"] == _dec(want["quantity"])
        assert got["entry"] == _dec(want["entry"])
        assert got["stop"] == _dec(want["stop"])
        assert got["target"] == _dec(want["target"])

    if "expected_features" in case:
        for key, value in case["expected_features"].items():
            assert result.features[key] == _dec(value), key


def test_all_seventeen_cases_present() -> None:
    assert set(_CASES) == {f"F{n:02d}" for n in range(1, 18)}

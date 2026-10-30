"""Scanner ranking tests (docs/contracts.md "Scanner baseline"; docs/M2_IMPLEMENTATION_STATUS.md
notes the real RVOL-history computation is out of scope here)."""
from __future__ import annotations

from decimal import Decimal

from rsys.scanner import (
    MISSING_GAIN,
    MISSING_RVOL,
    NOT_ELIGIBLE,
    RANKING_JSON_VERSION,
    CandidateInput,
    rank_candidates,
    ranking_digest,
    ranking_evidence,
)


def _row(symbol, rvol="2", gain="0.03", eligible=True) -> CandidateInput:
    return CandidateInput(
        symbol=symbol, rvol=Decimal(rvol) if rvol is not None else None,
        gain=Decimal(gain) if gain is not None else None, dollar_volume_eligible=eligible,
    )


def test_rank_order_descending_rvol_then_gain_then_symbol():
    rows = [_row("A", rvol="1"), _row("B", rvol="3"), _row("C", rvol="3", gain="0.05")]
    result = rank_candidates(rows, top_n=10)
    assert result.ranked == ["C", "B", "A"]


def test_tie_break_by_symbol_ascending():
    rows = [_row("ZZZ"), _row("ABC"), _row("MMM")]
    result = rank_candidates(rows, top_n=10)
    assert result.ranked == ["ABC", "MMM", "ZZZ"]


def test_rank_is_independent_of_input_order():
    forward = [_row("A", rvol="1"), _row("B", rvol="3")]
    reversed_input = list(reversed(forward))
    assert rank_candidates(forward, top_n=10).ranked == rank_candidates(reversed_input, top_n=10).ranked


def test_missing_rvol_excluded_not_defaulted_to_zero():
    rows = [_row("A", rvol=None), _row("B", rvol="1")]
    result = rank_candidates(rows, top_n=10)
    assert result.excluded["A"] == MISSING_RVOL
    assert result.ranked == ["B"]


def test_missing_gain_excluded():
    rows = [_row("A", gain=None)]
    result = rank_candidates(rows, top_n=10)
    assert result.excluded["A"] == MISSING_GAIN


def test_not_dollar_volume_eligible_excluded():
    rows = [_row("A", eligible=False)]
    result = rank_candidates(rows, top_n=10)
    assert result.excluded["A"] == NOT_ELIGIBLE


def test_top_n_limits_output_without_dropping_the_full_ranking():
    rows = [_row("A", rvol="3"), _row("B", rvol="2"), _row("C", rvol="1")]
    result = rank_candidates(rows, top_n=2)
    assert result.top == ["A", "B"]
    assert result.ranked == ["A", "B", "C"]  # full ranking retained even when top is capped


def test_ranking_evidence_pins_rank_symbol_rvol_and_gain():
    result = rank_candidates([_row("A", rvol="3"), _row("B", rvol="1")], top_n=10)
    evidence = ranking_evidence(result.ranked_candidates, feature_registry_version="v1")
    assert evidence["encoding"] == RANKING_JSON_VERSION
    assert evidence["feature_registry_version"] == "v1"
    assert evidence["ranking"] == [
        {"rank": 1, "symbol": "A", "rvol": "3", "gain": "0.03"},
        {"rank": 2, "symbol": "B", "rvol": "1", "gain": "0.03"},
    ]


def test_ranking_digest_is_reconstructible_from_pinned_inputs():
    result = rank_candidates([_row("A", rvol="3"), _row("B", rvol="1")], top_n=10)
    digest = ranking_digest(result.ranked_candidates, feature_registry_version="v1")
    reconstructed = rank_candidates(
        [_row("B", rvol="1"), _row("A", rvol="3")], top_n=10  # reversed input order
    )
    assert ranking_digest(reconstructed.ranked_candidates, feature_registry_version="v1") == digest


def test_ranking_digest_changes_when_a_ranked_value_changes():
    original = rank_candidates([_row("A", rvol="3"), _row("B", rvol="1")], top_n=10)
    changed = rank_candidates([_row("A", rvol="4"), _row("B", rvol="1")], top_n=10)
    original_digest = ranking_digest(original.ranked_candidates, feature_registry_version="v1")
    changed_digest = ranking_digest(changed.ranked_candidates, feature_registry_version="v1")
    assert original_digest != changed_digest

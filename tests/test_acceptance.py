"""Acceptance ordering and reservation bookkeeping tests (docs/contracts.md "Acceptance and
pending-order order"; docs/M2_IMPLEMENTATION_HANDOFF.md step 2)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from rsys.acceptance import AcceptanceBook, sort_proposals
from rsys.orders import NO_CURRENT_RANK, Proposal
from rsys.portfolio import AcceptanceError, Portfolio


def _proposal(symbol, rank=None, time_ms=0, strategy_id="fixture", proposal_id=None) -> Proposal:
    return Proposal(
        symbol=symbol, quantity=Decimal(1), signal_price=Decimal(100), stop=Decimal(98), target=None,
        decision_time_ms=time_ms, scanner_rank=rank, strategy_id=strategy_id, proposal_id=proposal_id,
    )


def _portfolio(cash="1000") -> Portfolio:
    total = Decimal(cash)
    return Portfolio(total_cash=total, spendable_cash=total, unsettled_cash=Decimal(0))


def test_sort_order_by_decision_time_then_rank_then_symbol():
    proposals = [
        _proposal("XYZ", rank=2, time_ms=1000),
        _proposal("ABC", rank=1, time_ms=1000),
        _proposal("EARLY", rank=5, time_ms=500),
    ]
    ordered = sort_proposals(proposals)
    assert [p.symbol for p in ordered] == ["EARLY", "ABC", "XYZ"]


def test_sort_is_independent_of_input_order():
    forward = [_proposal("ABC", rank=1, time_ms=1000), _proposal("XYZ", rank=2, time_ms=1000)]
    reversed_input = list(reversed(forward))
    assert [p.symbol for p in sort_proposals(forward)] == [p.symbol for p in sort_proposals(reversed_input)]


def test_missing_rank_rejected_with_no_current_rank():
    book = AcceptanceBook()
    proposal = _proposal("ABC", rank=None)
    with pytest.raises(AcceptanceError) as exc:
        book.accept(_portfolio(), proposal, Decimal(100))
    assert exc.value.reason == NO_CURRENT_RANK


def test_missing_rank_allowed_when_not_required():
    book = AcceptanceBook()
    portfolio = _portfolio()
    proposal = _proposal("ABC", rank=None)
    intent = book.accept(portfolio, proposal, Decimal(100), require_rank=False)
    assert intent.reserved_cash == Decimal(100)
    assert portfolio.reserved_cash == Decimal(100)


def test_duplicate_proposal_id_rejected_without_double_reserving():
    book = AcceptanceBook()
    portfolio = _portfolio()
    proposal = _proposal("ABC", rank=1, proposal_id="p-1")
    book.accept(portfolio, proposal, Decimal(100))
    with pytest.raises(AcceptanceError) as exc:
        book.accept(portfolio, proposal, Decimal(100))
    assert exc.value.reason == "DUPLICATE_PROPOSAL"
    assert portfolio.reserved_cash == Decimal(100)  # not charged/reserved twice


def test_rejected_acceptance_does_not_mark_id_as_seen():
    """An id that failed to reserve (e.g. insufficient cash) was never actually accepted,
    so a later legitimate retry with the same id must not be treated as a duplicate."""
    book = AcceptanceBook()
    poor_portfolio = _portfolio(cash="50")
    proposal = _proposal("ABC", rank=1, proposal_id="p-1")
    with pytest.raises(AcceptanceError):
        book.accept(poor_portfolio, proposal, Decimal(100))

    funded_portfolio = _portfolio(cash="1000")
    intent = book.accept(funded_portfolio, proposal, Decimal(100))
    assert intent.reserved_cash == Decimal(100)


def test_cancel_releases_exact_reservation_and_repeat_release_is_harmless():
    portfolio = _portfolio()
    book = AcceptanceBook()
    intent = book.accept(portfolio, _proposal("ABC", rank=1), Decimal(300))
    assert portfolio.available_cash == Decimal(700)

    portfolio.release_reservation(intent.reserved_cash)  # cancel
    assert portfolio.reserved_cash == Decimal(0)
    assert portfolio.available_cash == Decimal(1000)

    portfolio.release_reservation(intent.reserved_cash)  # repeated release is a harmless no-op
    assert portfolio.reserved_cash == Decimal(0)


def test_expired_reservation_frees_cash_for_a_later_proposal():
    portfolio = _portfolio()
    book = AcceptanceBook()
    expiring = book.accept(portfolio, _proposal("ABC", rank=1), Decimal(1000))
    assert portfolio.available_cash == Decimal(0)

    portfolio.release_reservation(expiring.reserved_cash)  # expiry timer fires

    later = book.accept(portfolio, _proposal("XYZ", rank=1), Decimal(1000))
    assert later.reserved_cash == Decimal(1000)
    assert portfolio.reserved_cash == Decimal(1000)

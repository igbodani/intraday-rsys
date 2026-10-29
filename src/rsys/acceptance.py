"""Deterministic acceptance ordering and reservation bookkeeping (docs/contracts.md
"Acceptance and pending-order order"; docs/M2_IMPLEMENTATION_HANDOFF.md step 2).

Sorts proposals by `(decision_available_at, scanner_rank, instrument_id, strategy_id,
proposal_id)`, rejects a missing rank with `NO_CURRENT_RANK` instead of inventing rank
zero, and deduplicates a reused `proposal_id` instead of charging/reserving twice.
"""
from __future__ import annotations

from decimal import Decimal

from rsys.orders import NO_CURRENT_RANK, AcceptedIntent, Proposal
from rsys.portfolio import AcceptanceError, Portfolio

DUPLICATE_PROPOSAL = "DUPLICATE_PROPOSAL"


def sort_proposals(proposals: list[Proposal]) -> list[Proposal]:
    """Stable ascending sort; a missing rank is not resolved here -- `AcceptanceBook.accept`
    rejects it explicitly so callers cannot silently treat it as rank zero."""
    return sorted(
        proposals,
        key=lambda p: (
            p.decision_time_ms, p.scanner_rank if p.scanner_rank is not None else -1,
            p.symbol, p.strategy_id, p.proposal_id or "",
        ),
    )


class AcceptanceBook:
    """Tracks which `proposal_id`s have already been accepted or rejected in this run."""

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()

    def accept(
        self, portfolio: Portfolio, proposal: Proposal, needed_cash: Decimal, *, require_rank: bool = True,
    ) -> AcceptedIntent:
        """Raises AcceptanceError with the declared reason; portfolio state is unchanged on
        any rejection path (rank, duplicate id or insufficient cash)."""
        if require_rank and proposal.scanner_rank is None:
            raise AcceptanceError(NO_CURRENT_RANK)
        if proposal.proposal_id is not None and proposal.proposal_id in self._seen_ids:
            raise AcceptanceError(DUPLICATE_PROPOSAL)
        portfolio.reserve(needed_cash)
        if proposal.proposal_id is not None:
            self._seen_ids.add(proposal.proposal_id)
        return AcceptedIntent(proposal=proposal, reserved_cash=needed_cash)

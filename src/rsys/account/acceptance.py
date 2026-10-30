"""Deterministic acceptance ordering and reservation bookkeeping (docs/contracts.md
"Acceptance and pending-order order"; docs/M2_IMPLEMENTATION_HANDOFF.md step 2).

Sorts proposals by `(decision_available_at, scanner_rank, instrument_id, strategy_id,
proposal_id)`, rejects a missing rank with `NO_CURRENT_RANK` instead of inventing rank
zero, and deduplicates a reused `proposal_id` instead of charging/reserving twice.
"""

from __future__ import annotations

from decimal import Decimal

from rsys.account.errors import AcceptanceError
from rsys.account.models import NO_CURRENT_RANK, AcceptedIntent, Proposal
from rsys.account.portfolio import Portfolio

DUPLICATE_PROPOSAL = "DUPLICATE_PROPOSAL"


def sort_proposals(proposals: list[Proposal]) -> list[Proposal]:
    """Stable ascending sort; a missing rank is not resolved here -- `AcceptanceBook.accept`
    rejects it explicitly so callers cannot silently treat it as rank zero."""
    return sorted(
        proposals,
        key=lambda p: (
            p.decision_time_ms,
            p.scanner_rank if p.scanner_rank is not None else -1,
            p.symbol,
            p.strategy_id,
            p.proposal_id or "",
        ),
    )


class AcceptanceBook:
    """Tracks which `proposal_id`s have already been accepted or rejected in this run.

    `reservation_namespace` (typically a session's dated origin) prefixes the id actually
    reserved on the (possibly cross-session) `Portfolio`, so an across-session-identical
    `proposal_id` -- a strategy's own ids are time-of-day-only, not dated -- does not collide
    with a retired reservation from a prior session. Duplicate-proposal detection within this
    run still keys on the strategy's raw, unqualified `proposal_id`."""

    def __init__(self, reservation_namespace: str | None = None) -> None:
        self._seen_ids: set[str] = set()
        self._sequence = 0
        self._accepted = {}
        self._pending_symbols = {}
        self._reservation_namespace = reservation_namespace

    def _qualify(self, proposal_id: str | None) -> str | None:
        if proposal_id is None or self._reservation_namespace is None:
            return proposal_id
        return f"{self._reservation_namespace}:{proposal_id}"

    def accept(
        self,
        portfolio: Portfolio,
        proposal: Proposal,
        needed_cash: Decimal,
        *,
        require_rank: bool = True,
    ) -> AcceptedIntent:
        """Raises AcceptanceError with the declared reason; portfolio state is unchanged on
        any rejection path (rank, duplicate id or insufficient cash)."""
        if require_rank and proposal.scanner_rank is None:
            raise AcceptanceError(NO_CURRENT_RANK)
        if proposal.proposal_id is not None and proposal.proposal_id in self._seen_ids:
            if self._accepted[proposal.proposal_id] != (proposal, needed_cash):
                raise ValueError("conflicting proposal identity")
            raise AcceptanceError(DUPLICATE_PROPOSAL)
        if proposal.symbol in portfolio.positions or portfolio.has_reservation(
            self._pending_symbols.get(proposal.symbol)
        ):
            raise AcceptanceError("DUPLICATE_PENDING_OR_HELD")
        reservation_id = portfolio.reserve(
            needed_cash, self._qualify(proposal.proposal_id)
        )
        self._pending_symbols[proposal.symbol] = reservation_id
        self._sequence += 1
        if proposal.proposal_id is not None:
            self._seen_ids.add(proposal.proposal_id)
            self._accepted[proposal.proposal_id] = (proposal, needed_cash)
        return AcceptedIntent(proposal, needed_cash, reservation_id, self._sequence)

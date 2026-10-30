"""Optional quote state (R3-B / docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md 7.4).

Quotes update quote state only -- they never contribute to a trade bar's OHLC,
volume or VWAP (that remains exclusively `TradeBarBuilder`'s concern). Each
accepted `QuoteEvent` produces a new, immutable `QuoteSnapshot` capturing
bid/ask prices and sizes, source identity, event/availability time and an
explicit condition classification (locked/crossed/missing-side/normal). A
displayed quote size is not a guaranteed fill; nothing here claims otherwise.

Freshness is computed against the *decision clock* (the cutoff a strategy
context is frozen at), never against wall-clock "now" -- so freshness
evaluation itself stays replay-deterministic.
"""

from __future__ import annotations

from rsys.data.errors import MismatchedQuoteSeriesError
from rsys.data.models import (
    FreshnessState,
    QuoteEvent,
    QuoteFreshnessPolicy,
    QuoteSnapshot,
)


class QuoteState:
    """Latest quote for an instrument/feed, in nondecreasing availability order.

    Equal-time events follow the runner's source sequence and stable tie break.
    """

    def __init__(
        self, instrument_id: str, feed: str, freshness_policy: QuoteFreshnessPolicy
    ):
        self.instrument_id = instrument_id
        self.feed = feed
        self._freshness_policy = freshness_policy
        self._latest: QuoteSnapshot | None = None

    def apply(self, event: QuoteEvent) -> QuoteSnapshot:
        if event.instrument_id != self.instrument_id or event.feed != self.feed:
            raise MismatchedQuoteSeriesError(
                f"quote for ({event.instrument_id!r}, {event.feed!r}) cannot update quote state "
                f"for ({self.instrument_id!r}, {self.feed!r})"
            )
        if (
            self._latest is not None
            and event.available_at_ns < self._latest.available_at_ns
        ):
            raise ValueError("quote availability cannot move backward")
        snapshot = QuoteSnapshot.from_event(event)
        self._latest = snapshot
        return snapshot

    def latest(self) -> QuoteSnapshot | None:
        return self._latest

    def latest_with_freshness(
        self, decision_clock_ns: int
    ) -> tuple[QuoteSnapshot, FreshnessState] | None:
        """The current quote paired with its freshness as of `decision_clock_ns`, or `None` if
        no quote has ever been applied -- never a fabricated default quote."""
        if self._latest is None:
            return None
        return self._latest, self._freshness_policy.classify(
            self._latest, decision_clock_ns
        )

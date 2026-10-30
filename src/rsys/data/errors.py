"""Data shared definitions."""

from __future__ import annotations


class CalendarCoverageError(Exception):
    """Raised for a date outside this declared calendar's covered range."""


class UnresolvedCorrectionError(Exception):
    """A correction/cancel references a trade_id this builder never accepted (or has evicted)."""


class ConflictingTradeError(Exception):
    """A second delivery under the same trade_id with a different payload, with no correction."""


class MismatchedSeriesError(Exception):
    """An event for a different instrument/feed was routed at this builder (C05): the builder
    independently rejects it before any mutation, regardless of what the caller believes."""


class RejectedQualityError(Exception):
    """An event whose quality_state is not 'ok' cannot mutate builder state (C05); suspect/
    invalid/unavailable observations are quarantined, never silently treated as valid."""


class EvictedBucketError(Exception):
    """The bucket's contributor detail was evicted under the retention policy; its effect on a
    late correction is explicitly unresolved, never silently applied or silently ignored."""


class UnsupportedSplitOverlapError(Exception):
    """A bucket interval straddles a split's dispatch moment, mixing old/new price units."""


class UnknownConditionError(Exception):
    """A trade condition code outside this rule set's declared table."""


class MismatchedQuoteSeriesError(Exception):
    """A quote for a different instrument/feed was routed at this series' quote state."""


class ReplayInputError(Exception):
    """Input cannot satisfy the declared offline replay contract."""

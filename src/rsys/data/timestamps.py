"""Nanosecond-precise UTC timestamp parsing (docs/data_rules.md "Quality and time").

Python's `datetime` truncates to microseconds. Provider timestamps may carry
finer precision; store the canonical value as integer nanoseconds since the
UTC epoch so no precision is silently dropped, and so two equivalent string
representations (`...Z` vs `...+00:00`) always compare identical.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

_TIMESTAMP_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})(\.(?P<frac>\d{1,9}))?(?P<offset>Z|\+00:00)$"
)


def parse_utc_timestamp_ns(raw: str) -> int:
    """Require an explicit, already-UTC ISO-8601 timestamp (`Z` or `+00:00`); reject anything
    ambiguous or offset-bearing in another zone -- exchange-local conversion belongs to
    `rsys.calendar.to_exchange_utc`, which already handles DST folds/gaps explicitly.
    """
    match = _TIMESTAMP_RE.fullmatch(raw)
    if match is None:
        raise ValueError(f"malformed or non-UTC timestamp: {raw!r}")
    frac = (match.group("frac") or "").ljust(9, "0")
    dt = datetime.fromisoformat(f"{match.group('date')}T{match.group('time')}+00:00")
    return int(dt.timestamp()) * 1_000_000_000 + int(frac)


def ns_to_datetime(ns: int) -> datetime:
    """Lossy display/interop conversion (microsecond precision); `ns` remains the source of truth."""
    seconds, remainder_ns = divmod(ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(
        microsecond=remainder_ns // 1_000
    )


def ns_to_canonical_iso(ns: int) -> str:
    """Canonical string form (always `+00:00`) so equivalent inputs compare/identity-match identically."""
    seconds, remainder_ns = divmod(ns, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{remainder_ns:09d}+00:00"


# Declared bucket durations for interval-alignment validation; extending beyond these
# requires its own versioned entry, not a silent assumption (D05: 1-minute baseline in v1).
# Lives here (not in bars.py) so both bars.py and events.py can import it without a cycle.
INTERVAL_DURATIONS_NS: dict[str, int] = {"1Min": 60_000_000_000}

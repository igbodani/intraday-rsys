"""Timestamp convenience adapter. Market-event records live in data.models."""

from __future__ import annotations

from rsys.data.timestamps import parse_utc_timestamp_ns


def event_timestamp_from_iso(raw: str) -> int:
    """Convenience wrapper so callers building fixtures don't import timestamps.py separately."""
    return parse_utc_timestamp_ns(raw)

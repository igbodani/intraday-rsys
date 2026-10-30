"""Immutable availability/phase/sequence cutoffs shared by publications and views."""

from rsys.data.models import ReplayKey


def cutoff_key(value: int | ReplayKey) -> ReplayKey:
    return ReplayKey.end_of_time(value) if isinstance(value, int) else value

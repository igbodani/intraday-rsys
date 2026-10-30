"""Immutable availability/phase/sequence cutoffs shared by publications and views."""

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class ReplayKey:
    available_at_ns: int
    phase: int
    sequence: int = -1
    tie: tuple[str, ...] = ()

    @classmethod
    def end_of_time(cls, ns: int):
        return cls(ns, 1000, 2**63 - 1, ("\uffff",))


def cutoff_key(value: int | ReplayKey) -> ReplayKey:
    return ReplayKey.end_of_time(value) if isinstance(value, int) else value

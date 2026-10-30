"""Replay shared definitions."""

from __future__ import annotations


class PhaseOrderError(ValueError):
    pass


class UnsupportedPhaseError(Exception):
    """An event classified into a phase its replay profile does not declare."""


class IncompleteReplayError(Exception):
    """The stream ended before a required closure/coverage boundary; never silently assumed closed."""


class LateInsertionError(Exception):
    """A derived event was inserted into an `(available_at_ns, phase)` bucket already dispatched."""


class NoAuthoritativeBuilderError(Exception):
    """A trade/correction arrived for a series this router was not configured to build
    (bars.authority must name exactly one source per series; this router only owns builders
    for feeds/instruments it was told to build)."""

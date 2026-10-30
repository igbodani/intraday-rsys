"""Research shared definitions."""

from __future__ import annotations


class CompareCliError(Exception):
    """Missing, incomplete, legacy or corrupted run evidence."""


class ComparisonError(Exception):
    """Raised when two runs differ in an assumption other than the one being deliberately
    isolated; a comparison must never silently attribute P&L change to the wrong cause."""


class FinalizationError(Exception):
    """Finalizing a run failed for a reason other than a legitimate concurrent terminal
    outcome -- e.g. immutable-field corruption, a conflicting duplicate terminal write, or
    an unexpected persistence failure. Never silently swallowed; the application exception
    being finalized (if any) is chained via `__cause__` rather than lost."""


class HoldoutAccessDenied(Exception):
    """Raised before any read is attempted against a forbidden or undeclared path."""


class RunAlreadyExistsError(Exception):
    """A run_id already exists; it is never overwritten."""


class RunNotFoundError(Exception):
    pass


class InvalidRunIdError(ValueError):
    """run_id is not a single safe path component (no separators, no traversal)."""


class IllegalTransitionError(Exception):
    """A status change outside the declared lifecycle (e.g. resurrecting a finalized attempt)."""


class FrozenFieldError(Exception):
    """A write attempted to change a field that is frozen once a run is created."""


class RecoveryInProgressError(Exception):
    """Another recovery sweep already holds the exclusive recovery lock."""


class TerminalOutcomeConflictError(IllegalTransitionError):
    """A second finalizer tried to record a different terminal outcome for an already-sealed run.
    Distinct from a generic IllegalTransitionError so callers can tell "someone else finished
    this run first" apart from an actually-illegal lifecycle request."""

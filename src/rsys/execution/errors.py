"""Execution shared definitions."""

from __future__ import annotations


class IncompleteSessionError(ValueError):
    """Required execution data is absent or ambiguous; never a successful run."""

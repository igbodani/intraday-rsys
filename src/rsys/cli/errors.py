"""Cli shared definitions."""

from __future__ import annotations


class FixtureCliError(Exception):
    """Invalid `--case` selector or unreadable fixture file."""


class _FixtureMismatch(Exception):
    """Internal-only: makes `run_context` finalize this attempt as `failed` with a truthful,
    retained reason when one or more fixture checkpoints did not match."""

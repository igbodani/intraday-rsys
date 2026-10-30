"""Reporting shared definitions."""

from __future__ import annotations


class ReportError(Exception):
    """Missing or malformed manifest/trace for the requested attempt."""

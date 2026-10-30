"""Account shared definitions."""

from __future__ import annotations


class AcceptanceError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

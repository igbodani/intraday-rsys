"""Cli data models and their intrinsic validation."""

from __future__ import annotations


class FixtureCaseOutcome:
    def __init__(self, case_id: str, problems: list[str]):
        self.case_id = case_id
        self.problems = problems

    @property
    def ok(self) -> bool:
        return not self.problems

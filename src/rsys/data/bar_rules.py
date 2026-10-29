"""Provider/feed-specific bar-aggregation rules (R2, docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md section 7-8).

A rule set tells the bar builder which fields a trade's condition codes let it
contribute to: `price` (OHLC), `volume` (volume/count), and `vwap` (the VWAP
numerator/denominator, tracked separately from OHLC so a volume-only print can
never contaminate either). An unrecognized condition is a named, explicit
error -- never silently treated as fully eligible or fully excluded.

No rule set here asserts verified real-provider semantics. The exact
Alpaca condition-to-field mapping is unresolved (S1 vs. S5 in the replay
plan's sources disagree on timestamp semantics, and neither source gives an
authoritative field-eligibility table); a `providers/alpaca_rules.py`
implementing *that* is blocked on D12's provider audit, not fabricated here.
`FixtureBarRuleSet` is the synthetic, declared rule table used for tests.
"""
from __future__ import annotations

from typing import ClassVar, NamedTuple, Protocol


class FieldEligibility(NamedTuple):
    price: bool  # contributes to open/high/low/close
    volume: bool  # contributes to volume/trade_count
    vwap: bool  # contributes to the VWAP numerator/denominator


class UnknownConditionError(Exception):
    """A trade condition code outside this rule set's declared table."""


class BarRuleSet(Protocol):
    rule_set_id: str

    def classify(self, conditions: tuple[str, ...]) -> FieldEligibility: ...


class FixtureBarRuleSet:
    """The synthetic rule table from the replay plan's worked example: `ALL` contributes
    everywhere; `VOLUME_ONLY` contributes to volume/count only. No conditions means `ALL`.
    """

    rule_set_id = "fixture_rules_v1"

    _TABLE: ClassVar[dict[str, FieldEligibility]] = {
        "ALL": FieldEligibility(price=True, volume=True, vwap=True),
        "VOLUME_ONLY": FieldEligibility(price=False, volume=True, vwap=False),
    }

    def classify(self, conditions: tuple[str, ...]) -> FieldEligibility:
        if not conditions:
            return self._TABLE["ALL"]
        unknown = [c for c in conditions if c not in self._TABLE]
        if unknown:
            raise UnknownConditionError(f"{self.rule_set_id}: unrecognized condition code(s) {unknown}")
        # Combined-condition precedence: the most restrictive declared condition governs.
        eligibilities = [self._TABLE[c] for c in conditions]
        return FieldEligibility(
            price=all(e.price for e in eligibilities),
            volume=all(e.volume for e in eligibilities),
            vwap=all(e.vwap for e in eligibilities),
        )

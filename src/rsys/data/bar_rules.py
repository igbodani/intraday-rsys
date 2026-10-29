"""Provider/feed-specific bar-aggregation rules (R2/C10, docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md
section 7-8 and docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md section 5).

A rule set tells the bar builder which fields a trade's tape/conditions let it
contribute to: open, high, low and close separately (a single combined
`price` flag cannot express every provider rule -- e.g. a late-reported print
may be eligible for close but not for the already-set high/low extremes),
`volume` and `count` separately (a print can be eligible for volume without
being eligible for trade count, or vice versa), and `vwap` (tracked
separately again, so a volume-only print can never contaminate either).

Classification is driven by a `TradeClassificationContext`, not a bare
conditions tuple: real provider rules depend on tape and interval/rule
context as well as conditions (and, per Alpaca's own FAQ, on conditional
combination behavior), so an adapter needs more than the raw condition list
to classify a print correctly. An unrecognized condition is a named,
explicit error -- never silently treated as fully eligible or fully excluded.

This module does not assert verified real-provider semantics. Official
documentation (checked 2026-10-29) DOES distinguish open/close, high/low and
volume eligibility by tape/condition/bar-type [S1] -- there is an
authoritative eligibility table to map, it has simply not yet been verified
and encoded for this feed; `providers/alpaca_rules.py` implementing that
verified mapping is blocked on D12's provider audit, not fabricated here.
`FixtureBarRuleSet` is the synthetic, declared rule table used for tests --
named as a fixture, never as verified Alpaca behavior.
"""
from __future__ import annotations

from typing import ClassVar, NamedTuple, Protocol


class FieldEligibility(NamedTuple):
    open: bool  # contributes to the bucket's open price
    high: bool  # contributes to the bucket's high extreme
    low: bool  # contributes to the bucket's low extreme
    close: bool  # contributes to the bucket's close price
    volume: bool  # contributes to volume
    count: bool  # contributes to trade_count (not inferred from volume without a declared rule)
    vwap: bool  # contributes to the VWAP numerator/denominator


class TradeClassificationContext(NamedTuple):
    """Everything a real classification rule may need, not just the bare conditions tuple.

    `conditions` and `tape` preserve the provider's raw codes verbatim -- including a code that
    is itself a meaningful space -- never stripped or treated as a blank identifier.
    """

    conditions: tuple[str, ...] = ()
    tape: str | None = None
    interval: str | None = None


class UnknownConditionError(Exception):
    """A trade condition code outside this rule set's declared table."""


class BarRuleSet(Protocol):
    rule_set_id: str

    def classify(self, context: TradeClassificationContext) -> FieldEligibility: ...


_FULL_ELIGIBILITY = FieldEligibility(
    open=True, high=True, low=True, close=True, volume=True, count=True, vwap=True,
)
_VOLUME_ONLY_ELIGIBILITY = FieldEligibility(
    open=False, high=False, low=False, close=False, volume=True, count=True, vwap=False,
)
# A late-reported/out-of-sequence print: still contributes to close (the provider's declared
# "last known" price) and volume/count, but must never retroactively move an already-set
# high/low extreme or distort VWAP with an uncertain fill price.
_LATE_REPORT_ELIGIBILITY = FieldEligibility(
    open=False, high=False, low=False, close=True, volume=True, count=True, vwap=False,
)


class FixtureBarRuleSet:
    """The synthetic rule table from the replay plan's worked example, extended for C10:
    `ALL` contributes everywhere; `VOLUME_ONLY` contributes to volume/count only;
    `LATE_REPORT` contributes to close/volume/count but never to high/low/vwap. No
    conditions means `ALL`. Declared fixture-only; never verified Alpaca behavior.
    """

    rule_set_id = "fixture_rules_v1"

    _TABLE: ClassVar[dict[str, FieldEligibility]] = {
        "ALL": _FULL_ELIGIBILITY,
        "VOLUME_ONLY": _VOLUME_ONLY_ELIGIBILITY,
        "LATE_REPORT": _LATE_REPORT_ELIGIBILITY,
    }

    def classify(self, context: TradeClassificationContext) -> FieldEligibility:
        conditions = context.conditions
        if not conditions:
            return self._TABLE["ALL"]
        unknown = [c for c in conditions if c not in self._TABLE]
        if unknown:
            raise UnknownConditionError(f"{self.rule_set_id}: unrecognized condition code(s) {unknown}")
        # Combined-condition precedence: the most restrictive declared condition governs,
        # field by field (an unconditional Boolean alone cannot describe every combination).
        eligibilities = [self._TABLE[c] for c in conditions]
        return FieldEligibility(
            open=all(e.open for e in eligibilities),
            high=all(e.high for e in eligibilities),
            low=all(e.low for e in eligibilities),
            close=all(e.close for e in eligibilities),
            volume=all(e.volume for e in eligibilities),
            count=all(e.count for e in eligibilities),
            vwap=all(e.vwap for e in eligibilities),
        )

"""Deterministic scanner ranking (docs/contracts.md "Scanner baseline", D14-proposed).

Honesty note: only the ranking/exclusion/tie-break layer is implemented here. Real
prior-session RVOL history selection (H01-H03, DQ08) and a verified dollar-volume
eligibility audit require calendar-aware historical data loading that remains out of scope
for this pass -- see docs/M2_IMPLEMENTATION_STATUS.md. Callers supply already-computed
`rvol`/`gain` per symbol for one decision snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

MISSING_RVOL = "MISSING_RVOL"
MISSING_GAIN = "MISSING_GAIN"
NOT_ELIGIBLE = "NOT_DOLLAR_VOLUME_ELIGIBLE"


@dataclass(frozen=True)
class CandidateInput:
    symbol: str
    rvol: Decimal | None
    gain: Decimal | None
    dollar_volume_eligible: bool = True


@dataclass(frozen=True)
class ScanResult:
    top: list[str]
    excluded: dict[str, str]
    ranked: list[str]  # the full eligible ranking, independent of top_n


def rank_candidates(inputs: list[CandidateInput], *, top_n: int) -> ScanResult:
    """Rank descending by RVOL, then descending by gain, then ascending symbol; take
    `top_n`. Missing required inputs are excluded with a reason, never defaulted to zero."""
    if type(top_n) is not int or top_n < 1:
        raise ValueError("top_n must be a positive integer")
    seen = set()
    for row in inputs:
        if not row.symbol or row.symbol in seen:
            raise ValueError("scanner symbols must be unique and nonblank")
        seen.add(row.symbol)
        for name in ("rvol", "gain"):
            value = getattr(row, name)
            if value is not None and (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or (name == "rvol" and value < 0)
            ):
                raise ValueError("invalid scanner score")
    excluded: dict[str, str] = {}
    eligible: list[CandidateInput] = []
    for row in inputs:
        if not row.dollar_volume_eligible:
            excluded[row.symbol] = NOT_ELIGIBLE
        elif row.rvol is None:
            excluded[row.symbol] = MISSING_RVOL
        elif row.gain is None:
            excluded[row.symbol] = MISSING_GAIN
        else:
            eligible.append(row)

    ranked_rows = sorted(eligible, key=lambda r: (-r.rvol, -r.gain, r.symbol))
    ranked = [row.symbol for row in ranked_rows]
    return ScanResult(top=ranked[:top_n], excluded=excluded, ranked=ranked)

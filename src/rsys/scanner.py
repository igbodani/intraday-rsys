"""Deterministic scanner ranking (docs/contracts.md "Scanner baseline", D14-proposed).

`rank_candidates` is the ranking/exclusion/tie-break layer; callers supply already-computed
`rvol`/`gain` per symbol for one decision snapshot, either directly (isolated fixture tests)
or via `rsys.features.compute_cumulative_rvol`/`compute_session_gain` (Step 3, M2-02a) feeding
a calendar-aware historical scanner mode. This module stays agnostic to which path produced
the scores -- it never computes RVOL/gain itself.

`ranking_digest`/`ranking_evidence` implement the `ranking-json-v1` canonical encoding from
docs/contracts.md "Ranking evidence retention": hashing the full ordered ranking so a decision
can later be reconstructed from pinned inputs and checked against its digest. Persisting that
evidence to storage is a separate concern (`storage.py`/`run_cli.py`), out of scope here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

MISSING_RVOL = "MISSING_RVOL"
MISSING_GAIN = "MISSING_GAIN"
NOT_ELIGIBLE = "NOT_DOLLAR_VOLUME_ELIGIBLE"
RANKING_JSON_VERSION = "ranking-json-v1"


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
    ranked_candidates: list[CandidateInput]  # same order as `ranked`, full rows for evidence


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
    return ScanResult(
        top=ranked[:top_n], excluded=excluded, ranked=ranked, ranked_candidates=ranked_rows
    )


def _canonical_decimal(value: Decimal) -> str:
    """Fixed-point string, trailing fractional zeros removed, zero normalized to "0"."""
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def ranking_evidence(
    ranked_candidates: list[CandidateInput], *, feature_registry_version: str
) -> dict:
    """The canonical `ranking-json-v1` payload for a full ordered ranking (already excludes
    ineligible/missing rows): pins exact rank, symbol, RVOL and gain so the ranking can be
    reconstructed and its digest independently checked."""
    return {
        "encoding": RANKING_JSON_VERSION,
        "feature_registry_version": feature_registry_version,
        "ranking": [
            {
                "rank": i,
                "symbol": row.symbol,
                "rvol": _canonical_decimal(row.rvol),
                "gain": _canonical_decimal(row.gain),
            }
            for i, row in enumerate(ranked_candidates, 1)
        ],
    }


def ranking_digest(
    ranked_candidates: list[CandidateInput], *, feature_registry_version: str
) -> str:
    """SHA-256 of the canonical UTF-8 JSON (sorted keys, no insignificant whitespace)."""
    payload = ranking_evidence(
        ranked_candidates, feature_registry_version=feature_registry_version
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

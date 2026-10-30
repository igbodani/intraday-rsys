"""Application shared definitions."""

from __future__ import annotations

import math
from typing import Any


def _positive_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _nonnegative(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    try:
        return math.isfinite(float(v)) and float(v) >= 0
    except (TypeError, ValueError):
        return False


def _positive_number(v: Any) -> bool:
    """Shared with config.py: finite, strictly positive Decimal/int/float, excluding bool."""
    if isinstance(v, bool):
        return False
    try:
        return math.isfinite(float(v)) and float(v) > 0
    except (TypeError, ValueError):
        return False


def _fraction_0_1(v: Any) -> bool:
    """Shared with config.py: finite value in (0, 1], e.g. a fraction of equity."""
    if isinstance(v, bool):
        return False
    try:
        f = float(v)
        return math.isfinite(f) and 0 < f <= 1
    except (TypeError, ValueError):
        return False


def _non_empty_str(v: Any) -> bool:
    """Shared with config.py: a required identifier is present and not blank."""
    return isinstance(v, str) and v.strip() != ""


def _non_empty_str_list(v: Any) -> bool:
    """A nonempty list whose elements are themselves nonblank strings (no blank list elements)."""
    return (
        isinstance(v, list) and len(v) > 0 and all(_non_empty_str(item) for item in v)
    )

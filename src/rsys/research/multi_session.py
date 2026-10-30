"""Multi-session diagnostic statistics (Step 5 of docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md).

Exposure and drawdown are computed independently from each session's own `SessionResult`
(fills and the declared calendar's scheduled session length) -- never from an invented
intrabar timestamp or a borrowed single-session identity. These are descriptive
diagnostics only: D10's recorded risk policy is not enforced here and D17's promotion
gates remain Proposed/incomplete. Nothing in
this module authorizes a trading decision, a promotion claim or a risk limit.
"""

from __future__ import annotations

from decimal import Decimal

from rsys.application.models import SessionResult
from rsys.data.calendar import session_duration_minutes, session_open_local
from rsys.research.evaluation import compute_metrics
from rsys.research.models import (
    DrawdownResult,
    ExposureResult,
    MultiSessionMetrics,
    SessionEquityPoint,
)

UNAVAILABLE_NO_SESSIONS = "UNAVAILABLE_NO_SESSIONS"
UNAVAILABLE_NONPOSITIVE_PEAK = "UNAVAILABLE_NONPOSITIVE_PEAK"


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union-merges closed-open `[start, end)` intervals so overlapping or touching
    positions from different symbols count their covered time once, not once per symbol."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def session_open_intervals(result: SessionResult) -> list[tuple[int, int]]:
    """Pairs each `FILLED` fill with its own symbol's next `EXIT` fill. Fill timestamps
    are milliseconds since exchange-local midnight, matching both runners. Return elapsed
    intervals clipped to the scheduled regular session. A position still open when the session's
    fills end (no `EXIT` recorded -- allowed when `flatten_at_ms` is unset) is closed out
    at the session's own scheduled close for this diagnostic only; it does not assert the
    position was actually flattened there."""
    opening = session_open_local(result.session_date)
    open_ms = (opening.hour * 60 + opening.minute) * 60_000
    duration_ms = session_duration_minutes(result.session_date) * 60_000

    def elapsed(point):
        return max(0, min(point - open_ms, duration_ms))

    open_since: dict[str, int] = {}
    intervals: list[tuple[int, int]] = []
    for fill in sorted(result.fills, key=lambda f: f.available_at_ms):
        if fill.status == "FILLED":
            if fill.symbol in open_since:
                raise ValueError("duplicate entry without an intervening exit")
            open_since[fill.symbol] = elapsed(fill.available_at_ms)
        elif fill.status == "EXIT":
            start = open_since.pop(fill.symbol, None)
            if start is not None:
                intervals.append((start, elapsed(fill.available_at_ms)))
    for start in open_since.values():
        intervals.append((start, duration_ms))
    return intervals


def compute_exposure(results: list[SessionResult]) -> ExposureResult:
    total_open_ms = 0
    total_scheduled_ms = 0
    for result in results:
        scheduled_ms = session_duration_minutes(result.session_date) * 60_000
        total_scheduled_ms += scheduled_ms
        total_open_ms += sum(
            end - start
            for start, end in _merge_intervals(session_open_intervals(result))
        )
    fraction: Decimal | str = (
        UNAVAILABLE_NO_SESSIONS
        if total_scheduled_ms == 0
        else Decimal(total_open_ms) / Decimal(total_scheduled_ms)
    )
    return ExposureResult(
        open_ms=total_open_ms, total_scheduled_ms=total_scheduled_ms, fraction=fraction
    )


def compute_drawdown(equity_curve: list[SessionEquityPoint]) -> DrawdownResult:
    if not equity_curve:
        return DrawdownResult(
            equity_curve=(),
            max_drawdown=Decimal(0),
            max_drawdown_pct=UNAVAILABLE_NO_SESSIONS,
            peak_equity=Decimal(0),
            trough_equity=Decimal(0),
        )
    peak = equity_curve[0].ending_equity
    max_drawdown = Decimal(0)
    max_drawdown_pct: Decimal | str = (
        Decimal(0) if peak > 0 else UNAVAILABLE_NONPOSITIVE_PEAK
    )
    peak_at_max, trough_at_max = peak, peak
    for point in equity_curve:
        peak = max(peak, point.ending_equity)
        if max_drawdown == 0 and peak > 0:
            max_drawdown_pct = Decimal(0)
            peak_at_max = trough_at_max = peak
        decline = peak - point.ending_equity
        if decline > max_drawdown:
            max_drawdown = decline
            peak_at_max, trough_at_max = peak, point.ending_equity
            max_drawdown_pct = (
                decline / peak if peak > 0 else UNAVAILABLE_NONPOSITIVE_PEAK
            )
    return DrawdownResult(
        equity_curve=tuple(equity_curve),
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        peak_equity=peak_at_max,
        trough_equity=trough_at_max,
    )


def compute_multi_session_metrics(
    results: list[SessionResult], starting_equity: Decimal
) -> MultiSessionMetrics:
    """Combines every session's own fills for trade-level statistics (sessions are
    chronologically disjoint, so concatenation never mixes overlapping trades), and each
    session's own `ending_equity` for the drawdown curve."""
    all_fills = [fill for result in results for fill in result.fills]
    realized_pnl = sum(
        (fill.net_pnl for fill in all_fills if fill.net_pnl is not None), Decimal(0)
    )
    ending_equity = results[-1].ending_equity if results else starting_equity
    trade_metrics = compute_metrics(
        starting_equity, ending_equity, realized_pnl, all_fills
    )
    equity_curve = ([SessionEquityPoint(None, starting_equity)] if results else []) + [
        SessionEquityPoint(result.session_date, result.ending_equity)
        for result in results
    ]
    return MultiSessionMetrics(
        session_count=len(results),
        trade_metrics=trade_metrics,
        drawdown=compute_drawdown(equity_curve),
        exposure=compute_exposure(results),
    )


__all__ = [
    "UNAVAILABLE_NONPOSITIVE_PEAK",
    "UNAVAILABLE_NO_SESSIONS",
    "DrawdownResult",
    "ExposureResult",
    "MultiSessionMetrics",
    "SessionEquityPoint",
    "compute_drawdown",
    "compute_exposure",
    "compute_multi_session_metrics",
]

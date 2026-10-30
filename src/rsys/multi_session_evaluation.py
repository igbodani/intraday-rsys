"""Multi-session diagnostic statistics (Step 5 of docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md).

Exposure and drawdown are computed independently from each session's own `SessionResult`
(fills and the declared calendar's scheduled session length) -- never from an invented
intrabar timestamp or a borrowed single-session identity. These are descriptive
diagnostics only: D10 (risk policy -- a maximum drawdown *ceiling* or exposure *limit*) and
D17 (promotion gates) remain Open in docs/decisions.md and are untouched here. Nothing in
this module authorizes a trading decision, a promotion claim or a risk limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rsys.calendar import session_duration_minutes
from rsys.evaluation import Metrics, compute_metrics
from rsys.session_coordinator import SessionResult

UNAVAILABLE_NO_SESSIONS = "UNAVAILABLE_NO_SESSIONS"
UNAVAILABLE_NONPOSITIVE_PEAK = "UNAVAILABLE_NONPOSITIVE_PEAK"


@dataclass(frozen=True)
class SessionEquityPoint:
    session_date: object
    ending_equity: Decimal


@dataclass(frozen=True)
class DrawdownResult:
    """`max_drawdown` is the largest peak-to-trough decline (in money) across the
    chronological session-end equity curve; `max_drawdown_pct` divides it by the peak at
    which it occurred and is `UNAVAILABLE_NONPOSITIVE_PEAK` if that peak is not positive.
    An empty curve reports zero drawdown with an explicit unavailable percentage, never a
    fabricated zero-is-good percentage."""

    equity_curve: tuple[SessionEquityPoint, ...]
    max_drawdown: Decimal
    max_drawdown_pct: Decimal | str
    peak_equity: Decimal
    trough_equity: Decimal


@dataclass(frozen=True)
class ExposureResult:
    """Time-weighted fraction of scheduled session time with at least one open position.
    Overlapping intervals across symbols are unioned (merged), never summed, so exposure
    can never exceed 1 even when several positions are open at once."""

    open_ms: int
    total_scheduled_ms: int
    fraction: Decimal | str


@dataclass(frozen=True)
class MultiSessionMetrics:
    session_count: int
    trade_metrics: Metrics
    drawdown: DrawdownResult
    exposure: ExposureResult


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
    """Pairs each `FILLED` fill with its own symbol's next `EXIT` fill, in elapsed
    milliseconds since the session's own open. A position still open when the session's
    fills end (no `EXIT` recorded -- allowed when `flatten_at_ms` is unset) is closed out
    at the session's own scheduled close for this diagnostic only; it does not assert the
    position was actually flattened there."""
    scheduled_close_ms = session_duration_minutes(result.session_date) * 60_000
    open_since: dict[str, int] = {}
    intervals: list[tuple[int, int]] = []
    for fill in result.fills:
        if fill.status == "FILLED":
            open_since[fill.symbol] = fill.available_at_ms
        elif fill.status == "EXIT":
            start = open_since.pop(fill.symbol, None)
            if start is not None:
                intervals.append((start, fill.available_at_ms))
    for start in open_since.values():
        intervals.append((start, scheduled_close_ms))
    return intervals


def compute_exposure(results: list[SessionResult]) -> ExposureResult:
    total_open_ms = 0
    total_scheduled_ms = 0
    for result in results:
        scheduled_ms = session_duration_minutes(result.session_date) * 60_000
        total_scheduled_ms += scheduled_ms
        total_open_ms += sum(
            end - start for start, end in _merge_intervals(session_open_intervals(result))
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
    max_drawdown_pct: Decimal | str = UNAVAILABLE_NONPOSITIVE_PEAK
    peak_at_max, trough_at_max = peak, peak
    for point in equity_curve:
        peak = max(peak, point.ending_equity)
        decline = peak - point.ending_equity
        if decline > max_drawdown:
            max_drawdown = decline
            peak_at_max, trough_at_max = peak, point.ending_equity
            max_drawdown_pct = decline / peak if peak > 0 else UNAVAILABLE_NONPOSITIVE_PEAK
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
    trade_metrics = compute_metrics(starting_equity, ending_equity, realized_pnl, all_fills)
    equity_curve = [
        SessionEquityPoint(result.session_date, result.ending_equity) for result in results
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

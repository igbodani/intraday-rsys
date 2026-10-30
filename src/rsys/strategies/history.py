"""Compose bounded historical data into scanner candidates."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from rsys.data.calendar import session_open_local
from rsys.data.features import (
    compute_cumulative_rvol,
    compute_dollar_volume_eligible,
    compute_session_gain,
)
from rsys.data.history import prior_sessions
from rsys.data.providers import HistoryProvider
from rsys.strategies.models import CandidateInput


def candidate_input(
    *,
    symbol: str,
    decision_date: date,
    elapsed_minutes: int,
    current_volume: Decimal,
    latest_close: Decimal,
    provider: HistoryProvider,
    lookback_sessions: int,
    min_dollar_volume: Decimal,
    feed: str = "sip",
    history_feed: str = "sip",
) -> CandidateInput:
    """Compose `features.py`'s causal math into one `CandidateInput` for `rank_candidates`.
    Any unavailable input (history, previous close, dollar-volume) excludes rather than
    defaults; see `features.py`'s status constants for the underlying reasons."""
    dollar_volume_inputs = provider.prior_session_dollar_volume_inputs(
        symbol, decision_date
    )
    eligible = compute_dollar_volume_eligible(
        prior_session_volume=dollar_volume_inputs[0] if dollar_volume_inputs else None,
        prior_session_close=dollar_volume_inputs[1] if dollar_volume_inputs else None,
        min_dollar_volume=min_dollar_volume,
    )
    rvol = compute_cumulative_rvol(
        elapsed_minutes=elapsed_minutes,
        lookback_sessions=lookback_sessions,
        current_volume=current_volume,
        history_newest_first=prior_sessions(
            provider.sessions_before(symbol, decision_date), decision_date
        ),
        feed=feed,
        history_feed=history_feed,
    ).value
    gain = compute_session_gain(
        latest_close=latest_close,
        previous_close=provider.previous_session_close(symbol, decision_date),
    ).value
    return CandidateInput(symbol, rvol, gain, eligible)


def build_scanner_inputs(
    *,
    bars_by_symbol: dict[str, list],
    provider: HistoryProvider,
    decision_date: date,
    lookback_sessions: int,
    min_dollar_volume: Decimal,
    feed: str = "sip",
    history_feed: str = "sip",
) -> dict[int, dict[str, tuple[Decimal | None, Decimal | None, bool]]]:
    """Build the `{available_at_ms: {symbol: (rvol, gain, dollar_volume_eligible)}}` shape
    `BarSessionRunner`/`TradeInputRunner` already consume as `scanner_inputs`, from each
    symbol's own completed bars (providing today's cumulative volume and latest close) plus
    a `HistoryProvider`. Each bar's available_at_ms is `point` (ms since exchange-local
    midnight), matching the runners' own `_ms`/`point` convention. Strictly causal: a bar's
    candidate only uses that bar and earlier bars for today's cumulative volume/close."""
    session_open_ms = (
        session_open_local(decision_date).hour * 60
        + session_open_local(decision_date).minute
    ) * 60_000
    result: dict[int, dict[str, tuple[Decimal | None, Decimal | None, bool]]] = {}
    for symbol, bars in bars_by_symbol.items():
        cumulative_volume = Decimal(0)
        previous_point = None
        for bar in bars:
            if previous_point is not None and bar.available_at_ms <= previous_point:
                raise ValueError(
                    "scanner bars must have strictly increasing availability"
                )
            previous_point = bar.available_at_ms
            cumulative_volume += bar.volume
            elapsed_minutes = (bar.available_at_ms - session_open_ms) // 60_000
            row = candidate_input(
                symbol=symbol,
                decision_date=decision_date,
                elapsed_minutes=elapsed_minutes,
                current_volume=cumulative_volume,
                latest_close=bar.close,
                provider=provider,
                lookback_sessions=lookback_sessions,
                min_dollar_volume=min_dollar_volume,
                feed=feed,
                history_feed=history_feed,
            )
            result.setdefault(bar.available_at_ms, {})[symbol] = (
                row.rvol,
                row.gain,
                row.dollar_volume_eligible,
            )
    return result

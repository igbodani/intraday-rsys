"""Explicit calendar-aware historical scanner mode (M2-02a, Step 3 continuation of
docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md "Replace supplied scores in a new explicit scanner
mode. Keep the existing supplied-score fixture mode for isolated tests.").

`HistoryProvider` is a narrow interface for prior-session observations (module 16,
`features.py`, computes the actual RVOL/gain math from whatever a provider returns). The
only implementation here, `FixtureHistoryProvider`, is a declared **synthetic** in-memory
dataset -- never a real market-data loader. A real provider-backed implementation remains
blocked on D12/D13 (feed entitlement and historical-universe audits). This module's job is
only to compose the existing causal math into the same `scanner_inputs` shape the runners
(`standalone_run.py`/`trade_input_run.py`) already consume, so neither runner needs to
change: the historical mode and the supplied-score fixture mode are interchangeable inputs
to `rank_candidates`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from rsys.calendar import session_open_local
from rsys.features import (
    HistorySession,
    compute_cumulative_rvol,
    compute_dollar_volume_eligible,
    compute_session_gain,
)
from rsys.scanner import CandidateInput


class HistoryProvider(Protocol):
    """Prior-session observations for one instrument, already calendar-ordered newest
    first and in current split units. Implementations declare their own source/coverage;
    this interface carries no feed-entitlement guarantee by itself."""

    def sessions_before(self, symbol: str, decision_date: date) -> list[HistorySession]:
        """Newest-first prior sessions usable as RVOL lookback candidates."""
        ...

    def previous_session_close(self, symbol: str, decision_date: date) -> Decimal | None:
        """Previous regular-session raw close, current split units; `None` if unknown."""
        ...

    def prior_session_dollar_volume_inputs(
        self, symbol: str, decision_date: date
    ) -> tuple[Decimal, Decimal] | None:
        """`(prior_session_volume, prior_session_close)`, current split units; `None` if
        unavailable (eligibility is then `False`, never defaulted to eligible)."""
        ...


@dataclass(frozen=True)
class SyntheticSymbolHistory:
    """One symbol's declared synthetic history for `FixtureHistoryProvider`."""

    sessions: list[HistorySession]  # newest-first
    previous_session_close: Decimal | None
    prior_session_volume: Decimal | None
    prior_session_close_for_dollar_volume: Decimal | None


class FixtureHistoryProvider:
    """A declared synthetic/fixture `HistoryProvider`. `data_cases.json`'s hand-calculated
    H01-H03 values are the kind of input this wraps; it is not a provider adapter and must
    never be presented as real historical coverage (D12/D13 remain Open)."""

    def __init__(self, history: dict[str, SyntheticSymbolHistory]):
        self._history = history

    def sessions_before(self, symbol: str, decision_date: date) -> list[HistorySession]:
        record = self._history.get(symbol)
        return list(record.sessions) if record else []

    def previous_session_close(self, symbol: str, decision_date: date) -> Decimal | None:
        record = self._history.get(symbol)
        return record.previous_session_close if record else None

    def prior_session_dollar_volume_inputs(
        self, symbol: str, decision_date: date
    ) -> tuple[Decimal, Decimal] | None:
        record = self._history.get(symbol)
        if (
            record is None
            or record.prior_session_volume is None
            or record.prior_session_close_for_dollar_volume is None
        ):
            return None
        return record.prior_session_volume, record.prior_session_close_for_dollar_volume


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
    dollar_volume_inputs = provider.prior_session_dollar_volume_inputs(symbol, decision_date)
    eligible = compute_dollar_volume_eligible(
        prior_session_volume=dollar_volume_inputs[0] if dollar_volume_inputs else None,
        prior_session_close=dollar_volume_inputs[1] if dollar_volume_inputs else None,
        min_dollar_volume=min_dollar_volume,
    )
    rvol = compute_cumulative_rvol(
        elapsed_minutes=elapsed_minutes,
        lookback_sessions=lookback_sessions,
        current_volume=current_volume,
        history_newest_first=provider.sessions_before(symbol, decision_date),
        feed=feed,
        history_feed=history_feed,
    ).value
    gain = compute_session_gain(
        latest_close=latest_close,
        previous_close=provider.previous_session_close(symbol, decision_date),
    ).value
    return CandidateInput(symbol, rvol, gain, eligible)


@dataclass(frozen=True)
class _ObservedBar:
    available_at_ms: int
    close: Decimal
    volume: Decimal


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
    `StandaloneRunner`/`TradeInputRunner` already consume as `scanner_inputs`, from each
    symbol's own completed bars (providing today's cumulative volume and latest close) plus
    a `HistoryProvider`. Each bar's available_at_ms is `point` (ms since exchange-local
    midnight), matching the runners' own `_ms`/`point` convention. Strictly causal: a bar's
    candidate only uses that bar and earlier bars for today's cumulative volume/close."""
    session_open_ms = (
        session_open_local(decision_date).hour * 60 + session_open_local(decision_date).minute
    ) * 60_000
    result: dict[int, dict[str, tuple[Decimal | None, Decimal | None, bool]]] = {}
    for symbol, bars in bars_by_symbol.items():
        cumulative_volume = Decimal(0)
        for bar in bars:
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

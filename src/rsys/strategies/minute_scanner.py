"""Decision-time synthetic scanner using bounded completed bars and dated history.

This is a fixture policy, not an audited feed adapter. Each symbol uses its latest
completed minute visible at this decision. Missing current minutes are coverage
gaps; no zero-volume bars or previous-day closes are invented.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from rsys.data.calendar import session_duration_minutes, session_open_utc
from rsys.data.features import (
    compute_cumulative_rvol,
    compute_dollar_volume_eligible,
    compute_session_gain,
)
from rsys.data.history import MinuteHistoryProvider
from rsys.data.models import MinuteSymbolHistory
from rsys.replay.views import ClosedOnlyContext
from rsys.strategies.models import CandidateInput, HistoryScan
from rsys.strategies.scanner import rank_candidates, ranking_digest, ranking_evidence

HISTORY_SCANNER_VERSION = "synthetic_minute_history_v1"
MINUTE_NS = 60_000_000_000


class MinuteHistoryScanner:
    def __init__(
        self,
        history: dict[str, MinuteSymbolHistory],
        *,
        session_date: date,
        symbols: list[str],
        feed: str,
        lookback_sessions: int,
        max_lookback_calendar_sessions: int,
        min_dollar_volume: Decimal,
        max_current_bar_age_ms: int,
    ):
        self.history = history
        self.session_date = session_date
        self.symbols = sorted(symbols)
        self.feed = feed
        self.feature_version = (
            HISTORY_SCANNER_VERSION
            if feed == "fixture"
            else "historical_final_bar_features_v1"
        )
        self.lookback_sessions = lookback_sessions
        self.max_lookback = max_lookback_calendar_sessions
        self.min_dollar_volume = min_dollar_volume
        self.max_current_bar_age_ms = max_current_bar_age_ms
        self.open_ns = int(session_open_utc(session_date).timestamp()) * 1_000_000_000
        self.close_ns = (
            self.open_ns + session_duration_minutes(session_date) * MINUTE_NS
        )
        # Content-addressed snapshots avoid repeating full previous-day revisions
        # at every decision. The application seals this registry with the trace.
        self.snapshots: dict[str, dict] = {}

    def _retain(self, value) -> str:
        payload = json.loads(json.dumps(asdict(value), default=str))
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        identity = hashlib.sha256(encoded).hexdigest()
        self.snapshots[identity] = payload
        return identity

    def evaluate(
        self, context: ClosedOnlyContext, *, as_of_ns: int, top_n: int
    ) -> HistoryScan:
        as_of = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            microseconds=as_of_ns // 1000
        )
        candidates, symbols = [], {}
        for symbol in self.symbols:
            bars = context.closed_bars(symbol)
            detail = {"current_status": None}
            symbols[symbol] = detail
            if not bars:
                detail["current_status"] = "NO_COMPLETED_BAR"
                candidates.append(CandidateInput(symbol, None, None))
                continue
            latest = bars[-1]
            detail["current_bar_end_ns"] = latest.bar_end_ns
            detail["current_bar_available_at_ns"] = latest.available_at_ns
            age_ns = as_of_ns - latest.bar_end_ns
            detail["current_bar_age_ms"] = age_ns // 1_000_000
            if age_ns > self.max_current_bar_age_ms * 1_000_000:
                detail["current_status"] = "STALE_CURRENT_BAR"
                candidates.append(CandidateInput(symbol, None, None))
                continue
            elapsed = (latest.bar_end_ns - self.open_ns) // MINUTE_NS
            detail["elapsed_minutes"] = elapsed
            # Compute elapsed time from the completed interval, never delivery time.
            # A delayed publication must not request later denominator minutes.
            expected = list(range(self.open_ns, latest.bar_end_ns, MINUTE_NS))
            if (
                elapsed <= 0
                or latest.bar_end_ns > self.close_ns
                or [b.bar_start_ns for b in bars] != expected
                or any(
                    b.feed != self.feed
                    or b.interval != "1Min"
                    or b.bar_end_ns != b.bar_start_ns + MINUTE_NS
                    or b.bar_end_ns > as_of_ns
                    or b.adjustment_mode != "raw"
                    or b.close is None
                    or not b.close.is_finite()
                    or b.close <= 0
                    or not b.volume.is_finite()
                    or b.volume < 0
                    for b in bars
                )
            ):
                detail["current_status"] = "INCOMPLETE_CURRENT_MINUTES"
                candidates.append(CandidateInput(symbol, None, None))
                continue
            volume = sum((b.volume for b in bars), Decimal(0))
            provider = MinuteHistoryProvider(
                self.history,
                feed=self.feed,
                elapsed_minutes=elapsed,
                as_of=as_of,
                max_lookback_calendar_sessions=self.max_lookback,
            )
            sessions = provider.sessions_before(symbol, self.session_date)
            rvol = compute_cumulative_rvol(
                elapsed_minutes=elapsed,
                lookback_sessions=self.lookback_sessions,
                current_volume=volume,
                history_newest_first=sessions,
                feed=self.feed,
                history_feed=self.feed,
                max_lookback_calendar_sessions=self.max_lookback,
            )
            previous_close = provider.previous_session_close(symbol, self.session_date)
            previous = provider.previous_session_build(symbol)
            gain = compute_session_gain(
                latest_close=latest.close, previous_close=previous_close
            )
            eligible = compute_dollar_volume_eligible(
                prior_session_volume=previous.volume,
                prior_session_close=previous.close,
                min_dollar_volume=self.min_dollar_volume,
            )
            candidate = CandidateInput(symbol, rvol.value, gain.value, eligible)
            candidates.append(candidate)
            detail.update(
                current_volume=volume,
                latest_close=latest.close,
                rvol=asdict(rvol),
                gain=asdict(gain),
                dollar_volume_eligible=eligible,
                previous_session={
                    "session_date": previous.session_date,
                    "close": previous.close,
                    "volume": previous.volume,
                    "exclusion_reason": previous.exclusion_reason,
                    "snapshot_id": self._retain(previous),
                },
                history_sessions=[
                    {
                        **asdict(build.history_session),
                        "selected": build.history_session.session_date
                        in rvol.selected_dates,
                        "exclusion_reason": build.exclusion_reason,
                        "snapshot_id": self._retain(build),
                    }
                    for build in provider.session_builds(symbol)
                ],
            )
        scan = rank_candidates(candidates, top_n=top_n)
        for symbol, detail in symbols.items():
            detail["selection"] = (
                "SELECTED"
                if symbol in scan.top
                else "OUTSIDE_TOP_N"
                if symbol in scan.ranked
                else scan.excluded[symbol]
            )
        return HistoryScan(
            scan,
            {
                "mode": self.feature_version,
                "as_of": as_of.isoformat(),
                "symbols": symbols,
                "ranking": ranking_evidence(
                    scan.ranked_candidates,
                    feature_registry_version=self.feature_version,
                ),
                "ranking_digest": ranking_digest(
                    scan.ranked_candidates,
                    feature_registry_version=self.feature_version,
                ),
                "current_input_versions": context.input_references(),
            },
        )

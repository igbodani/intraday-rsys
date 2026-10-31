"""Synthetic history provider and strict prior-session date selection."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from rsys.data.calendar import is_trading_day, session_duration_minutes
from rsys.data.errors import CalendarCoverageError
from rsys.data.models import (
    HistorySession,
    MinuteObservation,
    MinuteSymbolHistory,
    ObservationStatus,
    PreviousSessionObservation,
    SelectedSession,
    SyntheticSymbolHistory,
    UnitBasis,
)


class FixtureHistoryProvider:
    """A declared synthetic/fixture `HistoryProvider`. `data_cases.json`'s hand-calculated
    H01-H03 values are the kind of input this wraps; it is not a provider adapter and must
    never be presented as real historical coverage (D12/D13 remain Open)."""

    def __init__(self, history: dict[str, SyntheticSymbolHistory]):
        self._history = history

    def sessions_before(self, symbol: str, decision_date: date) -> list[HistorySession]:
        record = self._history.get(symbol)
        return prior_sessions(record.sessions if record else [], decision_date)

    def previous_session_close(
        self, symbol: str, decision_date: date
    ) -> Decimal | None:
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


def prior_sessions(
    sessions: list[HistorySession], decision_date: date
) -> list[HistorySession]:
    """Enforce the dated boundary even for a provider that returns unbounded history.

    Keep missing prior rows in the calendar selection: never backfill a missing selected
    observation with an older session. Duplicate dates are ambiguous, not extra samples.
    """
    prior = [s for s in sessions if s.session_date < decision_date]
    if len({s.session_date for s in prior}) != len(prior):
        raise ValueError("duplicate scanner history session date")
    return sorted(prior, key=lambda s: s.session_date, reverse=True)


# Minute-addressable, as-of scanner history (Step 1 of
# docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md). These functions rebuild the same coarse
# `HistorySession` rows `FixtureHistoryProvider` already supplies hand-calculated, but derive
# them from declared minute-level revisions visible by an explicit decision cutoff instead of
# a caller-precomputed total. They extend this module's existing dated-selection boundary;
# `features.compute_cumulative_rvol`/`compute_session_gain`/`compute_dollar_volume_eligible`
# are unchanged and still own the RVOL/gain/eligibility math itself.

MISSING_MINUTE = "MISSING_MINUTE"
UNSUPPORTED_UNIT_BASIS = "UNSUPPORTED_UNIT_BASIS"
SESSION_TOO_SHORT = "SESSION_TOO_SHORT"


def _positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _aware_timestamp(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must have an explicit timezone")


def _validate_minute_payload(observation: MinuteObservation) -> None:
    """Validate the selected visible revision, never an unavailable future payload."""
    if (
        not isinstance(observation.volume, Decimal)
        or not observation.volume.is_finite()
        or observation.volume < 0
    ):
        raise ValueError("minute volume must be a nonnegative finite Decimal")
    if observation.status == ObservationStatus.NO_TRADE:
        if observation.volume != 0 or observation.close is not None:
            raise ValueError("a verified NO_TRADE minute must have zero volume and no close")
    elif observation.status == ObservationStatus.TRADED:
        if (
            not isinstance(observation.close, Decimal)
            or not observation.close.is_finite()
            or observation.close <= 0
        ):
            raise ValueError("a TRADED minute close must be a positive finite Decimal")
    else:
        raise ValueError("minute status must be TRADED or NO_TRADE")


def candidate_prior_session_dates(
    decision_date: date, *, max_lookback_calendar_sessions: int = 252
) -> list[date]:
    """Prior trading-session dates selected strictly from the declared calendar
    (`calendar.is_trading_day`), newest-first -- never from whatever dates happen to appear
    in a provider's observations (docs/data_rules.md: "select sessions by the dated exchange
    calendar before inspecting price/volume values"). Stops at the calendar's declared
    coverage boundary instead of raising past it: running out of covered calendar is
    insufficient history, not an error.
    """
    _positive_integer(max_lookback_calendar_sessions, "max_lookback_calendar_sessions")
    dates: list[date] = []
    cursor = decision_date
    while len(dates) < max_lookback_calendar_sessions:
        cursor = cursor - timedelta(days=1)
        try:
            trading = is_trading_day(cursor)
        except CalendarCoverageError:
            break
        if trading:
            dates.append(cursor)
    return dates


def _select_minute(
    candidates: list[MinuteObservation], *, feed: str, as_of: datetime
) -> tuple[MinuteObservation | None, str | None]:
    """The highest-revision observation visible by `as_of` under `feed`, or `(None,
    MISSING_MINUTE)` if nothing is visible yet. A future correction cannot displace a
    currently visible revision. Equal revisions must agree on payload and provenance;
    otherwise reject them instead of retaining whichever input happened to come first.
    """
    visible = []
    for observation in candidates:
        if observation.feed != feed:
            continue
        _aware_timestamp(observation.available_at, "available_at")
        if observation.available_at <= as_of:
            if type(observation.revision) is not int or observation.revision < 0:
                raise ValueError("minute revision must be a nonnegative integer")
            visible.append(observation)
    if not visible:
        return None, MISSING_MINUTE
    max_revision = max(o.revision for o in visible)
    top = [o for o in visible if o.revision == max_revision]
    for observation in top:
        _validate_minute_payload(observation)
    first = top[0]
    for other in top[1:]:
        if other != first:
            raise ValueError(
                "ambiguous minute revision for "
                f"{first.instrument_id} {first.session_date.isoformat()} "
                f"minute {first.minute_index} revision {max_revision}"
            )
    if first.unit_basis != UnitBasis.RAW:
        return first, UNSUPPORTED_UNIT_BASIS
    return first, None


def _build_session(
    observations: list[MinuteObservation],
    *,
    symbol: str,
    feed: str,
    session_date: date,
    required_minutes: int,
    scheduled_duration_minutes: int,
    as_of: datetime,
) -> SelectedSession:
    by_minute: dict[int, list[MinuteObservation]] = {}
    for observation in observations:
        if observation.instrument_id == symbol and observation.session_date == session_date:
            by_minute.setdefault(observation.minute_index, []).append(observation)

    selected: list[MinuteObservation] = []
    exclusion_reason: str | None = None
    volume_total = Decimal(0)
    for minute_index in range(required_minutes):
        chosen, reason = _select_minute(
            by_minute.get(minute_index, []), feed=feed, as_of=as_of
        )
        if chosen is not None:
            selected.append(chosen)
        if reason is not None:
            exclusion_reason = reason
            break
        volume_total += chosen.volume

    coverage_complete = exclusion_reason is None
    history_session = HistorySession(
        session_date=session_date,
        duration_minutes=scheduled_duration_minutes,
        cumulative_volume=volume_total if coverage_complete else None,
        coverage_complete=coverage_complete,
    )
    return SelectedSession(
        history_session=history_session,
        selected_revisions=tuple(selected),
        exclusion_reason=exclusion_reason,
    )


def build_minute_history_sessions(
    observations: list[MinuteObservation],
    *,
    symbol: str,
    feed: str,
    decision_date: date,
    elapsed_minutes: int,
    as_of: datetime,
    max_lookback_calendar_sessions: int = 252,
) -> list[SelectedSession]:
    """Newest-first prior-session builds, each a `HistorySession` rebuilt from
    minute-addressable observations as of `as_of` -- feed `[b.history_session for b in ...]`
    straight into `features.compute_cumulative_rvol`'s `history_newest_first`. Which calendar
    dates to even attempt is decided by the declared calendar
    (`candidate_prior_session_dates`), never by whichever dates a provider happens to supply.
    A session shorter than `elapsed_minutes` (an early close) is still returned with its real
    scheduled duration so `compute_cumulative_rvol`'s own duration filter excludes it.
    Its observations are never inspected: they cannot establish coverage through the
    requested minute, and malformed data in that ineligible session cannot affect RVOL.
    """
    _positive_integer(elapsed_minutes, "elapsed_minutes")
    _aware_timestamp(as_of, "as_of")
    dates = candidate_prior_session_dates(
        decision_date, max_lookback_calendar_sessions=max_lookback_calendar_sessions
    )
    builds = []
    for session_date in dates:
        duration = session_duration_minutes(session_date)
        if duration < elapsed_minutes:
            builds.append(
                SelectedSession(
                    history_session=HistorySession(session_date, duration, None, False),
                    selected_revisions=(),
                    exclusion_reason=SESSION_TOO_SHORT,
                )
            )
            continue
        builds.append(
            _build_session(
                observations,
                symbol=symbol,
                feed=feed,
                session_date=session_date,
                required_minutes=elapsed_minutes,
                scheduled_duration_minutes=duration,
                as_of=as_of,
            )
        )
    return builds


def build_previous_session_observation(
    observations: list[MinuteObservation],
    *,
    symbol: str,
    feed: str,
    decision_date: date,
    as_of: datetime,
) -> PreviousSessionObservation:
    """The immediately prior trading session's full-session close/volume -- the previous raw
    close used for session gain and the previous full-session volume/close used for prior-day
    dollar-volume eligibility (docs/data_rules.md); never the RVOL lookback's same-elapsed-
    minute slice, which `build_minute_history_sessions` computes separately."""
    _aware_timestamp(as_of, "as_of")
    dates = candidate_prior_session_dates(decision_date, max_lookback_calendar_sessions=1)
    if not dates:
        return PreviousSessionObservation(None, None, None, (), MISSING_MINUTE)
    session_date = dates[0]
    duration = session_duration_minutes(session_date)
    build = _build_session(
        observations,
        symbol=symbol,
        feed=feed,
        session_date=session_date,
        required_minutes=duration,
        scheduled_duration_minutes=duration,
        as_of=as_of,
    )
    if build.exclusion_reason is not None:
        return PreviousSessionObservation(
            session_date, None, None, build.selected_revisions, build.exclusion_reason
        )
    close = next(
        (
            o.close
            for o in reversed(build.selected_revisions)
            if o.status == ObservationStatus.TRADED
        ),
        None,
    )
    return PreviousSessionObservation(
        session_date,
        close,
        build.history_session.cumulative_volume,
        build.selected_revisions,
        None,
    )


class MinuteHistoryProvider:
    """A `HistoryProvider` built from minute-addressable `MinuteObservation` revisions, fixed
    to one decision's `elapsed_minutes`/`as_of`/`feed`. The `HistoryProvider` protocol carries
    no elapsed-minute parameter, so one instance answers for exactly one elapsed-minute
    decision -- mirroring how `FixtureHistoryProvider`'s precomputed `HistorySession` rows are
    already only valid for one implicit elapsed-minute value, just made explicit here.
    Construct a fresh instance for a different elapsed-minute/cutoff: this never recomputes
    "as of right now"; it only ever answers from what was visible by its own declared
    `as_of`, the same as-of discipline `BarVersionStore.as_of` already applies to live bar
    publications. This remains a declared offline/fixture provider (D12/D13 stay Open); it is
    not an Alpaca/audited adapter, and composing it across multiple same-session elapsed
    minutes (as `strategies.history.build_scanner_inputs` does for supplied scores) is a
    separate, not-yet-connected next step.
    """

    def __init__(
        self,
        history: dict[str, MinuteSymbolHistory],
        *,
        feed: str,
        elapsed_minutes: int,
        as_of: datetime,
        max_lookback_calendar_sessions: int = 252,
    ) -> None:
        _positive_integer(elapsed_minutes, "elapsed_minutes")
        _positive_integer(max_lookback_calendar_sessions, "max_lookback_calendar_sessions")
        _aware_timestamp(as_of, "as_of")
        self._history = history
        self._feed = feed
        self._elapsed_minutes = elapsed_minutes
        self._as_of = as_of
        self._max_lookback_calendar_sessions = max_lookback_calendar_sessions
        # Retained evidence from the most recent call per symbol: denominator sessions,
        # selected revisions and exclusion reasons.
        self._session_builds: dict[str, list[SelectedSession]] = {}
        self._previous_session_builds: dict[str, PreviousSessionObservation] = {}

    def _observations(self, symbol: str) -> list[MinuteObservation]:
        record = self._history.get(symbol)
        return record.observations if record else []

    def sessions_before(self, symbol: str, decision_date: date) -> list[HistorySession]:
        builds = build_minute_history_sessions(
            self._observations(symbol),
            symbol=symbol,
            feed=self._feed,
            decision_date=decision_date,
            elapsed_minutes=self._elapsed_minutes,
            as_of=self._as_of,
            max_lookback_calendar_sessions=self._max_lookback_calendar_sessions,
        )
        self._session_builds[symbol] = builds
        return [b.history_session for b in builds]

    def previous_session_close(self, symbol: str, decision_date: date) -> Decimal | None:
        return self._previous_session(symbol, decision_date).close

    def prior_session_dollar_volume_inputs(
        self, symbol: str, decision_date: date
    ) -> tuple[Decimal, Decimal] | None:
        build = self._previous_session(symbol, decision_date)
        if build.volume is None or build.close is None:
            return None
        return build.volume, build.close

    def _previous_session(
        self, symbol: str, decision_date: date
    ) -> PreviousSessionObservation:
        build = build_previous_session_observation(
            self._observations(symbol),
            symbol=symbol,
            feed=self._feed,
            decision_date=decision_date,
            as_of=self._as_of,
        )
        self._previous_session_builds[symbol] = build
        return build

    def session_builds(self, symbol: str) -> list[SelectedSession]:
        """Retained evidence: the exact `HistorySession`s, selected minute revisions and
        exclusion reasons `sessions_before` built for `symbol`'s most recent call."""
        return list(self._session_builds.get(symbol, []))

    def previous_session_build(self, symbol: str) -> PreviousSessionObservation | None:
        """Retained evidence for the most recent `previous_session_close`/
        `prior_session_dollar_volume_inputs` call for `symbol`."""
        return self._previous_session_builds.get(symbol)

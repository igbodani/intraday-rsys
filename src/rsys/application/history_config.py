"""Strict, inline synthetic history input; no provider/network/filesystem reads."""

from datetime import UTC, date, datetime, timedelta

from rsys.application.errors import RunConfigError
from rsys.application.session_config import decimal, fields, integer
from rsys.data.calendar import session_duration_minutes, session_open_utc
from rsys.data.errors import CalendarCoverageError
from rsys.data.models import (
    MinuteObservation,
    MinuteSymbolHistory,
    ObservationStatus,
    UnitBasis,
)
from rsys.strategies.minute_scanner import MinuteHistoryScanner


def parse_scanner(config):
    """Return validated history or None for the unchanged supplied-score mode."""
    if "scanner" not in config:
        return None
    spec = config["scanner"]
    fields(
        spec,
        {
            "mode",
            "feed",
            "unit_basis",
            "corporate_actions",
            "lookback_sessions",
            "max_lookback_calendar_sessions",
            "min_dollar_volume",
            "max_current_bar_age_ms",
            "history",
        },
        {"mode"},
    )
    if spec["mode"] == "supplied":
        fields(spec, {"mode"})
        return None
    if spec["mode"] != "minute_history":
        raise RunConfigError("scanner mode must be supplied or minute_history")
    required = {
        "mode",
        "feed",
        "unit_basis",
        "corporate_actions",
        "lookback_sessions",
        "max_lookback_calendar_sessions",
        "min_dollar_volume",
        "max_current_bar_age_ms",
        "history",
    }
    fields(spec, required, required)
    if (spec["feed"], spec["unit_basis"], spec["corporate_actions"]) != (
        "fixture",
        "raw",
        "none",
    ):
        raise RunConfigError(
            "minute_history requires fixture feed, raw units and an explicit no-corporate-actions fixture"
        )
    lookback = integer(spec["lookback_sessions"], minimum=1)
    maximum = integer(spec["max_lookback_calendar_sessions"], minimum=1)
    if lookback > maximum:
        raise RunConfigError("lookback_sessions exceeds max_lookback_calendar_sessions")
    decimal(spec["min_dollar_volume"], positive=True)
    integer(spec["max_current_bar_age_ms"])
    if not isinstance(spec["history"], dict):
        raise RunConfigError("scanner history must be an object of symbol observations")
    history = {}
    for symbol, rows in spec["history"].items():
        if (
            not isinstance(symbol, str)
            or not symbol.strip()
            or not isinstance(rows, list)
        ):
            raise RunConfigError(
                "history requires nonblank symbols and observation lists"
            )
        observations = []
        for row in rows:
            required = {
                "session_date",
                "minute_index",
                "available_at",
                "revision",
                "source",
                "status",
                "close",
                "volume",
            }
            fields(row, required, required)
            try:
                day = date.fromisoformat(row["session_date"])
                if day.isoformat() != row["session_date"]:
                    raise ValueError("noncanonical history date")
                minute = integer(row["minute_index"])
                if minute >= session_duration_minutes(day):
                    raise ValueError("history minute outside regular session")
                available = datetime.fromisoformat(row["available_at"])
                if available.utcoffset() is None or available.microsecond % 1000:
                    raise ValueError(
                        "history availability needs a timezone and millisecond precision"
                    )
                if available < session_open_utc(day) + timedelta(minutes=minute + 1):
                    raise ValueError("history availability precedes minute completion")
                status = ObservationStatus(row["status"])
            except (TypeError, ValueError, CalendarCoverageError) as exc:
                raise RunConfigError(f"invalid history observation: {exc}") from exc
            integer(row["revision"])
            if not isinstance(row["source"], str) or not row["source"].strip():
                raise RunConfigError("history source must be nonblank")
            close = (
                decimal(row["close"], positive=True)
                if row["close"] is not None
                else None
            )
            volume = decimal(row["volume"])
            if status == ObservationStatus.NO_TRADE and (
                volume != 0 or close is not None
            ):
                raise RunConfigError("NO_TRADE requires zero volume and no close")
            if status == ObservationStatus.TRADED and close is None:
                raise RunConfigError("TRADED requires a close")
            observations.append(
                MinuteObservation(
                    symbol,
                    spec["feed"],
                    row["source"],
                    day,
                    minute,
                    available.astimezone(UTC),
                    row["revision"],
                    status,
                    UnitBasis.RAW,
                    close,
                    volume,
                )
            )
        history[symbol] = MinuteSymbolHistory(observations)
    return history


def build_history_scanner(config, history, day, symbols):
    if history is None:
        return None
    spec = config["scanner"]
    return MinuteHistoryScanner(
        history,
        session_date=day,
        symbols=list(symbols),
        feed=spec["feed"],
        lookback_sessions=spec["lookback_sessions"],
        max_lookback_calendar_sessions=spec["max_lookback_calendar_sessions"],
        min_dollar_volume=decimal(spec["min_dollar_volume"], positive=True),
        max_current_bar_age_ms=spec["max_current_bar_age_ms"],
    )

"""Strict input boundary for the synthetic one-session run schema (not ReplayConfig)."""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal, InvalidOperation

from rsys.calendar import CalendarCoverageError, session_close_local, session_open_local


class RunConfigError(ValueError):
    pass


def fields(value, allowed, required=()):
    if not isinstance(value, dict):
        raise RunConfigError("expected an object")
    if set(value) - set(allowed):
        raise RunConfigError(f"unsupported fields: {sorted(set(value) - set(allowed))}")
    if set(required) - set(value):
        raise RunConfigError(
            f"missing required config fields: {sorted(set(required) - set(value))}"
        )


def decimal(value, *, positive=False):
    if isinstance(value, bool):
        raise RunConfigError("boolean is not an amount")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RunConfigError("invalid decimal") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise RunConfigError(
            "amount must be finite and nonnegative (positive where required)"
        )
    return result


def integer(value, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise RunConfigError(f"expected integer >= {minimum}")
    return value


def ms(value):
    if not isinstance(value, str):
        raise RunConfigError("expected exchange-local time text")
    try:
        t = (
            datetime.fromisoformat(value).time()
            if "T" in value
            else time.fromisoformat(value)
        )
        if t.tzinfo is not None or (
            "T" in value and datetime.fromisoformat(value).tzinfo is not None
        ):
            raise ValueError("offset is not permitted in exchange-local timestamps")
        if t.microsecond % 1000:
            raise ValueError(
                "sub-millisecond timestamps are unsupported by this schema"
            )
        return ((t.hour * 60 + t.minute) * 60 + t.second) * 1000 + t.microsecond // 1000
    except ValueError as exc:
        raise RunConfigError(f"invalid exchange-local timestamp: {value!r}") from exc


def validate(config):
    fields(
        config,
        {
            "schema_version",
            "evidence_type",
            "initial_cash",
            "top_n",
            "sizing",
            "execution",
            "symbols",
            "scanner_inputs",
        },
        {"schema_version", "evidence_type", "initial_cash", "symbols"},
    )
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise RunConfigError("unsupported synthetic schema_version")
    if config["evidence_type"] != "synthetic_standalone":
        raise RunConfigError(
            "only synthetic_standalone evidence is supported; research/holdout input is blocked"
        )
    decimal(config["initial_cash"])
    integer(config.get("top_n", 20), minimum=1)
    sizing = config.get("sizing", {})
    fields(
        sizing,
        {"risk_budget", "max_quantity", "quantity_step", "entry_fee", "exit_fee"},
    )
    for key, value in sizing.items():
        if value is None and key in ("risk_budget", "max_quantity"):
            continue
        decimal(value, positive=key in ("quantity_step", "max_quantity"))
    execution = config.get("execution", {})
    fields(execution, {"max_hold_ms", "flatten_at", "decision_latency_ms"})
    if execution.get("max_hold_ms") is not None:
        integer(execution["max_hold_ms"], minimum=1)
    integer(execution.get("decision_latency_ms", 0))
    symbols = config["symbols"]
    if not isinstance(symbols, dict) or not symbols:
        raise RunConfigError("symbols must be a nonempty object")
    dates, stamped = set(), []
    for symbol, spec in symbols.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise RunConfigError("symbol must be nonblank")
        fields(
            spec,
            {"strategy", "session_open", "bars", "strategy_config"},
            {"strategy", "bars"},
        )
        if spec["strategy"] not in ("orb_close_cross_v1", "vwap_touch_hold_v1"):
            raise RunConfigError(f"unknown strategy id: {spec['strategy']!r}")
        options = spec.get("strategy_config", {})
        common = {"base_interval_minutes", "entry_deadline_ms", "target_r", "tick"}
        specific = (
            {"range_minutes", "breakout_buffer_ticks"}
            if spec["strategy"] == "orb_close_cross_v1"
            else {
                "warmup_bars",
                "slope_lookback_bars",
                "pullback_max_bars",
                "confirmation_buffer_ticks",
            }
        )
        fields(options, common | specific)
        for key, value in options.items():
            if key in ("tick", "target_r"):
                decimal(value, positive=True)
            elif key == "entry_deadline_ms" and value is None:
                continue
            else:
                integer(
                    value,
                    minimum=0
                    if key.endswith("_ticks") or key == "entry_deadline_ms"
                    else 1,
                )
        if options.get("base_interval_minutes", 1) != 1:
            raise RunConfigError(
                "current synthetic runner supports only one-minute bars"
            )
        if options.get("range_minutes", 15) > 390:
            raise RunConfigError("opening range exceeds supported session")
        bars = spec["bars"]
        if not isinstance(bars, list) or not bars:
            raise RunConfigError("each symbol needs nonempty bars")
        previous = -1
        for bar in bars:
            fields(
                bar,
                {
                    "bar_start",
                    "bar_end",
                    "available_at",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                },
                {
                    "bar_start",
                    "bar_end",
                    "available_at",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                },
            )
            for key in ("bar_start", "bar_end", "available_at"):
                if not isinstance(bar[key], str) or "T" not in bar[key]:
                    raise RunConfigError("bar timestamps must include a session date")
                ms(bar[key])
                dates.add(datetime.fromisoformat(bar[key]).date())
            start, end, available = (
                ms(bar[k]) for k in ("bar_start", "bar_end", "available_at")
            )
            if (
                start % 60_000
                or end - start != 60_000
                or available < end
                or start <= previous
            ):
                raise RunConfigError(
                    "invalid, duplicate, unordered or prematurely published minute interval"
                )
            previous = start
            stamped.append((start, end, available))
            o, h, lo, c = (
                decimal(bar[k], positive=True) for k in ("open", "high", "low", "close")
            )
            if not lo <= min(o, c) <= max(o, c) <= h:
                raise RunConfigError("inconsistent OHLC")
            decimal(bar["volume"])
    if len(dates) != 1:
        raise RunConfigError("multi-session composition is unsupported")
    session = next(iter(dates))
    try:
        start = ms(session_open_local(session).isoformat())
        close = ms(session_close_local(session).isoformat())
    except CalendarCoverageError as exc:
        raise RunConfigError(str(exc)) from exc
    if any(s < start or e > close or a > close for s, e, a in stamped):
        raise RunConfigError("bars must lie within the declared regular session")

    def same_session(value):
        if (
            isinstance(value, str)
            and "T" in value
            and datetime.fromisoformat(value).date() != session
        ):
            raise RunConfigError("timestamp date differs from bar session")

    same_session(execution.get("flatten_at"))
    for spec in symbols.values():
        same_session(spec.get("session_open"))
        if (
            spec["strategy"] == "vwap_touch_hold_v1"
            and ms(spec["bars"][0]["bar_start"]) != start
        ):
            raise RunConfigError(
                "session VWAP requires observations from the session open"
            )
        if ms(spec.get("session_open", "09:30:00")) != start:
            raise RunConfigError("session_open differs from declared calendar")
        deadline = spec.get("strategy_config", {}).get("entry_deadline_ms")
        if deadline is not None and not start < deadline <= close:
            raise RunConfigError("entry deadline outside session")
    if (
        execution.get("flatten_at") is not None
        and not start <= ms(execution["flatten_at"]) < close
    ):
        raise RunConfigError("flatten_at must precede session close")
    scanners = config.get("scanner_inputs", {})
    if not isinstance(scanners, dict):
        raise RunConfigError("scanner_inputs must be an object")
    seen = set()
    for point, rows in scanners.items():
        timestamp = ms(point)
        if "T" in point and datetime.fromisoformat(point).date() != session:
            raise RunConfigError("scanner date differs from bar session")
        if timestamp in seen or not start <= timestamp <= close:
            raise RunConfigError("duplicate scanner time or outside session")
        seen.add(timestamp)
        if not isinstance(rows, dict) or set(rows) - set(symbols):
            raise RunConfigError("scanner symbols must belong to configured universe")
        for row in rows.values():
            fields(row, {"rvol", "gain"}, {"rvol", "gain"})
            if row["rvol"] is not None:
                decimal(row["rvol"])
            if row["gain"] is None:
                continue
            try:
                gain = Decimal(str(row["gain"]))
                if isinstance(row["gain"], bool) or not gain.is_finite():
                    raise ValueError("invalid gain")
            except (InvalidOperation, ValueError) as exc:
                raise RunConfigError("gain must be finite") from exc
    return session.isoformat()

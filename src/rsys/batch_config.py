"""Version-2 public synthetic sessions schema. All risk/cost conventions are explicit."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from rsys.calendar import CalendarCoverageError, session_close_local, session_open_local
from rsys.risk_policy import ExecutionCosts, RiskPolicy
from rsys.session_config import RunConfigError, decimal, fields, integer, ms, validate


def risk_policy(config):
    raw = config["risk"]
    return RiskPolicy(**{
        k: decimal(v, positive=True) if k.endswith("fraction") else v
        for k, v in raw.items()
    })


def execution_costs(config):
    return ExecutionCosts(decimal(config["costs"]["adverse_bps"]), config["costs"]["feed_delay_ms"])


def _dated_time(value, session_date):
    point = ms(value)
    if not isinstance(value, str) or "T" not in value:
        raise RunConfigError("market timestamps require a date")
    if datetime.fromisoformat(value).date() != session_date:
        raise RunConfigError("market timestamp differs from session date")
    return point


def _strategy(spec, opening, closing):
    name = spec["strategy"]
    if name not in ("orb_close_cross_v1", "vwap_touch_hold_v1"):
        raise RunConfigError("unknown strategy id")
    options = spec.get("strategy_config", {})
    common = {"base_interval_minutes", "entry_deadline_ms", "target_r", "tick"}
    specific = ({"range_minutes", "breakout_buffer_ticks"} if name == "orb_close_cross_v1"
                else {"warmup_bars", "slope_lookback_bars", "pullback_max_bars", "confirmation_buffer_ticks"})
    fields(options, common | specific)
    for key, value in options.items():
        if key in ("target_r", "tick"):
            decimal(value, positive=True)
        elif key == "entry_deadline_ms" and value is None:
            continue
        else:
            integer(value, minimum=0 if key.endswith("_ticks") or key == "entry_deadline_ms" else 1)
    if options.get("base_interval_minutes", 1) != 1 or options.get("range_minutes", 15) > 390:
        raise RunConfigError("only one-minute intervals and supported opening ranges are implemented")
    if ms(spec.get("session_open", "09:30:00")) != opening:
        raise RunConfigError("session_open differs from calendar")
    deadline = options.get("entry_deadline_ms")
    if deadline is not None and not opening < deadline <= closing:
        raise RunConfigError("entry deadline outside session")


def _scanner(rows_by_time, symbols, day, opening, closing):
    if not isinstance(rows_by_time, dict):
        raise RunConfigError("scanner_inputs must be an object")
    seen = set()
    for text, rows in rows_by_time.items():
        point = ms(text)
        if "T" in text:
            _dated_time(text, day)
        if point in seen or not opening <= point <= closing:
            raise RunConfigError("duplicate scanner time or outside session")
        seen.add(point)
        if not isinstance(rows, dict) or set(rows) - set(symbols):
            raise RunConfigError("scanner symbols must belong to session universe")
        for row in rows.values():
            fields(row, {"rvol", "gain"}, {"rvol", "gain"})
            if row["rvol"] is not None:
                decimal(row["rvol"])
            if row["gain"] is not None:
                try:
                    value = Decimal(str(row["gain"]))
                    if isinstance(row["gain"], bool) or not value.is_finite():
                        raise ValueError("invalid gain")
                except (InvalidOperation, ValueError) as exc:
                    raise RunConfigError("gain must be finite") from exc


def _trades(spec, day, opening, closing):
    trades = spec["trades"]
    if not isinstance(trades, list) or not trades:
        raise RunConfigError("each symbol needs nonempty trades")
    previous, identities = -1, set()
    for trade in trades:
        required = {"trade_id", "event_time", "available_at", "price", "size", "quality_ok"}
        fields(trade, required | {"conditions", "tape"}, required)
        identity = trade["trade_id"]
        if not isinstance(identity, str) or not identity.strip() or identity in identities:
            raise RunConfigError("trade identities must be nonblank and unique per symbol")
        identities.add(identity)
        market = _dated_time(trade["event_time"], day)
        available = _dated_time(trade["available_at"], day)
        if not opening <= market < closing or not market <= available <= closing or market <= previous:
            raise RunConfigError("invalid or unordered trade times; equal market timestamps are unsupported")
        previous = market
        decimal(trade["price"], positive=True)
        decimal(trade["size"], positive=True)
        if type(trade["quality_ok"]) is not bool:
            raise RunConfigError("quality_ok must be boolean")
        conditions = trade.get("conditions", [])
        if not isinstance(conditions, list) or any(c not in ("ALL", "VOLUME_ONLY", "LATE_REPORT") for c in conditions):
            raise RunConfigError("only declared synthetic condition codes are supported")
        if trade.get("tape") is not None and not isinstance(trade["tape"], str):
            raise RunConfigError("tape must be text or null")
    if spec["strategy"] == "vwap_touch_hold_v1" and ms(trades[0]["event_time"]) // 60000 != opening // 60000:
        raise RunConfigError("session VWAP requires observations in the opening minute")
    corrections = spec.get("corrections", [])
    if not isinstance(corrections, list):
        raise RunConfigError("corrections must be a list")
    for correction in corrections:
        required = {"original_trade_id", "available_at", "action"}
        fields(correction, required | {"corrected_price", "corrected_size", "conditions", "tape"}, required)
        if correction["original_trade_id"] not in identities:
            raise RunConfigError("correction references an unknown trade")
        point = _dated_time(correction["available_at"], day)
        if not opening <= point <= closing:
            raise RunConfigError("correction outside session")
        if correction["action"] == "correct":
            for field in ("corrected_price", "corrected_size"):
                if field not in correction:
                    raise RunConfigError("replacement correction requires price and size")
                decimal(correction[field], positive=True)
        elif correction["action"] != "cancel" or any(correction.get(k) is not None for k in ("corrected_price", "corrected_size")):
            raise RunConfigError("invalid cancellation/replacement correction")
        if correction.get("conditions") is not None and (
            not isinstance(correction["conditions"], list)
            or any(c not in ("ALL", "VOLUME_ONLY", "LATE_REPORT") for c in correction["conditions"])
        ):
            raise RunConfigError("unsupported correction conditions")
        if correction.get("tape") is not None and not isinstance(correction["tape"], str):
            raise RunConfigError("correction tape must be text or null")


def validate_batch(config):
    required = {"schema_version", "evidence_type", "input_mode", "initial_cash", "sizing",
                "risk", "costs", "execution", "completion_policy", "sessions"}
    fields(config, required | {"top_n"}, required)
    if type(config["schema_version"]) is not int or config["schema_version"] != 2:
        raise RunConfigError("unsupported sessions schema_version")
    if config["evidence_type"] != "synthetic_sessions":
        raise RunConfigError("only synthetic_sessions evidence is supported; historical/holdout input remains blocked")
    if config["input_mode"] not in ("bar", "trade"):
        raise RunConfigError("input_mode must be bar or trade")
    if config["completion_policy"] not in ("require_flat", "allow_final_open_snapshot"):
        raise RunConfigError("unsupported completion_policy")
    decimal(config["initial_cash"], positive=True)
    integer(config.get("top_n", 20), minimum=1)
    fields(config["sizing"], {"quantity_step", "max_quantity"}, {"quantity_step", "max_quantity"})
    decimal(config["sizing"]["quantity_step"], positive=True)
    if config["sizing"]["max_quantity"] is not None:
        decimal(config["sizing"]["max_quantity"], positive=True)
    risk_fields = {"per_trade_fraction", "daily_loss_fraction", "maximum_drawdown_fraction", "daily_loss_basis", "halt_action"}
    fields(config["risk"], risk_fields, risk_fields)
    cost_fields = {"adverse_bps", "feed_delay_ms", "entry_fee", "exit_fee"}
    fields(config["costs"], cost_fields, cost_fields)
    for k in ("adverse_bps", "entry_fee", "exit_fee"):
        decimal(config["costs"][k])
    integer(config["costs"]["feed_delay_ms"])
    try:
        risk_policy(config)
        execution_costs(config)
    except ValueError as exc:
        raise RunConfigError(str(exc)) from exc
    fields(config["execution"], {"max_hold_ms", "decision_latency_ms"}, {"max_hold_ms", "decision_latency_ms"})
    if config["execution"]["max_hold_ms"] is not None:
        integer(config["execution"]["max_hold_ms"], minimum=1)
    integer(config["execution"]["decision_latency_ms"])
    if not isinstance(config["sessions"], list) or not config["sessions"]:
        raise RunConfigError("sessions must be a nonempty list")
    previous = None
    for session in config["sessions"]:
        required_session = {"date", "symbols", "scanner_inputs", "flatten_at"}
        fields(session, required_session, required_session)
        try:
            day = date.fromisoformat(session["date"])
            if day.isoformat() != session["date"]:
                raise ValueError("noncanonical date")
            opening, closing = ms(session_open_local(day).isoformat()), ms(session_close_local(day).isoformat())
        except (CalendarCoverageError, ValueError, TypeError) as exc:
            raise RunConfigError(f"invalid or unsupported session date: {session['date']!r}") from exc
        if previous is not None and day <= previous:
            raise RunConfigError("sessions must be strictly increasing")
        previous = day
        flatten = session["flatten_at"]
        if flatten is not None:
            ms(flatten)
            if "T" in flatten:
                _dated_time(flatten, day)
            if not opening <= ms(flatten) < closing:
                raise RunConfigError("flatten_at must precede session close")
        symbols = session["symbols"]
        if not isinstance(symbols, dict) or not symbols:
            raise RunConfigError("symbols must be a nonempty object")
        if config["input_mode"] == "bar":
            legacy = {"schema_version": 1, "evidence_type": "synthetic_standalone",
                      "initial_cash": config["initial_cash"], "symbols": symbols,
                      "scanner_inputs": session["scanner_inputs"], "execution": {"flatten_at": flatten}}
            if validate(legacy) != session["date"]:
                raise RunConfigError("bar dates differ from session date")
        else:
            for symbol, spec in symbols.items():
                if not isinstance(symbol, str) or not symbol.strip():
                    raise RunConfigError("symbol must be nonblank")
                fields(spec, {"strategy", "strategy_config", "session_open", "trades", "corrections"}, {"strategy", "trades"})
                _strategy(spec, opening, closing)
                if "T" in spec.get("session_open", ""):
                    _dated_time(spec["session_open"], day)
                _trades(spec, day, opening, closing)
            _scanner(session["scanner_inputs"], symbols, day, opening, closing)
        sources = [row for spec in symbols.values()
                   for key in ("bars", "trades", "corrections") for row in spec.get(key, [])]
        if any(ms(row["available_at"]) + config["costs"]["feed_delay_ms"] >= 86_400_000 for row in sources):
            raise RunConfigError("modeled feed delay moves input outside the declared session date")
    return config

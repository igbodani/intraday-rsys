"""Explicit stock-trade acquisition and credential-free historical intrabar replay."""

import gzip
import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from html import escape
from itertools import groupby
from pathlib import Path

from rsys.account.models import SizingPolicy
from rsys.account.portfolio import Portfolio
from rsys.account.risk import RiskMonitor
from rsys.application.batch_config import (
    _strategy,
    execution_costs,
    risk_policy,
    validate_account_settings,
)
from rsys.application.builders import build_strategy
from rsys.application.dataset_policy import DatasetPolicy
from rsys.application.file_attempt import file_attempt
from rsys.application.models import SessionResult
from rsys.application.session_config import decimal, fields, integer, ms
from rsys.data.alpaca import AlpacaTradesClient
from rsys.data.tape_calendar import (
    REGULAR_OPEN,
    VERSION,
    canonical_day,
    execution_dates,
    prior_dates,
    tape_hours,
)
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.data.trade_tape import acquire_tape, load_tape, tape_request
from rsys.execution.intrabar import IntrabarSession
from rsys.execution.models import ExecutionPolicy
from rsys.research.diagnostics import atomic_write_json
from rsys.research.experiments import (
    compute_dependency_lock_hash,
    compute_source_fingerprint,
    redact_secrets,
)
from rsys.research.multi_session import compute_multi_session_metrics
from rsys.strategies.intrabar import BASE_STRATEGIES, TapeVolumeHistory, TradeScanner


def _flatten_ms(account, hours):
    if ("flatten_at" in account) == ("flatten_minutes_before_close" in account):
        raise ValueError("Specify flatten_at or flatten_minutes_before_close")
    if "flatten_minutes_before_close" in account:
        lead = integer(account["flatten_minutes_before_close"], minimum=1)
        value = (hours.order_close - lead) * 60000
    else:
        value = ms(account["flatten_at"])
    if not hours.order_open * 60000 <= value < hours.order_close * 60000:
        raise ValueError("Flatten time outside eligible trading session")
    return value


def _regular_offsets(hours):
    return (
        (REGULAR_OPEN - hours.data_open) * 60_000_000_000,
        (hours.regular_close - hours.data_open) * 60_000_000_000,
    )


def _base_strategy(config):
    spec = config["strategy"]
    if spec["strategy"] not in BASE_STRATEGIES:
        raise ValueError("Select an intrabar strategy: " + ", ".join(BASE_STRATEGIES))
    return {**spec, "strategy": BASE_STRATEGIES[spec["strategy"]]}


def plan_trade_tapes(config, log, *, replay):
    keys = {
        "schema_version",
        "cache",
        "feed",
        "symbols",
        "partitions",
        "purpose",
        "timing_basis",
        "print_policy",
        "scanner",
        "strategy",
        "account",
        "action_review",
    }
    options = {
        "sessions",
        "date_range",
        "evidence_detail",
        "session_mode",
        "strategy_anchor",
        "limit_offset_bps",
    }
    fields(config, keys | options, keys)
    if config.get("evidence_detail", "full") not in ("full", "signals"):
        raise ValueError("Unknown evidence_detail")
    mode = config.get("session_mode", "regular")
    if mode != "regular":
        if config.get("strategy_anchor") not in ("eligible_open", "regular_open"):
            raise ValueError("Select strategy_anchor for extended replay")
        if (
            "limit_offset_bps" not in config
            or decimal(config["limit_offset_bps"]) >= 10000
        ):
            raise ValueError("Specify limit_offset_bps in [0, 10000)")
    elif "limit_offset_bps" in config or "strategy_anchor" in config:
        raise ValueError("Extended execution settings require an extended session mode")
    if (
        type(config["schema_version"]) is not int
        or config["schema_version"] != 1
        or config["timing_basis"] != "final_trades_delayed_ms_v1"
        or config["print_policy"]
        != (
            "alpaca_minute_conditions_regular_fills_v1"
            if mode == "regular"
            else "alpaca_day_limit_v1"
        )
    ):
        raise ValueError("Unsupported trade tape schema, timing or condition policy")
    symbols = config["symbols"]
    if (
        not isinstance(symbols, list)
        or not symbols
        or len(symbols) != len(set(symbols))
    ):
        raise ValueError("Declare a unique nonempty universe")
    scan = config["scanner"]
    skeys = {"top_n", "lookback_sessions", "max_price_age_ms", "min_dollar_volume"}
    fields(scan, skeys, skeys)
    lookback = integer(scan["lookback_sessions"], minimum=1)
    integer(scan["top_n"], minimum=1)
    integer(scan["max_price_age_ms"])
    decimal(scan["min_dollar_volume"], positive=True)
    days = execution_dates(config)
    prior = {day: prior_dates(day, lookback, mode) for day in days}
    required = sorted(set(days).union(*prior.values()))
    policy = DatasetPolicy(config["partitions"], config["purpose"])
    try:
        for d in required:
            policy.check(d)
    finally:
        log.extend(policy.log)
    requests = {
        d: {s: tape_request(s, d, config["feed"], mode) for s in symbols}
        for d in required
    }
    if replay:
        review = config["action_review"]
        if set(review) != set(symbols):
            raise ValueError("Review corporate actions for every symbol")
        for r in review.values():
            fields(
                r,
                {"status", "source", "start", "end", "splits"},
                {"status", "source", "start", "end"},
            )
            if (
                r["status"] not in ("verified_none", "verified_splits_only")
                or not isinstance(r["source"], str)
                or not r["source"].strip()
                or date.fromisoformat(r["start"]) > required[0]
                or date.fromisoformat(r["end"]) < required[-1]
            ):
                raise ValueError(
                    "Action review must cover all execution and history sessions"
                )
            splits = r.get("splits", [])
            if not isinstance(splits, list) or (
                r["status"] == "verified_splits_only"
            ) != bool(splits):
                raise ValueError(
                    "Provide the reviewed split inventory for verified_splits_only"
                )
            seen_dates = set()
            for split in splits:
                fields(
                    split,
                    {"date", "ratio", "known_before_open"},
                    {"date", "ratio", "known_before_open"},
                )
                effective = canonical_day(split["date"])
                tape_hours(effective, mode)
                if (
                    split["known_before_open"] is not True
                    or effective in seen_dates
                    or not date.fromisoformat(r["start"])
                    <= effective
                    <= date.fromisoformat(r["end"])
                ):
                    raise ValueError(
                        "Split must be unique and verified known before the data window opened"
                    )
                seen_dates.add(effective)
                decimal(split["ratio"], positive=True)
        account = config["account"]
        akeys = {
            "initial_cash",
            "completion_policy",
            "risk",
            "sizing",
            "execution",
            "costs",
        }
        fields(account, akeys | {"flatten_at", "flatten_minutes_before_close"}, akeys)
        validate_account_settings(account)
        if mode != "regular" and decimal(account["sizing"]["quantity_step"]) % 1:
            raise ValueError("Extended day-limit profile requires whole shares")
        if account["completion_policy"] != "require_flat":
            raise ValueError("Trade replay requires flat completion")
        fields(
            config["strategy"],
            {"strategy", "strategy_config"},
            {"strategy", "strategy_config"},
        )
        for d in days:
            hours = tape_hours(d, mode)
            opening = (
                hours.order_open
                if config.get("strategy_anchor") == "eligible_open"
                else REGULAR_OPEN
            )
            spec = {
                **_base_strategy(config),
                "session_open": f"{opening // 60:02}:{opening % 60:02}:00",
            }
            _strategy(spec, opening * 60000, hours.order_close * 60000)
            _flatten_ms(account, hours)
    return days, prior, requests


def _render(result):
    return (
        '<!doctype html><meta charset="utf-8"><title>Historical trade replay</title>'
        "<h1>Historical trade replay</h1><pre>"
        + escape(json.dumps(result, indent=2, default=str))
        + "</pre>"
    )


def run_tape_command(
    config,
    out,
    *,
    acquire,
    pins_path=None,
    pins=None,
    client=None,
    watch=None,
    invocation=None,
    experiment_context=None,
):
    command = "trade-download" if acquire else "trade-backtest"
    result = {"status": "failed", "command": command, "access_log": []}
    with file_attempt(
        out,
        result,
        command=command,
        directory="trade-attempts",
        link_method="link_trade_attempt",
        report_name="result.json",
        renderer=_render,
        write_json=atomic_write_json,
        invocation=invocation,
    ) as (writer, attempt):
        writer.use_config(config)
        if experiment_context is not None:
            result["experiment"] = asdict(experiment_context)
        days, prior, requests = plan_trade_tapes(
            config, result["access_log"], replay=not acquire
        )
        atomic_write_json(attempt / "input.json", redact_secrets(config))
        result.update(
            calendar=VERSION,
            session_mode=config.get("session_mode", "regular"),
            universe_basis="user_supplied_fixed_list",
            estimate_label=f"{config['feed'].upper()}-based estimates",
            timing_basis=config["timing_basis"],
            print_policy=config["print_policy"],
            code_hash=compute_source_fingerprint()[0],
            dependency_hash=compute_dependency_lock_hash(),
        )
        if acquire:
            client = client or AlpacaTradesClient()
            pins = {}
            for day, reqs in requests.items():
                pins[day.isoformat()] = {}
                for s, req in reqs.items():
                    pins[day.isoformat()][s] = acquire_tape(
                        config["cache"], req, client
                    )
                    atomic_write_json(attempt / "pins.json", pins)
        else:
            if pins is None:
                pins = json.loads(Path(pins_path).read_bytes())
            if set(pins) != {d.isoformat() for d in requests} or any(
                set(pins[d.isoformat()]) != set(reqs) for d, reqs in requests.items()
            ):
                raise ValueError("Pin exactly the required symbol/session trade tapes")
            atomic_write_json(attempt / "pins.json", pins)
            result["strategy"] = config["strategy"]
            _run_sessions(config, pins, requests, days, prior, result, attempt, watch)
        result["status"] = "success"
    return attempt


def _run_sessions(config, pins, requests, days, prior, result, attempt, watch):
    account = config["account"]
    cash = decimal(account["initial_cash"], positive=True)
    portfolio = Portfolio(cash, cash, Decimal(0))
    risk = RiskMonitor(risk_policy(account), cash)
    sizing = SizingPolicy(
        quantity_step=decimal(account["sizing"]["quantity_step"], positive=True),
        max_quantity=decimal(account["sizing"]["max_quantity"], positive=True)
        if account["sizing"]["max_quantity"] is not None
        else None,
        entry_fee=decimal(account["costs"]["entry_fee"]),
        exit_fee=decimal(account["costs"]["exit_fee"]),
    )
    spec = _base_strategy(config)
    aggregate, sessions = [], []
    result["sessions"] = sessions

    mode = config.get("session_mode", "regular")

    def read(day, symbol):
        request = requests[day][symbol]
        opening_ns = parse_utc_timestamp_ns(request["start"])
        offsets = _regular_offsets(tape_hours(day, mode))
        return load_tape(
            config["cache"],
            request,
            pins[day.isoformat()][symbol],
            extended_window=tuple(opening_ns + dt for dt in offsets)
            if mode != "regular"
            else None,
        )

    summaries = {}

    def history_for(old, day, symbol):
        ratio = Decimal(1)
        for split in config["action_review"][symbol].get("splits", []):
            if old.isoformat() < split["date"] <= day.isoformat():
                ratio *= decimal(split["ratio"], positive=True)
        hours = tape_hours(old, mode)
        key = (old, symbol, ratio)
        if key not in summaries:
            summaries[key] = TapeVolumeHistory(
                read(old, symbol),
                (hours.data_close - hours.data_open) * 60000,
                _regular_offsets(hours),
                ratio,
            )
        return summaries[key]

    with gzip.open(
        attempt / "events.jsonl.gz", "wt", encoding="utf-8", compresslevel=1
    ) as stream:
        for day in days:
            hours = tape_hours(day, mode)
            anchor = (
                hours.order_open
                if config.get("strategy_anchor") == "eligible_open"
                else REGULAR_OPEN
            )
            spec["session_open"] = f"{anchor // 60:02}:{anchor % 60:02}:00"
            strategies = {
                s: build_strategy(s, spec["strategy"], spec)
                for s in sorted(config["symbols"])
            }
            relevant = set(prior[day])
            summaries = {k: v for k, v in summaries.items() if k[0] in relevant}
            history = {
                s: [history_for(d, day, s) for d in prior[day]] for s in strategies
            }
            settings = config["scanner"]
            scanner = TradeScanner(
                history,
                lookback=settings["lookback_sessions"],
                top_n=settings["top_n"],
                regular_offsets_ns=_regular_offsets(hours),
                max_age_ms=settings["max_price_age_ms"],
                min_dollar_volume=decimal(settings["min_dollar_volume"]),
            )
            runner = IntrabarSession(
                strategies,
                portfolio,
                sizing,
                execution=ExecutionPolicy(
                    **account["execution"], flatten_at_ms=_flatten_ms(account, hours)
                ),
                risk=risk,
                costs=execution_costs(account),
                session_date=day.isoformat(),
                feed=config["feed"],
            )

            session_label = day.isoformat()

            def write(event, session=session_label):
                row = {"session": session, **event}
                if (
                    config.get("evidence_detail", "full") == "full"
                    or event["kind"] not in ("minute_close", "trade_batch")
                    or event["signals"]
                ):
                    stream.write(
                        json.dumps(row, separators=(",", ":"), default=str) + "\n"
                    )
                if watch:
                    watch(row)

            tapes = [read(day, s) for s in strategies]
            print_counts = {
                s: len(tape) for s, tape in zip(strategies, tapes, strict=True)
            }
            runner.play(
                tapes,
                scanner,
                hours.data_open * 60000,
                hours.data_close * 60000,
                write,
                order_window=(hours.order_open * 60000, hours.order_close * 60000),
                strategy_open=anchor * 60000,
                limit_bps=decimal(config["limit_offset_bps"])
                if mode != "regular"
                else None,
                full_evidence=bool(watch)
                or config.get("evidence_detail", "full") == "full",
            )
            del tapes
            aggregate.append(
                SessionResult(
                    day,
                    runner.origin_ns + hours.order_open * 60_000_000_000,
                    [],
                    runner.fills,
                    [],
                    portfolio.equity({}),
                    (hours.order_open * 60000, hours.order_close * 60000),
                )
            )
            sessions.append(
                {
                    "date": day.isoformat(),
                    "hours_et_minutes": asdict(hours),
                    "print_counts": print_counts,
                    "strategy_open_minute": anchor,
                    "ending_equity": portfolio.equity({}),
                    "fills": [asdict(f) for f in runner.fills],
                }
            )
        metrics = compute_multi_session_metrics(aggregate, cash)
        result["multi_session_metrics"] = asdict(metrics)
        result["metrics"] = asdict(metrics.trade_metrics)
        yearly = []
        starting = cash
        for year, items in groupby(
            aggregate, key=lambda session: session.session_date.year
        ):
            sample = list(items)
            stats = compute_multi_session_metrics(sample, starting)
            yearly.append(
                {
                    "year": year,
                    "first_session": sample[0].session_date.isoformat(),
                    "last_session": sample[-1].session_date.isoformat(),
                    "sessions": len(sample),
                    "starting_equity": starting,
                    "ending_equity": sample[-1].ending_equity,
                    "net_change": stats.trade_metrics.net_change,
                    "completed_trades": stats.trade_metrics.completed_trade_count,
                    "session_end_drawdown": stats.drawdown.max_drawdown,
                    "return_fraction": stats.trade_metrics.net_change / starting
                    if starting > 0
                    else None,
                    "session_end_drawdown_fraction": stats.drawdown.max_drawdown_pct,
                }
            )
            starting = sample[-1].ending_equity
        result["calendar_year_results"] = yearly

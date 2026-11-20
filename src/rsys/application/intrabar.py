"""Explicit stock-trade acquisition and credential-free historical intrabar replay."""

import gzip
import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from html import escape
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
from rsys.data.calendar import (
    session_close_local,
    session_duration_minutes,
    session_open_local,
    session_open_utc,
)
from rsys.data.history import candidate_prior_session_dates
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


def _history_dates(day, lookback):
    # Early closes are comparable in the morning. Obtain enough longer sessions
    # for the last tick too, using only the calendar to select required dates.
    count = 0
    dates = []
    duration = session_duration_minutes(day)
    for candidate in candidate_prior_session_dates(day):
        dates.append(candidate)
        if session_duration_minutes(candidate) >= duration:
            count += 1
            if count == lookback:
                return dates
    raise ValueError("Insufficient calendar history")


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
        "sessions",
        "partitions",
        "purpose",
        "timing_basis",
        "print_policy",
        "scanner",
        "strategy",
        "account",
        "action_review",
    }
    fields(config, keys, keys)
    if (
        type(config["schema_version"]) is not int
        or config["schema_version"] != 1
        or config["timing_basis"] != "final_trades_delayed_ms_v1"
        or config["print_policy"] != "alpaca_minute_conditions_regular_fills_v1"
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
    days = [date.fromisoformat(x) for x in config["sessions"]]
    if (
        not days
        or days != sorted(set(days))
        or config["sessions"] != [d.isoformat() for d in days]
    ):
        raise ValueError("Execution dates must be unique, chronological and canonical")
    prior = {day: _history_dates(day, lookback) for day in days}
    required = sorted(set(days).union(*prior.values()))
    policy = DatasetPolicy(config["partitions"], config["purpose"])
    try:
        for d in required:
            policy.check(d)
    finally:
        log.extend(policy.log)
    requests = {
        d: {s: tape_request(s, d, config["feed"]) for s in symbols} for d in required
    }
    if replay:
        review = config["action_review"]
        if set(review) != set(symbols):
            raise ValueError("Review corporate actions for every symbol")
        for r in review.values():
            fields(
                r,
                {"status", "source", "start", "end"},
                {"status", "source", "start", "end"},
            )
            if (
                r["status"] != "verified_none"
                or not isinstance(r["source"], str)
                or not r["source"].strip()
                or date.fromisoformat(r["start"]) > required[0]
                or date.fromisoformat(r["end"]) < required[-1]
            ):
                raise ValueError(
                    "Action review must cover all execution and history sessions"
                )
        account = config["account"]
        akeys = {
            "initial_cash",
            "completion_policy",
            "risk",
            "sizing",
            "execution",
            "costs",
            "flatten_at",
        }
        fields(account, akeys, akeys)
        validate_account_settings(account)
        if account["completion_policy"] != "require_flat":
            raise ValueError("Trade replay requires flat completion")
        fields(
            config["strategy"],
            {"strategy", "strategy_config"},
            {"strategy", "strategy_config"},
        )
        for d in days:
            opening, closing = (
                ms(session_open_local(d).isoformat()),
                ms(session_close_local(d).isoformat()),
            )
            _strategy(_base_strategy(config), opening, closing)
            if not opening <= ms(account["flatten_at"]) < closing:
                raise ValueError("Flatten time outside session")
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

    def read(day, s):
        return load_tape(config["cache"], requests[day][s], pins[day.isoformat()][s])

    with gzip.open(
        attempt / "events.jsonl.gz", "wt", encoding="utf-8", compresslevel=1
    ) as stream:
        for day in days:
            strategies = {
                s: build_strategy(s, spec["strategy"], spec)
                for s in sorted(config["symbols"])
            }
            history = {
                s: [
                    TapeVolumeHistory(read(d, s), session_duration_minutes(d) * 60000)
                    for d in prior[day]
                ]
                for s in strategies
            }
            settings = config["scanner"]
            scanner = TradeScanner(
                history,
                lookback=settings["lookback_sessions"],
                top_n=settings["top_n"],
                max_age_ms=settings["max_price_age_ms"],
                min_dollar_volume=decimal(settings["min_dollar_volume"]),
            )
            runner = IntrabarSession(
                strategies,
                portfolio,
                sizing,
                execution=ExecutionPolicy(
                    **account["execution"], flatten_at_ms=ms(account["flatten_at"])
                ),
                risk=risk,
                costs=execution_costs(account),
                session_date=day.isoformat(),
                feed=config["feed"],
            )

            session_label = day.isoformat()

            def write(event, session=session_label):
                row = {"session": session, **event}
                stream.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
                if watch:
                    watch(row)

            runner.play(
                [read(day, s) for s in strategies],
                scanner,
                ms(session_open_local(day).isoformat()),
                ms(session_close_local(day).isoformat()),
                write,
            )
            aggregate.append(
                SessionResult(
                    day,
                    int(session_open_utc(day).timestamp()) * 1_000_000_000,
                    [],
                    runner.fills,
                    [],
                    portfolio.equity({}),
                )
            )
            sessions.append(
                {
                    "date": day.isoformat(),
                    "ending_equity": portfolio.equity({}),
                    "fills": [asdict(f) for f in runner.fills],
                }
            )
        metrics = compute_multi_session_metrics(aggregate, cash)
        result["multi_session_metrics"] = asdict(metrics)
        result["metrics"] = asdict(metrics.trade_metrics)

"""Explicit acquisition and network-free historical-bar backtests with retained failures."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from rsys.application.batch import run_batch_cli, write_json
from rsys.application.dataset_policy import DatasetPolicy
from rsys.application.errors import RunConfigError
from rsys.application.session_config import fields, integer
from rsys.data.alpaca import AlpacaBarsClient
from rsys.data.bar_cache import BarCache, request_identity
from rsys.data.calendar import EXCHANGE_TZ, is_trading_day
from rsys.data.history import candidate_prior_session_dates
from rsys.research.diagnostics import (
    DiagnosticWriter,
    invocation_scope,
    protect_evidence_write,
    start_invocation,
)
from rsys.research.errors import DiagnosticWriteError
from rsys.research.experiments import redact_secrets
from rsys.research.models import DiagnosticEventCode


def _source(config):
    spec = config["dataset"]
    fields(spec, {"symbol", "feed", "asof"}, {"symbol", "feed", "asof"})
    return spec


def download(
    config,
    out_dir,
    *,
    client=None,
    refresh=False,
    input_error=None,
    invocation: DiagnosticWriter | None = None,
):
    with invocation_scope(out_dir, command="download", invocation=invocation) as writer:
        return _download(
            config,
            out_dir,
            client=client,
            refresh=refresh,
            input_error=input_error,
            invocation=writer,
        )


def _download(config, out_dir, *, client, refresh, input_error, invocation):
    out_dir = Path(out_dir)
    owns_invocation = invocation is None
    if invocation is None:
        invocation = start_invocation(out_dir, command="download")
    attempt = out_dir / "downloads" / uuid4().hex
    report = {
        "status": "failed",
        "input": redact_secrets(config),
        "datasets": [],
        "access_log": [],
    }
    owns_attempt = False
    try:
        # A collision/permission failure still belongs to this invocation, but
        # never permits replacing evidence in an existing attempt directory.
        attempt.mkdir(parents=True, exist_ok=False)
        owns_attempt = True
        invocation.link_download_attempt(str(attempt / "manifest.json"))
        if input_error is not None:
            raise RunConfigError(input_error)
        required = {
            "schema_version",
            "cache",
            "dataset",
            "start",
            "end",
            "partitions",
            "purpose",
        }
        fields(config, required, required)
        if type(config["schema_version"]) is not int or config["schema_version"] != 1:
            raise ValueError("Unsupported download schema")
        spec = _source(config)
        policy = DatasetPolicy(config["partitions"], config["purpose"])
        report["access_log"] = policy.log
        start, end = (
            date.fromisoformat(config["start"]),
            date.fromisoformat(config["end"]),
        )
        if start > end or (end - start).days > 366:
            raise ValueError("Invalid download date range")
        requests = []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            if is_trading_day(day):
                try:
                    policy.check(day)
                except ValueError:
                    invocation.event(
                        DiagnosticEventCode.DATA_ACCESS_DENIED,
                        invocation.stage,
                        details={"date": day.isoformat()},
                        level="error",
                    )
                    raise
                requests.append(
                    (
                        day,
                        request_identity(
                            spec["symbol"], day, spec["feed"], spec["asof"]
                        ),
                    )
                )
        if not requests:
            raise ValueError("No trading dates requested")
        invocation.event(
            DiagnosticEventCode.DATA_ACCESS_PERMITTED,
            invocation.stage,
            details={"permitted_dates": len(requests), "total_sessions": len(requests)},
        )
        cache = BarCache(config["cache"])
        for day, request in requests:
            invocation.event(
                DiagnosticEventCode.PROGRESS,
                invocation.stage,
                details={
                    "date": day.isoformat(),
                    "total_sessions": len(requests),
                    "completed_sessions": len(report["datasets"]),
                },
            )
            identity = cache.acquire(
                request, client or AlpacaBarsClient(), refresh=refresh
            )
            payload = cache.load(identity, request)
            invocation.event(
                DiagnosticEventCode.CACHE_READ,
                invocation.stage,
                details={
                    "date": day.isoformat(),
                    "coverage_complete": payload["quality"]["coverage_complete"],
                    "completed_sessions": len(report["datasets"]) + 1,
                },
            )
            report["datasets"].append(
                {
                    "date": day.isoformat(),
                    "object_id": identity,
                    "quality": payload["quality"],
                }
            )
        report["status"] = "success"
        invocation.event(
            DiagnosticEventCode.DATASET_COMPLETE,
            invocation.stage,
            details={"dataset_count": len(report["datasets"])},
        )
        return report, attempt / "manifest.json"
    except BaseException as exc:
        report["status"] = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        )
        if owns_attempt:
            exc.manifest_path = attempt / "manifest.json"
        report["error"] = {
            "type": type(exc).__name__,
            "message": invocation.error_message(exc),
        }
        # Reflect the invocation's true current stage, not a hardcoded "preparing"
        # that becomes stale once link_download_attempt() has already resolved it.
        invocation.fail(exc)
        raise
    finally:

        def _finalize_evidence() -> None:
            # The diagnostics finalize step and the manifest.json write are both
            # "evidence writing" in T04's sense; a failure in either must never
            # replace/mask whatever application exception is already propagating
            # through this `finally` block (attached instead, never raised in its
            # place).
            if not owns_attempt:
                return  # A colliding attempt belongs to somebody else.
            if invocation.stage != "failed":
                invocation.set_stage("finalizing")
            # ARTIFACTS_READY must only be journaled once the manifest (the last
            # required artifact) is actually on disk -- never before, or a
            # manifest-write failure would leave a journal falsely claiming
            # readiness for an attempt that was never fully saved.
            # Downloads have no SQLite authority. Publish a non-success state
            # first, then seal diagnostics, then atomically commit the outcome.
            provisional = (
                {**report, "status": "finalizing"}
                if report["status"] == "success"
                else report
            )
            write_json(attempt / "manifest.json", provisional)
            if report["status"] == "success":
                invocation.artifacts_ready(stage=invocation.stage)
            report["diagnostics"] = invocation.seal()
            write_json(attempt / "manifest.json", report)

        try:
            protect_evidence_write(
                _finalize_evidence, catch=(DiagnosticWriteError, OSError)
            )
        except BaseException as write_exc:
            # Before sealing, record this sole write failure in the journal.
            # After sealing, preserve those bytes and update the small state
            # summary instead. The atomic manifest remains the download outcome
            # authority: ARTIFACTS_READY alone never establishes success.
            write_exc.manifest_path = attempt / "manifest.json"
            report["status"] = "failed"
            report["error"] = {
                "type": type(write_exc).__name__,
                "message": invocation.error_message(write_exc),
            }
            invocation.fail(write_exc)
            if owns_attempt:
                protect_evidence_write(
                    lambda: write_json(attempt / "manifest.json", report),
                    catch=(OSError, DiagnosticWriteError),
                )
            raise
        if owns_invocation:
            invocation.close()


def prepare_backtest(config, access_log, invocation: DiagnosticWriter | None = None):
    required = {
        "schema_version",
        "cache",
        "dataset",
        "objects",
        "partitions",
        "purpose",
        "sessions",
        "account",
        "strategy",
        "scanner",
        "action_review",
        "timing_basis",
    }
    fields(config, required, required)
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Unsupported historical backtest schema")
    if config["timing_basis"] != "final_bars_modeled_at_end":
        raise ValueError("Explicit final_bars_modeled_at_end acknowledgement required")
    spec = _source(config)
    policy = DatasetPolicy(config["partitions"], config["purpose"])
    days = [date.fromisoformat(d) for d in config["sessions"]]
    if not days or days != sorted(set(days)):
        raise ValueError("Backtest sessions must be nonempty, unique and ordered")
    scan = config["scanner"]
    scan_keys = {
        "top_n",
        "lookback_sessions",
        "max_lookback_calendar_sessions",
        "min_dollar_volume",
        "max_current_bar_age_ms",
    }
    fields(scan, scan_keys, scan_keys)
    maximum = integer(scan["max_lookback_calendar_sessions"], minimum=1)
    required_days = set(days)
    for day in days:
        required_days.update(
            candidate_prior_session_dates(day, max_lookback_calendar_sessions=maximum)
        )
    try:
        for day in sorted(required_days):
            try:
                policy.check(day)
            except ValueError:
                if invocation is not None:
                    invocation.event(
                        DiagnosticEventCode.DATA_ACCESS_DENIED,
                        "preparing",
                        details={"date": day.isoformat()},
                        level="error",
                    )
                raise
    finally:
        access_log.extend(policy.log)
    if invocation is not None:
        invocation.event(
            DiagnosticEventCode.DATA_ACCESS_PERMITTED,
            "preparing",
            details={"checked_dates": len(required_days)},
        )
    review = config["action_review"]
    fields(
        review,
        {"status", "source", "start", "end"},
        {"status", "source", "start", "end"},
    )
    if (
        review["status"] != "verified_none"
        or not isinstance(review["source"], str)
        or not review["source"].strip()
        or date.fromisoformat(review["start"]) > min(required_days)
        or date.fromisoformat(review["end"]) < max(required_days)
    ):
        raise ValueError(
            "Record a no-corporate-actions review covering execution and warmup dates; split conversion is not implemented"
        )
    if not isinstance(config["objects"], dict) or set(config["objects"]) != {
        d.isoformat() for d in required_days
    }:
        raise ValueError("Pin exactly one cache object per execution/warmup date")
    cache = BarCache(config["cache"])
    # Every date is authorized before the first dataset or alias is opened.
    loaded = {
        day: cache.load(
            config["objects"][day.isoformat()],
            request_identity(spec["symbol"], day, spec["feed"], spec["asof"]),
        )
        for day in sorted(required_days)
    }
    if invocation is not None:
        invocation.event(
            DiagnosticEventCode.CACHE_READ,
            "preparing",
            details={"loaded_dates": len(loaded)},
        )
    account = config["account"]
    keys = {
        "initial_cash",
        "sizing",
        "risk",
        "costs",
        "execution",
        "completion_policy",
        "flatten_at",
    }
    fields(account, keys, keys)
    fields(
        config["strategy"],
        {"strategy", "strategy_config"},
        {"strategy", "strategy_config"},
    )
    result = {k: deepcopy(v) for k, v in account.items() if k != "flatten_at"}
    history = []
    sessions = []
    delay = integer(account["costs"]["feed_delay_ms"])
    for day, dataset in loaded.items():
        bars = []
        for row in dataset["bars"]:
            start = datetime.fromisoformat(row["t"])
            end = start + timedelta(minutes=1)
            local = start.astimezone(EXCHANGE_TZ)
            history.append(
                {
                    "session_date": day.isoformat(),
                    "minute_index": (local.hour * 60 + local.minute) - 570,
                    "available_at": (end + timedelta(milliseconds=delay)).isoformat(),
                    "revision": 0,
                    "source": "alpaca-final-bars:" + config["objects"][day.isoformat()],
                    "status": "TRADED",
                    "close": row["c"],
                    "volume": row["v"],
                }
            )
            bars.append(
                {
                    "bar_start": local.replace(tzinfo=None).isoformat(),
                    "bar_end": end.astimezone(EXCHANGE_TZ)
                    .replace(tzinfo=None)
                    .isoformat(),
                    "available_at": end.astimezone(EXCHANGE_TZ)
                    .replace(tzinfo=None)
                    .isoformat(),
                    **{
                        dest: row[src]
                        for dest, src in (
                            ("open", "o"),
                            ("high", "h"),
                            ("low", "l"),
                            ("close", "c"),
                            ("volume", "v"),
                        )
                    },
                }
            )
        if day in days:
            if not dataset["quality"]["coverage_complete"]:
                raise ValueError(
                    f"Incomplete execution-day coverage: {day}; absent bars are not verified no-trade minutes"
                )
            sessions.append(
                {
                    "date": day.isoformat(),
                    "flatten_at": account["flatten_at"],
                    "symbols": {spec["symbol"]: {**config["strategy"], "bars": bars}},
                }
            )
    result.update(
        schema_version=2,
        evidence_type="historical_bar_estimate",
        input_mode="bar",
        top_n=scan["top_n"],
        sessions=sessions,
        historical_source={
            "request": config,
            "provider": "alpaca",
            "feed": spec["feed"],
            "access_log": access_log,
            "timing_basis": config["timing_basis"],
            "dataset_hashes": config["objects"],
            "promotion_status": "unscored",
        },
        scanner={
            "mode": "minute_history",
            "feed": spec["feed"],
            "unit_basis": "raw",
            "corporate_actions": "none",
            **{k: v for k, v in scan.items() if k != "top_n"},
            "history": {spec["symbol"]: history},
        },
    )
    if invocation is not None:
        invocation.event(
            DiagnosticEventCode.DATASET_COMPLETE,
            "preparing",
            details={"session_count": len(sessions)},
        )
    return result


def backtest(
    config,
    raw,
    out_dir,
    *,
    report=True,
    invocation: DiagnosticWriter | None = None,
):
    out_dir = Path(out_dir)
    with invocation_scope(out_dir, command="backtest", invocation=invocation) as writer:
        access_log = []

        # Shared lifecycle retains loader failures and the original request.
        def prepare(value):
            return prepare_backtest(value, access_log, writer)

        return run_batch_cli(
            config,
            raw,
            out_dir,
            report=report,
            prepare=prepare,
            access_log=access_log,
            invocation=writer,
        )


def load_request(path):
    raw = None
    try:
        path = Path(path).resolve()
        raw = path.read_bytes()
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise TypeError("Expected a JSON object")
        if isinstance(config.get("cache"), str):
            config["cache"] = str((path.parent / config["cache"]).resolve())
        return config, raw
    except Exception as exc:
        exc.input_sha256 = hashlib.sha256(raw).hexdigest() if raw is not None else None
        raise


def retain_input_failure(
    path, out_dir, command, error, invocation: DiagnosticWriter | None = None
):
    config = {
        "input_path": str(path),
        "input_sha256": getattr(error, "input_sha256", None),
        "load_error": type(error).__name__,
    }
    message = "Unable to load request: " + type(error).__name__
    if command == "download":
        return download(config, out_dir, input_error=message, invocation=invocation)

    def fail(_config):
        raise RunConfigError(message)

    return run_batch_cli(
        config,
        b"",
        Path(out_dir),
        report=True,
        prepare=fail,
        access_log=[],
        invocation=invocation,
    )

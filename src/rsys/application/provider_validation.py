"""Explicit, bounded Alpaca sample workflow using existing download/check/backtest services."""

import hashlib
import json
import os
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from pathlib import Path

from rsys.application.file_attempt import file_attempt
from rsys.application.historical import backtest, download, load_request, plan_backtest
from rsys.application.offline import deny_python_network
from rsys.application.preflight import check_data
from rsys.application.session_config import fields, integer
from rsys.data.alpaca import AlpacaBarsClient
from rsys.data.bar_cache import BarCache
from rsys.reporting.provider import provider_html
from rsys.research.diagnostics import atomic_write_json, protect_evidence_write
from rsys.research.experiments import redact_secrets
from rsys.research.models import DiagnosticEventCode

SOURCE_CONTRACT = {
    "reviewed_on": "2026-11-03",
    "urls": [
        "https://docs.alpaca.markets/us/reference/stockbars",
        "https://docs.alpaca.markets/us/docs/historical-stock-data-1",
    ],
    "scope": "Dated documentation reference; not a fresh entitlement, arrival-history or action audit",
}


@contextmanager
def _check(result, name):
    """Attempted checks must not remain indistinguishable from unrun checks."""
    try:
        yield
    except BaseException:
        result["checklist"][name]["status"] = "failed"
        raise


def _load(path, writer):
    writer.record_identity(input_path=str(path))
    try:
        config, raw = load_request(path)
    except Exception as exc:
        protect_evidence_write(
            lambda error=exc: writer.record_identity(
                input_path=str(path), input_sha256=getattr(error, "input_sha256", None)
            )
        )
        raise
    writer.record_identity(
        input_path=str(path), input_sha256=hashlib.sha256(raw).hexdigest()
    )
    return config


def sample_plan(request, path, access_log, writer, evidence):
    keys = {
        "schema_version",
        "backtest_config",
        "sample_dates",
        "max_sessions",
        "adjustment",
    }
    fields(request, keys, keys)
    if (
        type(request["schema_version"]) is not int
        or request["schema_version"] != 1
        or request["adjustment"] != "raw"
    ):
        raise ValueError("Sample schema 1 and explicit raw adjustment are required")
    maximum = integer(request["max_sessions"], minimum=1)
    days = request["sample_dates"]
    if not isinstance(days, list) or not days or len(days) > maximum:
        raise ValueError("Declare a nonempty sample_dates list within max_sessions")
    parsed = [date.fromisoformat(day) for day in days]
    if [str(day) for day in parsed] != days or parsed != sorted(set(parsed)):
        raise ValueError("Sample dates must be canonical, unique and ordered")
    if (
        not isinstance(request["backtest_config"], str)
        or not request["backtest_config"]
    ):
        raise ValueError("Declare a local backtest_config file")
    config_path = (Path(path).resolve().parent / request["backtest_config"]).resolve()
    identity = {"path": str(config_path), "sha256": None}
    evidence["backtest_template"] = identity
    try:
        config, raw = load_request(config_path)
    except Exception as exc:
        identity["sha256"] = getattr(exc, "input_sha256", None)
        raise
    writer.use_config({"request": request, "backtest": config})
    identity.update(
        sha256=hashlib.sha256(raw).hexdigest(),
        config=redact_secrets(deepcopy(config)),
    )
    if config.get("purpose") != "development":
        raise ValueError("Provider validation accepts development samples only")
    if config.get("objects") != {}:
        raise ValueError(
            "Sample template must use empty objects; selected pins are retained separately"
        )
    config["objects"] = {day: "0" * 64 for day in days}
    # Exact pin set must equal execution plus the FULL declared lookback budget.
    # This authorizes everything before cache, client, environment or HTTP access.
    plan = plan_backtest(config, access_log, writer)
    return config, plan


def validate_sample(
    path, out_dir, *, enable_live=False, refresh=False, transport=None, sleep=time.sleep
):
    result = {
        "schema_version": 1,
        "status": "failed",
        "real_provider_gate": "not_run",
        "evidence_type": "synthetic_provider_validation"
        if transport is not None
        else "provider_validation",
        "source_contract": SOURCE_CONTRACT,
        "access_log": [],
        "dates": [],
        "checklist": {
            name: {"status": "not_run"}
            for name in (
                "request_scope",
                "acquisition",
                "live_feed_access",
                "coverage",
                "response_fields",
                "cache_reuse",
                "offline_backtest",
                "corporate_action_audit",
                "arrival_history",
                "operator_review",
            )
        },
        "provider_stats": {},
        "discrepancies": [],
        "research_status": "unscored",
    }
    with file_attempt(
        out_dir,
        result,
        command="validate-alpaca",
        directory="provider-validations",
        link_method="link_provider_validation_attempt",
        report_name="validation.json",
        renderer=provider_html,
        write_json=atomic_write_json,
    ) as (writer, attempt):
        with _check(result, "request_scope"):
            request = _load(path, writer)
            writer.use_config(request)
            result["input"] = redact_secrets(request)
            config, plan = sample_plan(
                request, path, result["access_log"], writer, result
            )
        result["checklist"]["request_scope"] = {
            "status": "verified",
            "dates": request["sample_dates"],
            "dataset": config["dataset"],
            "adjustment": "raw",
        }
        if transport is None and (
            not enable_live or os.environ.get("RSYS_ENABLE_ALPACA_VALIDATION") != "1"
        ):
            raise ValueError(
                "Live sample disabled: require --enable-live AND RSYS_ENABLE_ALPACA_VALIDATION=1; credentials alone never enable it"
            )
        client = AlpacaBarsClient(
            transport=transport,
            sleep=sleep,
            observer=lambda details: writer.event(
                DiagnosticEventCode.PROGRESS, writer.stage, details=details
            ),
        )
        result["provider_stats"] = client.stats
        config = deepcopy(config)
        result["dates"] = [
            {"date": str(day), "status": "not_run"} for day in plan.requests
        ]
        for row in result["dates"]:
            day = row["date"]
            row["status"] = "running"
            acquisition = {
                k: config[k]
                for k in ("schema_version", "cache", "dataset", "partitions", "purpose")
            }
            acquisition.update(start=day, end=day)
            before = client.stats["request_count"]
            try:
                acquired, manifest = download(
                    acquisition, out_dir, client=client, refresh=refresh
                )
                dataset = acquired["datasets"][0]
                config["objects"][day] = dataset["object_id"]
                row.update(
                    status="success",
                    object_id=dataset["object_id"],
                    quality=dataset["quality"],
                    download_manifest=str(manifest),
                    request_count=client.stats["request_count"] - before,
                )
            except BaseException as exc:
                row.update(
                    status="interrupted"
                    if isinstance(exc, KeyboardInterrupt)
                    else "failed",
                    download_manifest=str(getattr(exc, "manifest_path", "")),
                    error_type=type(exc).__name__,
                    request_count=client.stats["request_count"] - before,
                )
                result["checklist"]["acquisition"] = {"status": "failed", "date": day}
                if transport is None and client.stats["request_count"]:
                    result["checklist"]["live_feed_access"] = {
                        "status": "failed",
                        "basis": "sample request did not complete",
                    }
                    result["real_provider_gate"] = "failed"
                raise
        result["checklist"]["acquisition"] = {
            "status": "verified",
            "object_ids": dict(config["objects"]),
        }
        if transport is None and client.stats["request_count"]:
            result["checklist"]["live_feed_access"] = {
                "status": "verified",
                "scope": "only this request/feed/date sample",
            }
            result["real_provider_gate"] = "pending_manual_review"
        atomic_write_json(attempt / "backtest.json", config)
        with _check(result, "coverage"):
            checked, checked_manifest = check_data(config, out_dir)
        result["preflight"] = {
            "manifest": str(checked_manifest),
            "can_execute": checked["can_execute"],
            "history_coverage_complete": checked["history_coverage_complete"],
            "warnings": checked["warnings"],
        }
        result["checklist"]["coverage"] = {
            "status": "verified"
            if checked["can_execute"] and checked["history_coverage_complete"]
            else "failed",
            "basis": "pinned data preflight; missing minutes are not zero",
        }
        result["discrepancies"] = checked["errors"] + checked["warnings"]
        if not checked["can_execute"]:
            raise ValueError(
                "Sample preflight blocked; inspect retained coverage/errors"
            )
        with _check(result, "response_fields"):
            cache = BarCache(config["cache"])
            for row in result["dates"]:
                payload = cache.load(
                    row["object_id"], plan.requests[date.fromisoformat(row["date"])]
                )
                row["observed"] = {
                    "first_bar": payload["bars"][0] if payload["bars"] else None,
                    "last_bar": payload["bars"][-1] if payload["bars"] else None,
                    "bars": len(payload["bars"]),
                    "timing_basis": payload["timing_basis"],
                }
            result["checklist"]["response_fields"] = {
                "status": "verified",
                "basis": "raw pages normalized/checked against exact UTC minute and numeric contracts; first/last bars retained",
            }

        def no_provider_request(_params):
            raise RuntimeError(
                "Cache reuse attempted provider access; original pins remain retained"
            )

        with _check(result, "cache_reuse"):
            cache_only = AlpacaBarsClient(transport=no_provider_request, sleep=sleep)
            before = client.stats["request_count"]
            for row in result["dates"]:
                acquisition = {
                    k: config[k]
                    for k in (
                        "schema_version",
                        "cache",
                        "dataset",
                        "partitions",
                        "purpose",
                    )
                }
                acquisition.update(start=row["date"], end=row["date"])
                repeated, manifest = download(acquisition, out_dir, client=cache_only)
                row["reuse_manifest"] = str(manifest)
                if (
                    repeated["datasets"][0]["object_id"] != row["object_id"]
                    or client.stats["request_count"] != before
                ):
                    raise ValueError(
                        "Cache reuse changed pins or requested provider data"
                    )
            result["checklist"]["cache_reuse"] = {
                "status": "verified",
                "additional_requests": 0,
            }

        try:
            with deny_python_network():
                _, run_id, trace, manifest = backtest(
                    config, json.dumps(config).encode(), Path(out_dir), report=True
                )
        except BaseException as exc:
            result["checklist"]["offline_backtest"] = {
                "status": "failed",
                "run_id": getattr(exc, "run_id", None),
                "manifest": str(getattr(exc, "manifest_path", "")),
            }
            raise
        result["checklist"]["offline_backtest"] = {
            "status": "verified",
            "run_id": run_id,
            "manifest": str(manifest),
            "trace": str(trace),
            "network_guard": "Python socket/DNS tripwire",
        }
        result["checklist"]["corporate_action_audit"]["basis"] = {
            "attestation_only": config["action_review"]
        }
        result["checklist"]["arrival_history"]["basis"] = (
            "Final historical bars with declared modeled timing; actual arrivals/revisions were not recorded"
        )
        result["checklist"]["operator_review"]["basis"] = (
            "Owner must review data, corporate actions, source-contract date and a manually reconciled trade before accepting the real-provider gate"
        )
        result["status"] = "success"
    return result, attempt / "manifest.json"

"""Local pinned-data eligibility checks. No strategy, acquisition or credentials."""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

from rsys.application.file_attempt import file_attempt
from rsys.application.historical import (
    compose_backtest,
    load_request,
    open_backtest_cache,
    plan_backtest,
)
from rsys.data.calendar import session_duration_minutes
from rsys.data.history import candidate_prior_session_dates
from rsys.reporting.preflight import preflight_html
from rsys.research.diagnostics import (
    atomic_write_json,
    protect_evidence_write,
)
from rsys.research.experiments import redact_secrets
from rsys.research.models import DiagnosticEventCode


def _intervals(minutes):
    """Consecutive missing UTC minutes as half-open intervals."""
    result = []
    for value in minutes:
        end = (datetime.fromisoformat(value) + timedelta(minutes=1)).isoformat()
        if result and result[-1]["end_exclusive"] == value:
            result[-1].update(end_exclusive=end, minutes=result[-1]["minutes"] + 1)
        else:
            result.append({"start": value, "end_exclusive": end, "minutes": 1})
    return result


def inspect_data(config, result, invocation):
    """Populate partial evidence even when one object fails validation."""
    plan = plan_backtest(config, result["access_log"], invocation)
    plan, cache = open_backtest_cache(plan)
    loaded = {}
    for day, request in plan.requests.items():
        identity = config["objects"][day.isoformat()]
        role = (
            "both"
            if day in plan.execution_dates and day in plan.warmup_dates
            else "execution"
            if day in plan.execution_dates
            else "warmup"
        )
        row = {
            "date": day.isoformat(),
            "role": role,
            "object_id": identity,
            "provider": "alpaca",
            "feed": request["feed"],
            "asof": request["asof"],
            "adjustment": request["adjustment"],
            "timing_basis": "final_historical_bars_not_recorded_arrivals",
            "expected_minutes": session_duration_minutes(day),
            "observed_minutes": None,
            "missing_intervals": None,
            "duplicate_rows": None,
            "zero_volume_minutes": None,
            "object_bytes": None,
            "coverage_status": "unverified",
        }
        result["datasets"].append(row)
        try:
            dataset = cache.load(identity, request)
            size = cache.object_size(identity, request)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            row["error"] = invocation.error_message(exc)
            result["errors"].append(f"{day}: {type(exc).__name__}: {row['error']}")
            continue
        loaded[day] = dataset
        quality = dataset["quality"]
        row.update(
            observed_minutes=len(dataset["bars"]),
            missing_intervals=_intervals(quality["missing_minutes"]),
            duplicate_rows=quality["duplicate_rows"],
            zero_volume_minutes=sum(Decimal(b["v"]) == 0 for b in dataset["bars"]),
            object_bytes=size,
            coverage_status="complete" if quality["coverage_complete"] else "gaps",
        )
        if not quality["coverage_complete"]:
            message = f"{day}: {len(quality['missing_minutes'])} absent minutes; absence is not verified zero volume"
            result["errors" if day in plan.execution_dates else "warnings"].append(
                message
            )
        invocation.event(
            DiagnosticEventCode.CACHE_READ,
            invocation.stage,
            details={
                "date": str(day),
                "coverage_complete": quality["coverage_complete"],
            },
        )

    rows = result["datasets"]
    result["history_coverage_complete"] = all(
        r["coverage_status"] == "complete" for r in rows if r["role"] != "execution"
    )
    scan = config["scanner"]
    # Calendar comparability is distinct from complete observations and a nonzero
    # denominator. Do not substitute older sessions for gaps in selected history.
    for day in plan.execution_dates:
        candidates = candidate_prior_session_dates(
            day, max_lookback_calendar_sessions=scan["max_lookback_calendar_sessions"]
        )
        comparable = [
            d
            for d in candidates
            if session_duration_minutes(d) >= session_duration_minutes(day)
        ]
        result["history_windows"].append(
            {
                "date": str(day),
                "requested_sessions": scan["lookback_sessions"],
                "full_session_comparable_candidates": len(comparable),
            }
        )
        if len(comparable) < scan["lookback_sessions"]:
            result["warnings"].append(
                f"{day}: insufficient comparable history at session end; earlier minutes may be usable"
            )
    result["inventory"] = {
        "execution_sessions": len(plan.execution_dates),
        "required_objects": len(rows),
        "verified_objects": len(loaded),
        "verified_bars": sum(r["observed_minutes"] or 0 for r in rows),
        "verified_object_bytes": sum(r["object_bytes"] or 0 for r in rows),
        "byte_scope": "verified pinned objects only; excludes aliases and unrelated cache objects",
        "prepared_input_json_bytes": None,
        "peak_memory_bytes": None,
        "backtest_evidence_bytes": None,
        "resource_note": "Cache bytes are not peak RAM or output size. Run scripts/benchmark_backtest_evidence.py for measured synthetic evidence costs.",
    }
    if not result["errors"]:
        resolved = compose_backtest(plan, loaded, result["access_log"])
        # Measure a declared serialization without allocating a second full buffer.
        result["inventory"]["prepared_input_json_bytes"] = sum(
            len(chunk.encode("utf-8"))
            for chunk in json.JSONEncoder(
                sort_keys=True, separators=(",", ":"), default=str
            ).iterencode(resolved)
        )
        result["can_execute"] = True
    result["check_completed"] = True


def check_data(config, out_dir, *, invocation=None, input_path=None):
    """Validate local data and retain a file-only authoritative check outcome."""
    result = {
        "schema_version": 1,
        "status": "failed",
        "can_execute": False,
        "check_completed": False,
        "history_coverage_complete": None,
        "research_status": "unscored",
        "datasets": [],
        "access_log": [],
        "errors": [],
        "warnings": [],
        "history_windows": [],
        "inventory": {},
        "limits": "Eligibility does not prove profitable signals, nonzero RVOL, provider entitlement or corporate-action correctness. The action review remains a caller attestation.",
    }
    with file_attempt(
        out_dir,
        result,
        command="check-data",
        directory="preflights",
        link_method="link_preflight_attempt",
        report_name="preflight.json",
        renderer=preflight_html,
        write_json=atomic_write_json,
        invocation=invocation,
    ) as (writer, attempt):
        if input_path is not None:
            writer.record_identity(input_path=str(input_path))
            try:
                config, raw = load_request(input_path)
            except Exception as exc:
                protect_evidence_write(
                    lambda error=exc: writer.record_identity(
                        input_path=str(input_path),
                        input_sha256=getattr(error, "input_sha256", None),
                    )
                )
                raise
            writer.record_identity(
                input_path=str(input_path), input_sha256=hashlib.sha256(raw).hexdigest()
            )
        writer.use_config(config)
        result["input"] = redact_secrets(config)
        inspect_data(config, result, writer)
        if result["can_execute"]:
            result["status"] = "success"
        else:
            writer.fail(
                ValueError("Data preflight blocked; inspect preflight.json errors")
            )
    return result, attempt / "manifest.json"

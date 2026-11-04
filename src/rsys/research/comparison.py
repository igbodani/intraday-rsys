"""Compare sealed successful session evidence, rejecting unexplained differences."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from rsys.research.diagnostics import verify_diagnostic_seal
from rsys.research.errors import (
    CompareCliError,
    DiagnosticIntegrityError,
    RunNotFoundError,
)
from rsys.research.evaluation import compare_runs
from rsys.research.models import (
    HISTORICAL_SCANNER_PROFILE_VERSION,
    SyntheticRunAssumptions,
)
from rsys.research.storage import RunStorage, _validate_run_id
from rsys.research.trace import file_sha256, verify_streamed_artifacts


def load_run_manifest(storage_root: Path, run_id: str) -> dict[str, Any]:
    try:
        _validate_run_id(run_id)
    except ValueError as exc:
        raise CompareCliError("invalid run id") from exc
    attempt = storage_root / "attempts" / run_id
    try:
        raw = (attempt / "manifest.json").read_bytes()
        manifest = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise CompareCliError(
            f"no retained manifest for run {run_id!r} under {storage_root}"
        ) from exc
    if (
        manifest.get("schema_version") not in (2, 3)
        or manifest.get("status") != "success"
    ):
        raise CompareCliError(
            "comparison requires a successful version-2/3 session manifest; rerun legacy input"
        )
    storage = RunStorage(storage_root)
    try:
        record = storage.load_record(run_id)
    except RunNotFoundError as exc:
        raise CompareCliError("manifest has no authoritative run record") from exc
    finally:
        storage.close()
    if (
        record.phase != "run"
        or record.status != "success"
        or manifest.get("run_id") != run_id
    ):
        raise CompareCliError("run is not a sealed successful synthetic session")
    if (
        record.decision_gate_status.get("manifest_sha256")
        != hashlib.sha256(raw).hexdigest()
    ):
        raise CompareCliError("manifest integrity mismatch")
    if "diagnostics_version" in manifest:
        try:
            if manifest["diagnostics_version"] != 1:
                raise DiagnosticIntegrityError("unsupported diagnostic evidence")
            verify_diagnostic_seal(
                storage_root,
                json.loads(record.decision_gate_status.get("diagnostics", "{}")),
                run_id=run_id,
            )
        except (DiagnosticIntegrityError, ValueError, TypeError) as exc:
            raise CompareCliError(str(exc)) from exc
    try:
        trace_hash = file_sha256(attempt / "trace.jsonl")
    except OSError as exc:
        raise CompareCliError("trace is missing") from exc
    if trace_hash != manifest.get("semantic_trace_hash"):
        raise CompareCliError("trace integrity mismatch")
    if "trace_version" in manifest:
        try:
            if manifest.get("trace_complete") is not True:
                raise DiagnosticIntegrityError("incomplete streamed trace")
            verify_streamed_artifacts(attempt, manifest)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CompareCliError(
                f"streamed evidence integrity failure: {exc}"
            ) from exc
    if manifest["schema_version"] == 3:
        hashes = manifest.get("artifact_hashes", {})
        required = {
            "input.json",
            "resolved_config.json",
            "fills.json",
            "metrics.json",
            "trace.jsonl",
        }
        required.update(f"sessions/{day}.json" for day in manifest["session_dates"])
        if manifest.get("scanner_basis") in (
            "synthetic_minute_history_v1",
            "historical_final_bar_features_v1",
        ):
            required.add("scanner_history.json")
        if manifest.get("evidence_type") == "historical_bar_estimate":
            required.add("data_access.json")
        if (
            not required <= hashes.keys()
            or manifest.get("metrics_scope") != "complete_attempt"
        ):
            raise CompareCliError("incomplete multi-session evidence")
        for name, expected in hashes.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise CompareCliError("invalid artifact path")
            try:
                actual = file_sha256(attempt / name)
            except OSError as exc:
                raise CompareCliError("retained artifact is missing") from exc
            if actual != expected:
                raise CompareCliError("retained artifact integrity mismatch")
    return manifest


def _historical_provenance(source: Any) -> Any:
    """Retain source identity without counting declared experiment settings twice.

    The expanded manifest already owns strategy, scanner, capital, risk, costs,
    execution and completion settings. The original request remains fully retained;
    only its comparison projection removes those duplicates. Unknown fields/schema
    versions remain protected, as do datasets, partitions and action-review evidence.
    """
    source = deepcopy(source)
    if not isinstance(source, dict):
        return source
    request = source.get("request")
    if not isinstance(request, dict) or request.get("schema_version") != 1:
        return source

    def remove_known(mapping, names):
        if isinstance(mapping, dict):
            for name in names:
                mapping.pop(name, None)

    remove_known(request.get("strategy"), ("strategy", "strategy_config"))
    remove_known(
        request.get("scanner"),
        (
            "top_n",
            "lookback_sessions",
            "max_lookback_calendar_sessions",
            "min_dollar_volume",
            "max_current_bar_age_ms",
        ),
    )
    account = request.get("account")
    if isinstance(account, dict):
        remove_known(account, ("initial_cash", "flatten_at", "completion_policy"))
        remove_known(account.get("sizing"), ("quantity_step", "max_quantity"))
        remove_known(
            account.get("risk"),
            (
                "per_trade_fraction",
                "daily_loss_fraction",
                "maximum_drawdown_fraction",
                "daily_loss_basis",
                "halt_action",
            ),
        )
        remove_known(
            account.get("costs"),
            (
                "adverse_bps",
                "feed_delay_ms",
                "entry_fee",
                "exit_fee",
            ),
        )
        remove_known(account.get("execution"), ("decision_latency_ms", "max_hold_ms"))
    return source


def assumptions_from_manifest(manifest: dict[str, Any]) -> SyntheticRunAssumptions:
    sizing = manifest["sizing"]
    encode = lambda v: json.dumps(v, sort_keys=True)
    scanner_identity = manifest["scanner_hash"]
    profile_version = manifest.get("scanner_profile_version")
    profile_hash = manifest.get("scanner_profile_hash")
    if profile_version is not None or profile_hash is not None:
        if (
            profile_version != HISTORICAL_SCANNER_PROFILE_VERSION
            or manifest.get("evidence_type") != "historical_bar_estimate"
            or not isinstance(profile_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", profile_hash)
        ):
            raise CompareCliError(
                "unsupported or incomplete scanner comparison profile"
            )
        scanner_identity = [profile_version, profile_hash]
    return SyntheticRunAssumptions(
        universe=tuple(manifest["universe"]),
        cost_profile=encode(
            {
                **{k: sizing[k] for k in ("entry_fee", "exit_fee", "quantity_step")},
                **manifest.get("costs", {}),
            }
        ),
        risk_profile=encode(
            {
                **{k: sizing[k] for k in ("risk_budget", "max_quantity")},
                **manifest.get("risk", {}),
            }
        ),
        execution_mode=encode(manifest["execution"]),
        initial_cash=str(Decimal(manifest["initial_cash"]).normalize()),
        session_date=manifest.get(
            "session_date", encode(manifest.get("session_dates"))
        ),
        market_data_hash=manifest["market_data_hash"],
        scanner_profile=encode([scanner_identity, manifest["top_n"]]),
        strategies=encode(manifest["strategies"]),
        code_hash=manifest["code_hash"],
        dependency_lock_hash=manifest["dependency_lock_hash"],
        evidence_basis=encode(
            [
                manifest.get("completion_policy"),
                manifest.get("input_mode"),
                _historical_provenance(manifest.get("historical_source")),
                *[
                    manifest[k]
                    for k in (
                        "evidence_type",
                        "calendar_version",
                        "execution_basis",
                        "scanner_basis",
                        "account_basis",
                    )
                ],
            ]
        ),
    )


def compare_run_pair(
    storage_root: Path,
    run_id_a: str,
    run_id_b: str,
    *,
    permitted_difference: str | None = None,
) -> dict[str, Any]:
    a, b = (
        load_run_manifest(storage_root, run_id_a),
        load_run_manifest(storage_root, run_id_b),
    )
    diffs = compare_runs(
        assumptions_from_manifest(a),
        assumptions_from_manifest(b),
        permitted_difference=permitted_difference,
    )
    return {
        "run_a": run_id_a,
        "run_b": run_id_b,
        "assumption_diffs": diffs,
        "metrics_a": a["metrics"],
        "metrics_b": b["metrics"],
        "ending_equity_a": a["ending_equity"],
        "ending_equity_b": b["ending_equity"],
        "ending_equity_delta": str(
            Decimal(b["ending_equity"]) - Decimal(a["ending_equity"])
        ),
    }

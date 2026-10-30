"""Compare sealed successful synthetic-session evidence, rejecting unexplained differences."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from rsys.evaluation import compare_runs
from rsys.storage import RunNotFoundError, RunStorage, _validate_run_id


class CompareCliError(Exception):
    """Missing, incomplete, legacy or corrupted run evidence."""


@dataclass(frozen=True)
class SyntheticRunAssumptions:
    universe: tuple[str, ...]
    cost_profile: str
    risk_profile: str
    execution_mode: str
    initial_cash: str
    session_date: str
    market_data_hash: str
    scanner_profile: str
    strategies: str
    code_hash: str
    dependency_lock_hash: str
    evidence_basis: str


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
    if manifest.get("schema_version") != 2 or manifest.get("status") != "success":
        raise CompareCliError(
            "comparison requires a successful version-2 session manifest; rerun legacy input"
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
    try:
        trace_hash = hashlib.sha256((attempt / "trace.jsonl").read_bytes()).hexdigest()
    except OSError as exc:
        raise CompareCliError("trace is missing") from exc
    if trace_hash != manifest.get("semantic_trace_hash"):
        raise CompareCliError("trace integrity mismatch")
    return manifest


def assumptions_from_manifest(manifest: dict[str, Any]) -> SyntheticRunAssumptions:
    sizing = manifest["sizing"]
    encode = lambda v: json.dumps(v, sort_keys=True)
    return SyntheticRunAssumptions(
        universe=tuple(manifest["universe"]),
        cost_profile=encode(
            {k: sizing[k] for k in ("entry_fee", "exit_fee", "quantity_step")}
        ),
        risk_profile=encode({k: sizing[k] for k in ("risk_budget", "max_quantity")}),
        execution_mode=encode(manifest["execution"]),
        initial_cash=str(Decimal(manifest["initial_cash"]).normalize()),
        session_date=manifest["session_date"],
        market_data_hash=manifest["market_data_hash"],
        scanner_profile=encode([manifest["scanner_hash"], manifest["top_n"]]),
        strategies=encode(manifest["strategies"]),
        code_hash=manifest["code_hash"],
        dependency_lock_hash=manifest["dependency_lock_hash"],
        evidence_basis=encode(
            [
                manifest[k]
                for k in (
                    "evidence_type",
                    "calendar_version",
                    "execution_basis",
                    "scanner_basis",
                    "account_basis",
                )
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

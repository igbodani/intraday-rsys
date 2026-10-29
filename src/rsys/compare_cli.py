"""`rsys compare` orchestration glue: loads two retained `rsys run` manifests and rejects an
unexplained assumption difference before reporting the metrics delta
(docs/M2_IMPLEMENTATION_HANDOFF.md step 9, comparison subset).

Honesty note: compares only `StandaloneRunner` synthetic-session manifests written by
`run_cli.py`; it does not resolve run IDs across arbitrary storage backends, does not know
about `rsys replay` attempts, and does not render an HTML report -- see
docs/M2_IMPLEMENTATION_STATUS.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from rsys.evaluation import compare_runs


class CompareCliError(Exception):
    """A requested run ID has no retained manifest under the given storage root."""


@dataclass(frozen=True)
class SyntheticRunAssumptions:
    """The subset of `run_cli.py`'s synthetic-session schema that must match before a
    diagnostic comparison is meaningful."""

    universe: tuple[str, ...]
    cost_profile: str
    risk_profile: str
    execution_mode: str


def load_run_manifest(storage_root: Path, run_id: str) -> dict[str, Any]:
    manifest_path = storage_root / "attempts" / run_id / "manifest.json"
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CompareCliError(f"no retained manifest for run {run_id!r} under {storage_root}") from exc
    except json.JSONDecodeError as exc:
        raise CompareCliError(f"manifest for run {run_id!r} is not valid JSON: {exc}") from exc


def assumptions_from_manifest(manifest: dict[str, Any]) -> SyntheticRunAssumptions:
    sizing = manifest.get("sizing", {})
    execution = manifest.get("execution", {})
    cost_profile = json.dumps(
        {k: sizing.get(k, "0") for k in ("entry_fee", "exit_fee", "quantity_step")}, sort_keys=True,
    )
    risk_profile = json.dumps(
        {k: sizing.get(k) for k in ("risk_budget", "max_quantity")}, sort_keys=True,
    )
    execution_mode = json.dumps(execution, sort_keys=True)
    return SyntheticRunAssumptions(
        universe=tuple(manifest.get("universe", [])), cost_profile=cost_profile,
        risk_profile=risk_profile, execution_mode=execution_mode,
    )


def compare_run_pair(
    storage_root: Path, run_id_a: str, run_id_b: str, *, permitted_difference: str | None = None,
) -> dict[str, Any]:
    """Raises `CompareCliError` for a missing manifest or `ComparisonError` (re-raised,
    unchanged) for an unexplained assumption difference."""
    manifest_a = load_run_manifest(storage_root, run_id_a)
    manifest_b = load_run_manifest(storage_root, run_id_b)
    diffs = compare_runs(
        assumptions_from_manifest(manifest_a), assumptions_from_manifest(manifest_b),
        permitted_difference=permitted_difference,
    )
    return {
        "run_a": run_id_a, "run_b": run_id_b, "assumption_diffs": diffs,
        "metrics_a": manifest_a["metrics"], "metrics_b": manifest_b["metrics"],
        "ending_equity_a": manifest_a["ending_equity"], "ending_equity_b": manifest_b["ending_equity"],
        "ending_equity_delta": str(Decimal(manifest_b["ending_equity"]) - Decimal(manifest_a["ending_equity"])),
    }

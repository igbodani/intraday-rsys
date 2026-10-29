"""`rsys fixture` orchestration glue: loads fixtures/cases.json, runs each requested case
through FixtureEngine, retains one evidence attempt per invocation (and one run record per
case) via the existing experiments/storage lifecycle, and reports a non-zero exit code if
any case's declared checkpoints/final-state fields do not match.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from rsys.experiments import run_context, start_run
from rsys.fixture_engine import compare_case, run_case
from rsys.storage import RunStorage

_CASES_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "cases.json"


class FixtureCliError(Exception):
    """Invalid `--case` selector or unreadable fixture file."""


def _load_cases() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FixtureCliError(f"cannot read fixture cases file: {_CASES_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise FixtureCliError(f"fixture cases file is not valid JSON: {exc}") from exc
    return {c["id"]: c for c in data["cases"]}


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


class FixtureCaseOutcome:
    def __init__(self, case_id: str, problems: list[str]):
        self.case_id = case_id
        self.problems = problems

    @property
    def ok(self) -> bool:
        return not self.problems


def run_fixture_cli(case_selector: str, out_dir: Path) -> tuple[list[FixtureCaseOutcome], Path, Path]:
    """Runs the selected case(s), writes a retained trace/manifest under `out_dir`, and
    returns (outcomes, trace_path, manifest_path). Raises FixtureCliError for an unknown
    case id or unreadable fixture file."""
    cases = _load_cases()
    if case_selector == "all":
        selected = [cases[cid] for cid in sorted(cases)]
    elif case_selector in cases:
        selected = [cases[case_selector]]
    else:
        raise FixtureCliError(f"unknown fixture case: {case_selector!r}")

    storage = RunStorage(out_dir)
    try:
        record = start_run(
            storage, hypothesis="Offline synthetic account/execution fixture check (M2-01).",
            phase="fixture", resolved_config={"case_selector": case_selector},
            data_identity=f"sha256:{_CASES_PATH.stat().st_mtime_ns}",
            repo_root=Path(__file__).resolve().parents[2],
        )
        attempt_dir = out_dir / "attempts" / record.run_id
        attempt_dir.mkdir(parents=True, exist_ok=False)
        trace_path = attempt_dir / "trace.jsonl"
        manifest_path = attempt_dir / "manifest.json"
        outcomes: list[FixtureCaseOutcome] = []
        trace_records: list[dict[str, Any]] = []
        try:
            with run_context(storage, record):
                for case in selected:
                    result = run_case(case)
                    problems = compare_case(case, result)
                    outcomes.append(FixtureCaseOutcome(case["id"], problems))
                    trace_records.append({
                        "case_id": case["id"], "ok": not problems, "problems": problems,
                        "checkpoints": [asdict(cp) for cp in result.checkpoints],
                        "rejection": result.rejection, "final_cash": result.final_cash,
                        "final_equity": result.final_equity, "final_quantity": result.final_quantity,
                        "positions": result.positions,
                    })
                if any(not o.ok for o in outcomes):
                    raise _FixtureMismatch(f"{sum(1 for o in outcomes if not o.ok)} of {len(outcomes)} case(s) mismatched")
        except _FixtureMismatch:
            pass
        finally:
            with trace_path.open("w", encoding="utf-8") as handle:
                for row in trace_records:
                    handle.write(json.dumps(row, sort_keys=True, default=_json_default))
                    handle.write("\n")
            _write_json(manifest_path, {
                "schema_version": 1, "run_id": record.run_id, "case_selector": case_selector,
                "cases": [o.case_id for o in outcomes], "all_ok": all(o.ok for o in outcomes),
                "config_hash": record.config_hash, "code_hash": record.code_hash,
            })
            record.artifacts.extend([str(trace_path), str(manifest_path)])
        return outcomes, trace_path, manifest_path
    finally:
        storage.close()


class _FixtureMismatch(Exception):
    """Internal-only: makes `run_context` finalize this attempt as `failed` with a truthful,
    retained reason when one or more fixture checkpoints did not match."""

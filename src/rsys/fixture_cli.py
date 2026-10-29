"""`rsys fixture` orchestration glue: loads fixtures/cases.json, runs each requested case
through FixtureEngine, retains one evidence attempt per invocation (one run record for the selected cases) via the existing experiments/storage lifecycle, and reports a non-zero exit code if
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
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )


class FixtureCaseOutcome:
    def __init__(self, case_id: str, problems: list[str]):
        self.case_id = case_id
        self.problems = problems

    @property
    def ok(self) -> bool:
        return not self.problems


def run_fixture_cli(
    case_selector: str, out_dir: Path
) -> tuple[list[FixtureCaseOutcome], Path, Path]:
    """Runs the selected case(s), writes a retained trace/manifest under `out_dir`, and
    returns (outcomes, trace_path, manifest_path). Raises FixtureCliError for an unknown
    case id or unreadable fixture file."""
    import hashlib

    raw, input_error = None, None
    try:
        raw = _CASES_PATH.read_bytes()
    except OSError:
        input_error = FixtureCliError("cannot read fixture cases file")
    storage = RunStorage(out_dir)
    try:
        provenance = (
            {"data_identity": "sha256:" + hashlib.sha256(raw).hexdigest()}
            if raw is not None
            else {"data_unavailable_reason": "fixture input could not be read"}
        )
        record = start_run(
            storage,
            hypothesis="Offline synthetic account/execution fixture check (M2-01).",
            phase="fixture",
            resolved_config={"case_selector": case_selector},
            repo_root=Path(__file__).resolve().parents[2],
            **provenance,
        )
        attempt_dir = out_dir / "attempts" / record.run_id
        attempt_dir.mkdir(parents=True, exist_ok=False)
        trace_path, manifest_path = (
            attempt_dir / "trace.jsonl",
            attempt_dir / "manifest.json",
        )
        outcomes, trace_records = [], []
        complete = False
        try:
            with run_context(storage, record):
                try:
                    if input_error is not None:
                        raise input_error
                    try:
                        cases = {c["id"]: c for c in json.loads(raw)["cases"]}
                    except (ValueError, KeyError, TypeError) as exc:
                        raise FixtureCliError("invalid fixture cases input") from exc
                    if case_selector == "all":
                        selected = [cases[cid] for cid in sorted(cases)]
                    elif case_selector in cases:
                        selected = [cases[case_selector]]
                    else:
                        raise FixtureCliError(
                            f"unknown fixture case: {case_selector!r}"
                        )
                    for case in selected:
                        result = run_case(case)
                        problems = compare_case(case, result)
                        outcomes.append(FixtureCaseOutcome(case["id"], problems))
                        trace_records.append(
                            {
                                "case_id": case["id"],
                                "ok": not problems,
                                "problems": problems,
                                **asdict(result),
                            }
                        )
                    complete = True
                    if any(not o.ok for o in outcomes):
                        raise _FixtureMismatch(
                            f"{sum(not o.ok for o in outcomes)} case(s) mismatched"
                        )
                finally:
                    trace = "".join(
                        json.dumps(row, sort_keys=True, default=_json_default) + "\n"
                        for row in trace_records
                    )
                    trace_path.write_text(trace, encoding="utf-8")
                    _write_json(
                        manifest_path,
                        {
                            "schema_version": 2,
                            "evidence_type": "synthetic_account_fixtures",
                            "run_id": record.run_id,
                            "case_selector": case_selector,
                            "cases": [o.case_id for o in outcomes],
                            "all_ok": complete and all(o.ok for o in outcomes),
                            "config_hash": record.config_hash,
                            "code_hash": record.code_hash,
                            "data_hash": record.data_hash,
                            "dependency_lock_hash": record.dependency_lock_hash,
                            "semantic_trace_hash": hashlib.sha256(
                                trace.encode()
                            ).hexdigest(),
                        },
                    )
                    record.artifacts.extend([str(trace_path), str(manifest_path)])
        except _FixtureMismatch:
            pass
        return outcomes, trace_path, manifest_path
    finally:
        storage.close()


class _FixtureMismatch(Exception):
    """Internal-only: makes `run_context` finalize this attempt as `failed` with a truthful,
    retained reason when one or more fixture checkpoints did not match."""

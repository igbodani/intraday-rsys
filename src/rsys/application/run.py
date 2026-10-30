"""Synthetic one-session run CLI with retained attempts and replay-backed execution.

This remains a distinct schema from ReplayConfig and accepts no real provider input.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from rsys.application.builders import build_runner
from rsys.application.errors import RunConfigError
from rsys.application.results import summarize_runner
from rsys.application.session_config import validate


def load_session_config(path: str | Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RunConfigError(f"cannot read run config: {path}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RunConfigError(f"run config is not valid JSON: {exc}") from exc


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if hasattr(obj, "value"):  # enum-like strategy states
        return obj.value
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def run_synthetic_session(config: dict[str, Any]) -> dict[str, Any]:
    runner, bars, scanner = build_runner(config)
    starting = runner.portfolio.equity({})
    runner.run_session(bars, scanner)
    return summarize_runner(runner, starting)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )


def run_cli_entry(
    config_path: str | Path, out_dir: Path, *, report: bool = False
) -> tuple[dict[str, Any], str, Path, Path]:
    """Retain every invocation, including unreadable/invalid input and partial runs."""
    import hashlib

    from rsys.reporting.html import render_report_for_attempt
    from rsys.research.experiments import run_context, start_run
    from rsys.research.storage import RunStorage, canonical_hash

    config, error, raw = {}, None, None
    try:
        raw = Path(config_path).read_bytes()
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise RunConfigError("run config must be an object")
    except (OSError, UnicodeError, json.JSONDecodeError, RunConfigError) as exc:
        error = RunConfigError(
            "cannot read run config"
            if isinstance(exc, OSError)
            else "run config is not valid JSON/object"
        )
        config = {"input_unavailable_or_invalid": True}
    provenance = (
        {"data_identity": "sha256:" + hashlib.sha256(raw).hexdigest()}
        if raw is not None
        else {"data_unavailable_reason": "input could not be read"}
    )
    if error is None and config.get("schema_version") == 2:
        from rsys.application.batch import run_batch_cli

        return run_batch_cli(config, raw, out_dir, report=report)
    storage = RunStorage(out_dir)
    try:
        record = start_run(
            storage,
            hypothesis="Offline synthetic bar session; execution and bounded replay composition.",
            phase="run",
            resolved_config=config,
            repo_root=Path(__file__).resolve().parents[3],
            **provenance,
        )
        attempt = out_dir / "attempts" / record.run_id
        attempt.mkdir(parents=True, exist_ok=False)
        trace_path, manifest_path = attempt / "trace.jsonl", attempt / "manifest.json"
        runner = None
        with run_context(storage, record):
            manifest = {
                "schema_version": 2,
                "evidence_type": "synthetic_standalone",
                "run_id": record.run_id,
                "status": "failed",
                "config_hash": record.config_hash,
                "data_hash": record.data_hash,
                "code_hash": record.code_hash,
                "code_revision": record.code_revision,
                "dependency_lock_hash": record.dependency_lock_hash,
                "execution_basis": "synthetic_bar_open_v2",
                "calendar_version": "nyse-2024-v1",
                "scanner_basis": "supplied_synthetic_scores",
                "account_basis": "synthetic_immediate_settlement",
                "completion_policy": "required_exits_or_finite_input_snapshot",
            }
            try:
                if error is not None:
                    raise error
                runner, bars, scanner = build_runner(config)
                starting = runner.portfolio.equity({})
                # Resolve defaults into evidence, so an omitted setting is never a hidden assumption.
                manifest.update(
                    {
                        "session_date": validate(config),
                        "initial_cash": config["initial_cash"],
                        "universe": sorted(config["symbols"]),
                        "sizing": asdict(runner.sizing),
                        "execution": asdict(runner.execution),
                        "top_n": runner.top_n,
                        "strategies": {
                            s: {
                                "strategy": config["symbols"][s]["strategy"],
                                "config": asdict(v.config),
                            }
                            for s, v in runner.strategies.items()
                        },
                        "market_data_hash": canonical_hash(
                            {s: v["bars"] for s, v in sorted(config["symbols"].items())}
                        ),
                        "scanner_hash": canonical_hash(
                            config.get("scanner_inputs", {})
                        ),
                    }
                )
                runner.run_session(bars, scanner)
                summary = summarize_runner(runner, starting)
                manifest.update(
                    {
                        k: summary[k]
                        for k in (
                            "starting_equity",
                            "ending_equity",
                            "metrics",
                            "open_positions",
                        )
                    }
                )
                manifest["status"] = "success"
                record.metrics = json.loads(
                    json.dumps(summary["metrics"], default=_json_default)
                )
            finally:
                rows = runner.trace if runner is not None else []
                trace_text = "".join(
                    json.dumps(row, sort_keys=True, default=_json_default) + "\n"
                    for row in rows
                )
                trace_path.write_bytes(trace_text.encode("utf-8"))
                manifest["semantic_trace_hash"] = hashlib.sha256(
                    trace_text.encode()
                ).hexdigest()
                _write_json(manifest_path, manifest)
                record.artifacts.extend([str(trace_path), str(manifest_path)])
                record.decision_gate_status["manifest_sha256"] = hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest()
            if report:
                report_path = render_report_for_attempt(attempt)
                record.artifacts.append(str(report_path))
        return summary, record.run_id, trace_path, manifest_path
    finally:
        storage.close()

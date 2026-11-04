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
from rsys.research.diagnostics import (
    DiagnosticWriter,
    atomic_write_json,
    invocation_scope,
    protect_evidence_write,
    start_invocation,
)
from rsys.research.errors import DiagnosticWriteError
from rsys.research.trace import TraceWriter, read_trace


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
    atomic_write_json(path, value, default=_json_default)


def run_cli_entry(
    config_path: str | Path,
    out_dir: Path,
    *,
    report: bool = False,
    invocation: DiagnosticWriter | None = None,
) -> tuple[dict[str, Any], str, Path, Path]:
    with invocation_scope(out_dir, command="run", invocation=invocation) as writer:
        return _run_cli_entry(config_path, out_dir, report=report, invocation=writer)


def _run_cli_entry(config_path, out_dir, *, report, invocation):
    """Retain every invocation, including unreadable/invalid input and partial runs."""
    import hashlib

    from rsys.reporting.html import render_report
    from rsys.research.experiments import redact_secrets, run_context, start_run
    from rsys.research.storage import RunStorage, canonical_hash

    out_dir = Path(out_dir)
    owns_invocation = invocation is None
    if invocation is None:
        invocation = start_invocation(out_dir, command="run")
    try:
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
        invocation.use_config(config)
        invocation.record_identity(
            input_path=str(config_path),
            input_sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None,
        )
        provenance = (
            {"data_identity": "sha256:" + hashlib.sha256(raw).hexdigest()}
            if raw is not None
            else {"data_unavailable_reason": "input could not be read"}
        )
        if error is None and config.get("schema_version") == 2:
            from rsys.application.batch import run_batch_cli

            return run_batch_cli(
                config, raw, out_dir, report=report, invocation=invocation
            )
        storage = RunStorage(out_dir)
        try:
            try:
                record = start_run(
                    storage,
                    hypothesis="Offline synthetic bar session; execution and bounded replay composition.",
                    phase="run",
                    resolved_config=config,
                    repo_root=Path(__file__).resolve().parents[3],
                    **provenance,
                )
            except BaseException as exc:
                invocation.fail(exc, stage="preparing")
                raise
            attempt = out_dir / "attempts" / record.run_id
            trace_path, manifest_path = (
                attempt / "trace.jsonl",
                attempt / "manifest.json",
            )
            runner, trace_writer = None, None
            try:
                # Inside the fail()-covered try: a collision/permission failure here
                # must still leave a FAILURE event, never an invocation stuck "resolved".
                with run_context(storage, record):
                    invocation.link_run(record.run_id)
                    attempt.mkdir(parents=True, exist_ok=False)
                    manifest = {
                        "schema_version": 2,
                        "evidence_type": "synthetic_standalone",
                        "run_id": record.run_id,
                        "status": "failed",
                        "metrics_scope": "partial_attempt",
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
                        "diagnostics_version": 1,
                        "trace_version": 1,
                    }
                    try:

                        def prepare_evidence():
                            nonlocal trace_writer
                            trace_writer = TraceWriter(trace_path)
                            _write_json(attempt / "input.json", redact_secrets(config))
                            _write_json(
                                attempt / "resolved_config.json", record.resolved_config
                            )

                        if error is not None:
                            try:
                                raise error
                            finally:
                                protect_evidence_write(
                                    prepare_evidence,
                                    catch=(DiagnosticWriteError, OSError),
                                )
                        prepare_evidence()
                        runner, bars, scanner = build_runner(config)
                        runner.event_sink = trace_writer.write
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
                                    {
                                        s: v["bars"]
                                        for s, v in sorted(config["symbols"].items())
                                    }
                                ),
                                "scanner_hash": canonical_hash(
                                    config.get("scanner_inputs", {})
                                ),
                            }
                        )
                        runner.run_session(bars, scanner)
                        trace_writer.close()
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
                        manifest["metrics_scope"] = "complete_attempt"
                        record.metrics = json.loads(
                            json.dumps(summary["metrics"], default=_json_default)
                        )
                    except BaseException as exc:
                        if isinstance(exc, KeyboardInterrupt):
                            manifest["status"] = "interrupted"
                        # Record the execution failure before finally seals the
                        # journal. Outer handlers deduplicate this exception.
                        invocation.fail(exc)
                        raise
                    finally:
                        if trace_writer is not None:
                            protect_evidence_write(trace_writer.close)
                        rows = runner.trace if runner is not None else []

                        def _finalize_evidence() -> None:
                            # Everything here -- the trace/manifest files and the
                            # diagnostics journal's own finalization -- is "evidence
                            # writing" in T04's sense. A failure partway (disk-full,
                            # permission denial) must never replace/mask whatever
                            # application exception is already propagating through
                            # this `finally` block; it is attached to that exception
                            # instead (`protect_evidence_write`), never raised in its
                            # place.
                            trace_state = read_trace(trace_path, collect_records=False)
                            manifest["semantic_trace_hash"] = trace_state.sha256
                            manifest["trace_records"] = trace_state.record_count
                            manifest["trace_complete"] = (
                                manifest["status"] == "success"
                                and not trace_state.incomplete_tail
                            )
                            if runner is not None:
                                partial_summary = summarize_runner(runner, starting)
                                _write_json(
                                    attempt / "fills.json", partial_summary["fills"]
                                )
                                _write_json(
                                    attempt / "account.json", runner._snapshot()
                                )
                            if invocation.stage != "failed":
                                invocation.set_stage("finalizing")
                            # ARTIFACTS_READY must only be journaled once the
                            # manifest (the last required artifact) is actually
                            # on disk -- never before, or a manifest-write
                            # failure would leave a journal falsely claiming
                            # readiness for an attempt that was never fully
                            # saved.
                            if report and manifest["status"] == "success":
                                (attempt / "report.html").write_text(
                                    render_report(manifest, rows), encoding="utf-8"
                                )
                            artifacts = sorted(
                                p for p in attempt.iterdir() if p.is_file()
                            )
                            manifest["artifact_hashes"] = {
                                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in artifacts
                            }
                            _write_json(manifest_path, manifest)
                            record.artifacts.extend(
                                str(p) for p in [*artifacts, manifest_path]
                            )
                            record.decision_gate_status["manifest_sha256"] = (
                                hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                            )
                            if manifest["status"] == "success":
                                invocation.artifacts_ready(stage=invocation.stage)
                            record.decision_gate_status["diagnostics"] = json.dumps(
                                invocation.seal(), sort_keys=True
                            )

                        protect_evidence_write(
                            _finalize_evidence, catch=(DiagnosticWriteError, OSError)
                        )
            except BaseException as exc:
                exc.run_id, exc.trace_path, exc.manifest_path = (
                    record.run_id,
                    trace_path,
                    manifest_path,
                )
                invocation.fail(exc)
                raise
            return summary, record.run_id, trace_path, manifest_path
        finally:
            storage.close()
    finally:
        if owns_invocation:
            invocation.close()

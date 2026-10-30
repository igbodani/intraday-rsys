"""Synthetic one-session run CLI with retained attempts and replay-backed execution.

This remains a distinct schema from ReplayConfig and accepts no real provider input.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from rsys.evaluation import compute_metrics
from rsys.portfolio import Portfolio
from rsys.session_config import RunConfigError, validate
from rsys.session_config import ms as _ms
from rsys.standalone_run import ExecutionPolicy, SizingPolicy, StandaloneRunner
from rsys.strategies.orb import CompletedBar as OrbBar
from rsys.strategies.orb import OrbConfig, OrbStrategy
from rsys.strategies.vwap import CompletedBar as VwapBar
from rsys.strategies.vwap import VwapConfig, VwapStrategy

_STRATEGY_IDS = ("orb_close_cross_v1", "vwap_touch_hold_v1")


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _build_strategy(symbol: str, strategy_id: str, spec: dict[str, Any]) -> Any:
    options = dict(spec.get("strategy_config", {}))
    for key in ("tick", "target_r"):
        if key in options:
            options[key] = _dec(options[key])
    if strategy_id == "orb_close_cross_v1":
        return OrbStrategy(
            symbol, _ms(spec.get("session_open", "09:30:00")), OrbConfig(**options)
        )
    if strategy_id == "vwap_touch_hold_v1":
        return VwapStrategy(symbol, VwapConfig(**options))
    raise RunConfigError(
        f"unknown strategy id: {strategy_id!r}; supported: {_STRATEGY_IDS}"
    )


def _build_bar(strategy_id: str, raw: dict[str, Any]) -> Any:
    bar_cls = OrbBar if strategy_id == "orb_close_cross_v1" else VwapBar
    return bar_cls(
        bar_start_ms=_ms(raw["bar_start"]),
        available_at_ms=_ms(raw["available_at"]),
        open=_dec(raw["open"]),
        high=_dec(raw["high"]),
        low=_dec(raw["low"]),
        close=_dec(raw["close"]),
        volume=_dec(raw["volume"]),
    )


def load_session_config(path: str | Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RunConfigError(f"cannot read run config: {path}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RunConfigError(f"run config is not valid JSON: {exc}") from exc


def build_runner(
    config: dict[str, Any],
) -> tuple[
    StandaloneRunner,
    dict[str, list[Any]],
    dict[int, dict[str, tuple[Decimal, Decimal]]],
]:
    session_date = validate(config)
    try:
        strategies: dict[str, Any] = {}
        bars_by_symbol: dict[str, list[Any]] = {}
        for symbol, spec in config["symbols"].items():
            strategy_id = spec["strategy"]
            strategies[symbol] = _build_strategy(symbol, strategy_id, spec)
            bars_by_symbol[symbol] = [
                _build_bar(strategy_id, raw) for raw in spec["bars"]
            ]

        sizing_raw = config.get("sizing", {})
        sizing = SizingPolicy(
            risk_budget=_dec(sizing_raw["risk_budget"])
            if sizing_raw.get("risk_budget") is not None
            else None,
            max_quantity=_dec(sizing_raw["max_quantity"])
            if sizing_raw.get("max_quantity") is not None
            else None,
            quantity_step=_dec(sizing_raw.get("quantity_step", "1")),
            entry_fee=_dec(sizing_raw.get("entry_fee", "0")),
            exit_fee=_dec(sizing_raw.get("exit_fee", "0")),
        )
        execution_raw = config.get("execution", {})
        execution = ExecutionPolicy(
            max_hold_ms=execution_raw.get("max_hold_ms"),
            decision_latency_ms=execution_raw.get("decision_latency_ms", 0),
            flatten_at_ms=_ms(execution_raw["flatten_at"])
            if execution_raw.get("flatten_at") is not None
            else None,
        )
        cash = _dec(config["initial_cash"])
        portfolio = Portfolio(
            total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0)
        )
        runner = StandaloneRunner(
            strategies,
            portfolio,
            sizing,
            execution=execution,
            top_n=config.get("top_n", 20),
            session_date=session_date,
        )

        scanner_inputs: dict[int, dict[str, tuple[Decimal, Decimal]]] = {}
        for time_local, rows in config.get("scanner_inputs", {}).items():
            scanner_inputs[_ms(time_local)] = {
                symbol: (
                    _dec(row["rvol"]) if row["rvol"] is not None else None,
                    _dec(row["gain"]) if row["gain"] is not None else None,
                )
                for symbol, row in rows.items()
            }
        return runner, bars_by_symbol, scanner_inputs
    except KeyError as exc:
        raise RunConfigError(f"missing required config field: {exc}") from exc


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if hasattr(obj, "value"):  # enum-like strategy states
        return obj.value
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def summarize_runner(runner, starting_equity):
    ending_equity = runner.portfolio.equity({})
    metrics = compute_metrics(
        starting_equity, ending_equity, runner.portfolio.realized_pnl, runner.fills
    )
    return {
        "starting_equity": starting_equity,
        "ending_equity": ending_equity,
        "decisions": [asdict(d) for d in runner.decisions],
        "fills": [asdict(f) for f in runner.fills],
        "metrics": asdict(metrics),
        "trace": runner.trace,
        "open_positions": {
            symbol: {
                "quantity": p.quantity,
                "entry": p.entry_price,
                "stop": p.stop,
                "target": p.target,
            }
            for symbol, p in runner.portfolio.positions.items()
        },
    }


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

    from rsys.experiments import run_context, start_run
    from rsys.report import render_report_for_attempt
    from rsys.storage import RunStorage, canonical_hash

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
        from rsys.batch_run import run_batch_cli
        return run_batch_cli(config, raw, out_dir, report=report)
    storage = RunStorage(out_dir)
    try:
        record = start_run(
            storage,
            hypothesis="Offline synthetic bar session; execution and bounded replay composition.",
            phase="run",
            resolved_config=config,
            repo_root=Path(__file__).resolve().parents[2],
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

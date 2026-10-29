"""`rsys run` orchestration glue for a synthetic, standalone ORB/VWAP session
(docs/M2_IMPLEMENTATION_HANDOFF.md step 9, run/compare subset).

Honesty note: this config schema and CLI drive ONLY `StandaloneRunner` (synthetic,
in-memory scanner + strategy + portfolio composition). It is NOT wired into `ReplayRunner`
or real market data, and it is a distinct schema from `rsys.config.ReplayConfig` -- see
docs/M2_IMPLEMENTATION_STATUS.md.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from rsys.evaluation import compute_metrics
from rsys.portfolio import Portfolio
from rsys.standalone_run import ExecutionPolicy, SizingPolicy, StandaloneRunner
from rsys.strategies.orb import CompletedBar as OrbBar
from rsys.strategies.orb import OrbStrategy
from rsys.strategies.vwap import CompletedBar as VwapBar
from rsys.strategies.vwap import VwapStrategy

_STRATEGY_IDS = ("orb_close_cross_v1", "vwap_touch_hold_v1")


class RunConfigError(Exception):
    """Malformed, missing-field or unsupported synthetic run configuration."""


def _ms(time_local: str) -> int:
    time_part = time_local.split("T")[-1]  # accepts plain "HH:MM:SS" or an ISO "...THH:MM:SS"
    hours, minutes, seconds = (int(part) for part in time_part.split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _build_strategy(symbol: str, strategy_id: str, spec: dict[str, Any]) -> Any:
    if strategy_id == "orb_close_cross_v1":
        return OrbStrategy(symbol, _ms(spec["session_open"]))
    if strategy_id == "vwap_touch_hold_v1":
        return VwapStrategy(symbol)
    raise RunConfigError(f"unknown strategy id: {strategy_id!r}; supported: {_STRATEGY_IDS}")


def _build_bar(strategy_id: str, raw: dict[str, Any]) -> Any:
    bar_cls = OrbBar if strategy_id == "orb_close_cross_v1" else VwapBar
    return bar_cls(
        bar_start_ms=_ms(raw["bar_start"]), available_at_ms=_ms(raw["available_at"]),
        open=_dec(raw["open"]), high=_dec(raw["high"]), low=_dec(raw["low"]),
        close=_dec(raw["close"]), volume=_dec(raw["volume"]),
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


def build_runner(config: dict[str, Any]) -> tuple[StandaloneRunner, dict[str, list[Any]], dict[int, dict[str, tuple[Decimal, Decimal]]]]:
    try:
        strategies: dict[str, Any] = {}
        bars_by_symbol: dict[str, list[Any]] = {}
        for symbol, spec in config["symbols"].items():
            strategy_id = spec["strategy"]
            strategies[symbol] = _build_strategy(symbol, strategy_id, spec)
            bars_by_symbol[symbol] = [_build_bar(strategy_id, raw) for raw in spec["bars"]]

        sizing_raw = config.get("sizing", {})
        sizing = SizingPolicy(
            risk_budget=_dec(sizing_raw["risk_budget"]) if sizing_raw.get("risk_budget") is not None else None,
            max_quantity=_dec(sizing_raw["max_quantity"]) if sizing_raw.get("max_quantity") is not None else None,
            quantity_step=_dec(sizing_raw.get("quantity_step", "1")),
            entry_fee=_dec(sizing_raw.get("entry_fee", "0")), exit_fee=_dec(sizing_raw.get("exit_fee", "0")),
        )
        execution_raw = config.get("execution", {})
        execution = ExecutionPolicy(
            max_hold_ms=execution_raw.get("max_hold_ms"),
            flatten_at_ms=_ms(execution_raw["flatten_at"]) if execution_raw.get("flatten_at") is not None else None,
        )
        cash = _dec(config["initial_cash"])
        portfolio = Portfolio(total_cash=cash, spendable_cash=cash, unsettled_cash=Decimal(0))
        runner = StandaloneRunner(
            strategies, portfolio, sizing, execution=execution, top_n=config.get("top_n", 20),
        )

        scanner_inputs: dict[int, dict[str, tuple[Decimal, Decimal]]] = {}
        for time_local, rows in config.get("scanner_inputs", {}).items():
            scanner_inputs[_ms(time_local)] = {
                symbol: (_dec(row["rvol"]), _dec(row["gain"])) for symbol, row in rows.items()
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


def run_synthetic_session(config: dict[str, Any]) -> dict[str, Any]:
    """Runs the configured session to completion and returns a JSON-serializable summary
    (decisions, fills, reconciled metrics). Raises RunConfigError for a malformed config."""
    runner, bars_by_symbol, scanner_inputs = build_runner(config)
    starting_equity = runner.portfolio.equity({})
    runner.run_session(bars_by_symbol, scanner_inputs)
    ending_equity = runner.portfolio.equity({})
    metrics = compute_metrics(starting_equity, ending_equity, runner.portfolio.realized_pnl, runner.fills)

    return {
        "starting_equity": starting_equity, "ending_equity": ending_equity,
        "decisions": [asdict(d) for d in runner.decisions],
        "fills": [asdict(f) for f in runner.fills],
        "metrics": asdict(metrics),
        "open_positions": {
            symbol: {"quantity": p.quantity, "entry": p.entry_price, "stop": p.stop, "target": p.target}
            for symbol, p in runner.portfolio.positions.items()
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def run_cli_entry(config_path: str | Path, out_dir: Path) -> tuple[dict[str, Any], str, Path, Path]:
    """Runs one retained attempt: loads the config, runs the session, writes a trace/manifest
    under `out_dir/attempts/<run_id>/` via the existing experiments/storage lifecycle, and
    returns (summary, run_id, trace_path, manifest_path). Raises RunConfigError for an
    unreadable/malformed config (caller maps this to the CLI's invalid-config exit code)."""
    from rsys.experiments import run_context, start_run
    from rsys.storage import RunStorage

    config = load_session_config(config_path)
    storage = RunStorage(out_dir)
    try:
        record = start_run(
            storage, hypothesis="Offline synthetic ORB/VWAP session: scanner -> proposal -> acceptance -> fill -> exit.",
            phase="run", resolved_config=config, data_identity=f"sha256:{Path(config_path).stat().st_mtime_ns}",
            repo_root=Path(__file__).resolve().parents[2],
        )
        attempt_dir = out_dir / "attempts" / record.run_id
        attempt_dir.mkdir(parents=True, exist_ok=False)
        trace_path, manifest_path = attempt_dir / "trace.jsonl", attempt_dir / "manifest.json"
        summary: dict[str, Any] = {}
        with run_context(storage, record):
            summary = run_synthetic_session(config)
            with trace_path.open("w", encoding="utf-8") as handle:
                for decision in summary["decisions"]:
                    handle.write(json.dumps({"kind": "decision", **decision}, sort_keys=True, default=_json_default))
                    handle.write("\n")
                for fill in summary["fills"]:
                    handle.write(json.dumps({"kind": "fill", **fill}, sort_keys=True, default=_json_default))
                    handle.write("\n")
            _write_json(manifest_path, {
                "schema_version": 1, "run_id": record.run_id, "config_hash": record.config_hash,
                "code_hash": record.code_hash, "starting_equity": summary["starting_equity"],
                "ending_equity": summary["ending_equity"], "metrics": summary["metrics"],
                "open_positions": summary["open_positions"],
                "universe": sorted(config["symbols"]), "sizing": config.get("sizing", {}),
                "execution": config.get("execution", {}),
            })
            record.artifacts.extend([str(trace_path), str(manifest_path)])
        return summary, record.run_id, trace_path, manifest_path
    finally:
        storage.close()

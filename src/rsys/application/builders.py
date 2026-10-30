"""Construct strategies, input bars and session runners from validated settings."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rsys.account.models import SizingPolicy
from rsys.account.portfolio import Portfolio
from rsys.application.errors import RunConfigError
from rsys.application.session_config import ms as _ms
from rsys.application.session_config import validate
from rsys.execution.bar import BarSessionRunner
from rsys.execution.models import ExecutionPolicy
from rsys.strategies.models import CompletedBar, OrbConfig, VwapConfig
from rsys.strategies.orb import OrbStrategy
from rsys.strategies.vwap import VwapStrategy

_STRATEGY_IDS = ("orb_close_cross_v1", "vwap_touch_hold_v1")


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def build_strategy(symbol: str, strategy_id: str, spec: dict[str, Any]) -> Any:
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


def build_bar(strategy_id: str, raw: dict[str, Any]) -> Any:
    return CompletedBar(
        bar_start_ms=_ms(raw["bar_start"]),
        available_at_ms=_ms(raw["available_at"]),
        open=_dec(raw["open"]),
        high=_dec(raw["high"]),
        low=_dec(raw["low"]),
        close=_dec(raw["close"]),
        volume=_dec(raw["volume"]),
    )


def build_runner(
    config: dict[str, Any],
) -> tuple[
    BarSessionRunner,
    dict[str, list[Any]],
    dict[int, dict[str, tuple[Decimal, Decimal]]],
]:
    session_date = validate(config)
    try:
        strategies: dict[str, Any] = {}
        bars_by_symbol: dict[str, list[Any]] = {}
        for symbol, spec in config["symbols"].items():
            strategy_id = spec["strategy"]
            strategies[symbol] = build_strategy(symbol, strategy_id, spec)
            bars_by_symbol[symbol] = [
                build_bar(strategy_id, raw) for raw in spec["bars"]
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
        runner = BarSessionRunner(
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

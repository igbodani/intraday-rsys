"""Parameter registry: proposed values, valid ranges and decision dependencies.

Mirrors the "Parameter registry" table in docs/contracts.md and the decision
register in docs/decisions.md. This module does not invent values; it only
records what the docs already state, so registry entries must stay in sync
with those two files if they change.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParameterSpec:
    """One registry row: a field path, its proposed value and its blocker."""

    field_path: str
    units: str
    status: str  # "decided" | "proposed" | "open"
    decision_id: str | None
    rationale: str
    validator: Callable[[Any], bool] | None = None

    def is_unresolved(self, value: Any) -> bool:
        """An 'open' parameter left at its unset default still blocks dependent work."""
        return self.status == "open" and value is None

    def validate(self, value: Any) -> str | None:
        """Return an error message if value violates the declared range/constraint, else None."""
        if value is None:
            return None
        if self.validator is not None and not self.validator(value):
            return f"{self.field_path}: value {value!r} outside valid range/constraint ({self.units})"
        return None


def _positive_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _nonnegative(v: Any) -> bool:
    try:
        return float(v) >= 0
    except (TypeError, ValueError):
        return False


# Field paths use dotted notation relative to the research config root.
REGISTRY: dict[str, ParameterSpec] = {
    "base_interval_minutes": ParameterSpec(
        "base_interval_minutes", "minutes", "proposed", "D05",
        "1 in v1; extensions require parity tests", lambda v: v == 1,
    ),
    "scanner.top_n": ParameterSpec(
        "scanner.top_n", "count", "proposed", "D14",
        "Integer 1-500; cannot exceed eligible symbols",
        lambda v: isinstance(v, int) and 1 <= v <= 500,
    ),
    "scanner.lookback_sessions": ParameterSpec(
        "scanner.lookback_sessions", "sessions", "proposed", "D14",
        "Integer 2-252; requires full selected history", lambda v: isinstance(v, int) and 2 <= v <= 252,
    ),
    "scanner.max_lookback_calendar_sessions": ParameterSpec(
        "scanner.max_lookback_calendar_sessions", "sessions", "proposed", "D14",
        "Integer >= lookback_sessions", _positive_int,
    ),
    "scanner.min_prior_dollar_volume": ParameterSpec(
        "scanner.min_prior_dollar_volume", "USD", "open", "D12",
        "Positive USD threshold; requires data audit",
    ),
    "reporting.ranking_storage": ParameterSpec(
        "reporting.ranking_storage", "enum", "proposed", "D21",
        "compact, proposals or full; full requires a bounded diagnostic run",
        lambda v: v in ("compact", "proposals", "full"),
    ),
    "strategy.range_minutes": ParameterSpec(
        "strategy.range_minutes", "minutes", "proposed", "D15",
        "1-60; integer multiple of base interval", lambda v: isinstance(v, int) and 1 <= v <= 60,
    ),
    "strategy.breakout_buffer_ticks": ParameterSpec(
        "strategy.breakout_buffer_ticks", "ticks", "proposed", "D15",
        "Integer 0-10; applied using instrument tick", lambda v: isinstance(v, int) and 0 <= v <= 10,
    ),
    "exit.target_r": ParameterSpec(
        "exit.target_r", "R multiple", "proposed", "D15",
        "Positive finite value; baseline search candidate 1-4", lambda v: float(v) > 0,
    ),
    "exit.maximum_hold_minutes": ParameterSpec(
        "exit.maximum_hold_minutes", "minutes", "proposed", "D15",
        "Positive multiple of base interval; flatten may occur earlier", _positive_int,
    ),
    "strategy.entry_cutoff_local": ParameterSpec(
        "strategy.entry_cutoff_local", "exchange-local time", "proposed", "D15",
        "Must precede flatten time",
    ),
    "exit.flatten_buffer_minutes": ParameterSpec(
        "exit.flatten_buffer_minutes", "minutes", "proposed", "D15",
        "At least one base bar; less than session duration", _positive_int,
    ),
    "strategy.max_fills_per_symbol_day": ParameterSpec(
        "strategy.max_fills_per_symbol_day", "count", "proposed", "D15",
        "Positive integer; v1 proposed baseline 1", _positive_int,
    ),
    "feed": ParameterSpec(
        "feed", "provider feed id", "open", "D12", "Feed entitlement and coverage must be audited first",
    ),
    "data_manifest": ParameterSpec(
        "data_manifest", "manifest ref", "open", "D12", "Coverage manifest must be produced by the data audit",
    ),
    "account.profile": ParameterSpec(
        "account.profile", "profile id", "open", "D09", "Requires verified Alpaca account profile",
    ),
    "account.quantity_step": ParameterSpec(
        "account.quantity_step", "shares", "open", "D09", "Requires verified whole/fractional share rules",
    ),
    "account.minimum_quantity": ParameterSpec(
        "account.minimum_quantity", "shares", "open", "D09", "Requires verified minimum order size",
    ),
    "account.proceeds_availability": ParameterSpec(
        "account.proceeds_availability", "policy id", "open", "D09", "Requires verified settlement/proceeds policy",
    ),
    "risk.fraction_per_trade": ParameterSpec(
        "risk.fraction_per_trade", "fraction of equity", "open", "D10", "Requires confirmed per-trade risk limit",
    ),
    "risk.daily_loss_limit_usd": ParameterSpec(
        "risk.daily_loss_limit_usd", "USD", "open", "D10", "Requires confirmed daily loss stop",
    ),
    "risk.drawdown_limit_fraction": ParameterSpec(
        "risk.drawdown_limit_fraction", "fraction of equity", "open", "D10", "Requires confirmed drawdown ceiling",
    ),
    "risk.max_positions": ParameterSpec(
        "risk.max_positions", "count", "open", "D10", "Requires confirmed simultaneous-position limit",
    ),
    "risk.max_position_notional_usd": ParameterSpec(
        "risk.max_position_notional_usd", "USD", "open", "D10", "Requires confirmed exposure cap",
    ),
    "execution.spread_bps": ParameterSpec(
        "execution.spread_bps", "basis points", "open", "D11", "Requires confirmed cost/latency assumptions",
    ),
    "execution.slippage_bps": ParameterSpec(
        "execution.slippage_bps", "basis points", "open", "D11", "Requires confirmed cost/latency assumptions",
    ),
    "execution.fee_profile": ParameterSpec(
        "execution.fee_profile", "profile id", "open", "D11", "Requires confirmed fee schedule",
    ),
    "execution.feed_delay_ms": ParameterSpec(
        "execution.feed_delay_ms", "milliseconds", "open", "D11", "Requires confirmed feed delay",
        _nonnegative,
    ),
    "execution.decision_latency_ms": ParameterSpec(
        "execution.decision_latency_ms", "milliseconds", "open", "D11", "Requires confirmed decision latency",
        _nonnegative,
    ),
    "experiment.development_start": ParameterSpec(
        "experiment.development_start", "date", "open", "D16", "Requires frozen chronological split",
    ),
    "experiment.development_end": ParameterSpec(
        "experiment.development_end", "date", "open", "D16", "Requires frozen chronological split",
    ),
    "experiment.validation_start": ParameterSpec(
        "experiment.validation_start", "date", "open", "D16", "Requires frozen chronological split",
    ),
    "experiment.validation_end": ParameterSpec(
        "experiment.validation_end", "date", "open", "D16", "Requires frozen chronological split",
    ),
    "experiment.final_start": ParameterSpec(
        "experiment.final_start", "date", "open", "D16", "Requires frozen chronological split",
    ),
    "experiment.final_end": ParameterSpec(
        "experiment.final_end", "date", "open", "D16", "Requires frozen chronological split",
    ),
    "experiment.trial_budget": ParameterSpec(
        "experiment.trial_budget", "count", "open", "D16", "Requires a bounded, registered trial budget",
    ),
    "experiment.seed": ParameterSpec(
        "experiment.seed", "seed value", "open", "D16", "Requires a declared seed for reproducibility",
    ),
    "promotion_gates": ParameterSpec(
        "promotion_gates", "gate spec", "open", "D17", "Requires frozen promotion thresholds and uncertainty method",
    ),
    "data_adjustment.symbol_mapping_asof": ParameterSpec(
        "data_adjustment.symbol_mapping_asof", "asof ref", "open", "D13", "Requires audited historical universe mapping",
    ),
}


def get_spec(field_path: str) -> ParameterSpec | None:
    return REGISTRY.get(field_path)

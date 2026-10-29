"""Parameter registry: proposed values, valid ranges and decision dependencies.

Mirrors the "Parameter registry" table in docs/contracts.md and the decision
register in docs/decisions.md. This module does not invent values; it only
records what the docs already state, so registry entries must stay in sync
with those two files if they change.
"""
from __future__ import annotations

import math
import re
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
        """Return an error message if value violates the declared range/constraint, else None.
        Never echoes the raw value itself: a resolved-but-invalid field may still hold something
        secret-like, and the message is surfaced directly to CLI stderr/blocker output.
        """
        if value is None:
            return None
        if self.validator is not None and not self.validator(value):
            return f"{self.field_path}: value outside valid range/constraint ({self.units})"
        return None


def _positive_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _nonnegative(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    try:
        return math.isfinite(float(v)) and float(v) >= 0
    except (TypeError, ValueError):
        return False


def _positive_number(v: Any) -> bool:
    """Shared with config.py: finite, strictly positive Decimal/int/float, excluding bool."""
    if isinstance(v, bool):
        return False
    try:
        return math.isfinite(float(v)) and float(v) > 0
    except (TypeError, ValueError):
        return False


def _fraction_0_1(v: Any) -> bool:
    """Shared with config.py: finite value in (0, 1], e.g. a fraction of equity."""
    if isinstance(v, bool):
        return False
    try:
        f = float(v)
        return math.isfinite(f) and 0 < f <= 1
    except (TypeError, ValueError):
        return False


def _non_empty_str(v: Any) -> bool:
    """Shared with config.py: a required identifier is present and not blank."""
    return isinstance(v, str) and v.strip() != ""


def _non_empty_str_list(v: Any) -> bool:
    """A nonempty list whose elements are themselves nonblank strings (no blank list elements)."""
    return isinstance(v, list) and len(v) > 0 and all(_non_empty_str(item) for item in v)


_METHOD_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_]*:v[0-9]+$")


def _is_method_reference(v: Any) -> bool:
    """A gate 'method' field must cite a declared, versioned spec (e.g. 'block_bootstrap:v1'),
    not an arbitrary placeholder string like 'defined' -- that would silently invent a method.
    """
    return isinstance(v, str) and _METHOD_REFERENCE_RE.fullmatch(v) is not None


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
        "Positive USD threshold; requires data audit", _positive_number,
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
        _non_empty_str,
    ),
    "data_manifest": ParameterSpec(
        "data_manifest", "manifest ref", "open", "D12", "Coverage manifest must be produced by the data audit",
        _non_empty_str,
    ),
    "account.profile": ParameterSpec(
        "account.profile", "profile id", "open", "D09", "Requires verified Alpaca account profile",
        _non_empty_str,
    ),
    "account.quantity_step": ParameterSpec(
        "account.quantity_step", "shares", "open", "D09", "Requires verified whole/fractional share rules",
        _positive_number,
    ),
    "account.minimum_quantity": ParameterSpec(
        "account.minimum_quantity", "shares", "open", "D09", "Requires verified minimum order size",
        _nonnegative,
    ),
    "account.proceeds_availability": ParameterSpec(
        "account.proceeds_availability", "policy id", "open", "D09", "Requires verified settlement/proceeds policy",
        _non_empty_str,
    ),
    "risk.fraction_per_trade": ParameterSpec(
        "risk.fraction_per_trade", "fraction of equity", "open", "D10", "Requires confirmed per-trade risk limit",
        _fraction_0_1,
    ),
    "risk.daily_loss_limit_usd": ParameterSpec(
        "risk.daily_loss_limit_usd", "USD", "open", "D10", "Requires confirmed daily loss stop",
        _positive_number,
    ),
    "risk.drawdown_limit_fraction": ParameterSpec(
        "risk.drawdown_limit_fraction", "fraction of equity", "open", "D10", "Requires confirmed drawdown ceiling",
        _fraction_0_1,
    ),
    "risk.max_positions": ParameterSpec(
        "risk.max_positions", "count", "open", "D10", "Requires confirmed simultaneous-position limit",
        _positive_int,
    ),
    "risk.max_position_notional_usd": ParameterSpec(
        "risk.max_position_notional_usd", "USD", "open", "D10", "Requires confirmed exposure cap",
        _positive_number,
    ),
    "execution.spread_bps": ParameterSpec(
        "execution.spread_bps", "basis points", "open", "D11", "Requires confirmed cost/latency assumptions",
        _nonnegative,
    ),
    "execution.slippage_bps": ParameterSpec(
        "execution.slippage_bps", "basis points", "open", "D11", "Requires confirmed cost/latency assumptions",
        _nonnegative,
    ),
    "execution.fee_profile": ParameterSpec(
        "execution.fee_profile", "profile id", "open", "D11", "Requires confirmed fee schedule",
        _non_empty_str,
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
        _positive_int,
    ),
    "experiment.seed": ParameterSpec(
        "experiment.seed", "seed value", "open", "D16", "Requires a declared seed for reproducibility",
    ),
    # D17 promotion gates: one leaf per required field in the experiments.md "Promotion contract"
    # table, grouped to mirror that table's rows. `promotion_gates: {}` leaves every leaf at None,
    # so it is exactly as unresolved as `promotion_gates: null` -- no longer silently satisfied.
    "promotion_gates.minimum_observations.minimum_evaluation_days": ParameterSpec(
        "promotion_gates.minimum_observations.minimum_evaluation_days", "days", "open", "D17",
        "Requires frozen minimum evaluation-day count", _positive_int,
    ),
    "promotion_gates.minimum_observations.minimum_evaluation_trades": ParameterSpec(
        "promotion_gates.minimum_observations.minimum_evaluation_trades", "count", "open", "D17",
        "Requires frozen minimum evaluation-trade count", _positive_int,
    ),
    "promotion_gates.minimum_observations.dependent_observation_treatment": ParameterSpec(
        "promotion_gates.minimum_observations.dependent_observation_treatment", "method ref", "open", "D17",
        "Requires a declared, versioned treatment for dependent observations", _is_method_reference,
    ),
    "promotion_gates.net_return.net_return_requirement": ParameterSpec(
        "promotion_gates.net_return.net_return_requirement", "fraction or USD", "open", "D17",
        "Requires frozen positive net-return target (deposits excluded)", _positive_number,
    ),
    "promotion_gates.net_return.metric_definition": ParameterSpec(
        "promotion_gates.net_return.metric_definition", "method ref", "open", "D17",
        "Requires a declared, versioned net-return metric definition", _is_method_reference,
    ),
    "promotion_gates.control_improvement.control_id": ParameterSpec(
        "promotion_gates.control_improvement.control_id", "control id", "open", "D17",
        "Requires a declared control to compare against", _non_empty_str,
    ),
    "promotion_gates.control_improvement.improvement_measure": ParameterSpec(
        "promotion_gates.control_improvement.improvement_measure", "method ref", "open", "D17",
        "Requires a declared, versioned paired-improvement measure", _is_method_reference,
    ),
    "promotion_gates.drawdown.drawdown_ceiling": ParameterSpec(
        "promotion_gates.drawdown.drawdown_ceiling", "fraction of equity", "open", "D17",
        "Requires a frozen maximum permitted drawdown", _fraction_0_1,
    ),
    "promotion_gates.uncertainty.confidence_level": ParameterSpec(
        "promotion_gates.uncertainty.confidence_level", "fraction", "open", "D17",
        "Requires a frozen confidence level", _fraction_0_1,
    ),
    "promotion_gates.uncertainty.resampling_design": ParameterSpec(
        "promotion_gates.uncertainty.resampling_design", "method ref", "open", "D17",
        "Requires a declared, versioned daily/block resampling design", _is_method_reference,
    ),
    "promotion_gates.uncertainty.acceptance_criterion": ParameterSpec(
        "promotion_gates.uncertainty.acceptance_criterion", "method ref", "open", "D17",
        "Requires a declared, versioned uncertainty acceptance criterion", _is_method_reference,
    ),
    "promotion_gates.cost_stress.scenarios": ParameterSpec(
        "promotion_gates.cost_stress.scenarios", "scenario list", "open", "D17",
        "Requires at least one declared, nonblank cost/delay stress scenario", _non_empty_str_list,
    ),
    "promotion_gates.cost_stress.acceptance_criteria": ParameterSpec(
        "promotion_gates.cost_stress.acceptance_criteria", "method ref", "open", "D17",
        "Requires declared, versioned cost-stress acceptance criteria", _is_method_reference,
    ),
    "promotion_gates.concentration.limits": ParameterSpec(
        "promotion_gates.concentration.limits", "method ref", "open", "D17",
        "Requires declared, versioned symbol/period/winner concentration limits", _is_method_reference,
    ),
    "promotion_gates.concentration.diagnostics": ParameterSpec(
        "promotion_gates.concentration.diagnostics", "method ref", "open", "D17",
        "Requires declared, versioned concentration diagnostics", _is_method_reference,
    ),
    "promotion_gates.paper_observation.forward_duration_days": ParameterSpec(
        "promotion_gates.paper_observation.forward_duration_days", "days", "open", "D17",
        "Requires a frozen forward paper-observation duration", _positive_int,
    ),
    "promotion_gates.paper_observation.sample_requirement": ParameterSpec(
        "promotion_gates.paper_observation.sample_requirement", "method ref", "open", "D17",
        "Requires a declared, versioned paper-observation sample requirement", _is_method_reference,
    ),
    "promotion_gates.paper_observation.incident_handling": ParameterSpec(
        "promotion_gates.paper_observation.incident_handling", "method ref", "open", "D17",
        "Requires declared, versioned paper-observation incident handling", _is_method_reference,
    ),
    "data_adjustment.symbol_mapping_asof": ParameterSpec(
        "data_adjustment.symbol_mapping_asof", "asof ref", "open", "D13", "Requires audited historical universe mapping",
        _non_empty_str,
    ),
}


def get_spec(field_path: str) -> ParameterSpec | None:
    return REGISTRY.get(field_path)

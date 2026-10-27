"""Typed, validated configuration profiles.

Two isolated profiles exist (`mode` discriminates them):
  - FixtureConfig: synthetic-only, no market decisions required.
  - ResearchConfig: the draft market profile; every "open" registry field
    left unset is reported as a named blocker instead of silently defaulting.

Fixture and research profiles are deliberately kept as separate models
(profile isolation) so a fixture run can never inherit an unresolved
market setting, and a research run can never silently fall back to the
fixture's zero-cost test simplifications.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rsys.registry import REGISTRY


class ConfigError(Exception):
    """Raised when a config file cannot be parsed or matched to a known profile."""


class Blocker(BaseModel):
    """One unresolved or out-of-range field, named so the owner can act on it."""

    field_path: str
    decision_id: str | None
    reason: str


class ConfigValidationResult(BaseModel):
    ok: bool
    blockers: list[Blocker] = Field(default_factory=list)


# ---- Fixture profile (configs/fixture.yaml) --------------------------------

class FixtureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    mode: Literal["fixture"]
    evidence_type: str
    network_allowed: bool
    input: str
    initial_cash_default: Decimal
    quantity_step: Decimal
    price_tick: Decimal
    cash_model: str
    spread: Decimal
    slippage: Decimal
    fees: Decimal
    feed_delay_ms: int
    decision_latency_ms: int
    overrides: str
    warning: str


# ---- Research profile (configs/baseline_pending.yaml) ----------------------

class AccountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str | None = None
    quantity_step: Decimal | None = None
    minimum_quantity: Decimal | None = None
    proceeds_availability: str | None = None


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fraction_per_trade: Decimal | None = None
    daily_loss_limit_usd: Decimal | None = None
    drawdown_limit_fraction: Decimal | None = None
    max_positions: int | None = None
    max_position_notional_usd: Decimal | None = None


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spread_bps: Decimal | None = None
    slippage_bps: Decimal | None = None
    fee_profile: str | None = None
    feed_delay_ms: int | None = None
    decision_latency_ms: int | None = None
    ambiguous_exit: str


class ScannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    top_n: int
    lookback_sessions: int
    min_prior_dollar_volume: Decimal | None = None
    max_lookback_calendar_sessions: int


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    range_minutes: int
    breakout_buffer_ticks: int
    entry_cutoff_local: str
    max_fills_per_symbol_day: int


class ExitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_r: Decimal
    maximum_hold_minutes: int
    flatten_buffer_minutes: int


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    development_start: str | None = None
    development_end: str | None = None
    validation_start: str | None = None
    validation_end: str | None = None
    final_start: str | None = None
    final_end: str | None = None
    trial_budget: int | None = None
    seed: int | None = None


class DataAdjustmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution: str
    historical_features: str
    symbol_mapping_asof: str | None = None


class ReportingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ranking_storage: str
    measure_storage: bool


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int
    status: str
    mode: Literal["research"]
    hypothesis: str
    decision_refs: list[str]
    capital_usd: Decimal
    provider: str
    feed: str | None = None
    data_manifest: str | None = None
    base_interval_minutes: int
    data_session: str
    trading_session: str
    side: str
    acceptance: str
    account: AccountConfig
    risk: RiskConfig
    execution: ExecutionConfig
    scanner: ScannerConfig
    strategy: StrategyConfig
    exit_: Annotated[ExitConfig, Field(alias="exit")]
    experiment: ExperimentConfig
    promotion_gates: Any | None = None
    data_adjustment: DataAdjustmentConfig
    reporting: ReportingConfig


RsysConfig = FixtureConfig | ResearchConfig

_PROFILES: dict[str, type[BaseModel]] = {"fixture": FixtureConfig, "research": ResearchConfig}


def load_raw(path: str | Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read config file ({exc})") from exc
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level YAML must be a mapping")
    return data


def parse_config(data: dict[str, Any]) -> RsysConfig:
    mode = data.get("mode")
    model = _PROFILES.get(mode)
    if model is None:
        raise ConfigError(f"unknown or missing 'mode' field: {mode!r}; expected one of {sorted(_PROFILES)}")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def load_config(path: str | Path) -> RsysConfig:
    return parse_config(load_raw(path))


def _get_by_path(tree: dict[str, Any], dotted_path: str) -> Any:
    node: Any = tree
    for part in dotted_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def validate_config(config: RsysConfig) -> ConfigValidationResult:
    """Run registry-driven blocker/range checks. Fixture profiles have no registry-tracked fields."""
    if isinstance(config, FixtureConfig):
        return ConfigValidationResult(ok=True, blockers=[])

    tree = config.model_dump(by_alias=True, mode="json")
    blockers: list[Blocker] = []
    for spec in REGISTRY.values():
        value = _get_by_path(tree, spec.field_path)
        if spec.is_unresolved(value):
            blockers.append(Blocker(field_path=spec.field_path, decision_id=spec.decision_id, reason=spec.rationale))
            continue
        error = spec.validate(value)
        if error:
            blockers.append(Blocker(field_path=spec.field_path, decision_id=spec.decision_id, reason=error))
    blockers.sort(key=lambda b: b.field_path)
    return ConfigValidationResult(ok=len(blockers) == 0, blockers=blockers)

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

from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from rsys.registry import REGISTRY, _nonnegative, _positive_int, _positive_number


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
    evidence_type: Literal["synthetic_only"]
    network_allowed: Literal[False]
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

    @field_validator("schema_version", mode="before")
    @classmethod
    def _check_schema_version(cls, v: Any) -> Any:
        if not _positive_int(v):
            raise ValueError(f"schema_version must be a positive integer, got {v!r}")
        return v

    @field_validator("initial_cash_default", "quantity_step", "price_tick")
    @classmethod
    def _check_positive(cls, v: Decimal) -> Decimal:
        if not _positive_number(v):
            raise ValueError(f"must be a finite positive value, got {v!r}")
        return v

    @field_validator("spread", "slippage", "fees")
    @classmethod
    def _check_nonnegative_cost(cls, v: Decimal) -> Decimal:
        if not _nonnegative(v):
            raise ValueError(f"must be a finite nonnegative value, got {v!r}")
        return v

    @field_validator("feed_delay_ms", "decision_latency_ms")
    @classmethod
    def _check_nonnegative_delay(cls, v: int) -> int:
        if isinstance(v, bool) or v < 0:
            raise ValueError(f"must be a nonnegative integer, got {v!r}")
        return v


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
    # Decided behavior (contracts.md "Position and exit behavior"): ambiguous bars use stop-first.
    ambiguous_exit: Literal["stop_first"]


class ScannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Only scanner implemented in v1 (D14); a parsed ID is not proof another scanner exists.
    id: Literal["rvol_then_gain_v1"]
    top_n: int
    lookback_sessions: int
    min_prior_dollar_volume: Decimal | None = None
    max_lookback_calendar_sessions: int

    @model_validator(mode="after")
    def _check_lookback_cap(self) -> ScannerConfig:
        if self.max_lookback_calendar_sessions < self.lookback_sessions:
            raise ValueError(
                "max_lookback_calendar_sessions must be >= lookback_sessions "
                f"({self.max_lookback_calendar_sessions} < {self.lookback_sessions})"
            )
        return self


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Only strategy implemented in v1 (D15); VWAP remains unimplemented/unspecified.
    id: Literal["orb_close_cross_v1"]
    range_minutes: int
    breakout_buffer_ticks: int
    entry_cutoff_local: str
    max_fills_per_symbol_day: int

    @field_validator("entry_cutoff_local")
    @classmethod
    def _check_entry_cutoff_format(cls, v: str) -> str:
        try:
            time.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(f"entry_cutoff_local must be an HH:MM exchange-local time, got {v!r}") from exc
        # Ordering against the actual session close/flatten timer needs calendar input; deferred to M1.
        return v


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

    @field_validator(
        "development_start", "development_end", "validation_start", "validation_end", "final_start", "final_end",
    )
    @classmethod
    def _check_date_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            date.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(f"must be an ISO 8601 date (YYYY-MM-DD), got {v!r}") from exc
        return v

    @model_validator(mode="after")
    def _check_interval_ordering(self) -> ExperimentConfig:
        fields = (
            self.development_start, self.development_end,
            self.validation_start, self.validation_end,
            self.final_start, self.final_end,
        )
        if any(f is None for f in fields):
            return self  # a missing endpoint stays a named registry blocker, not a format error
        dev_start, dev_end, val_start, val_end, fin_start, fin_end = (date.fromisoformat(f) for f in fields)  # type: ignore[arg-type]
        if not (dev_start <= dev_end < val_start <= val_end < fin_start <= fin_end):
            raise ValueError(
                "experiment intervals must be chronological and non-overlapping: "
                "development_start <= development_end < validation_start <= validation_end "
                "< final_start <= final_end"
            )
        return self


class DataAdjustmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Decided units policy (D20): raw execution, as-of split-converted historical features.
    execution: Literal["raw"]
    historical_features: Literal["splits_effective_and_known_asof"]
    symbol_mapping_asof: str | None = None


class ReportingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ranking_storage: str
    measure_storage: bool


# ---- Promotion gates (D17): structured groups mirroring experiments.md's "Promotion contract" ----
# table. Every leaf is optional here; the registry reports each missing/invalid leaf as a named
# D17 blocker, so `promotion_gates: {}` or a partially filled group blocks exactly like `null`.

class MinimumObservationsGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_evaluation_days: int | None = None
    minimum_evaluation_trades: int | None = None
    dependent_observation_treatment: str | None = None


class NetReturnGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    net_return_requirement: Decimal | None = None
    metric_definition: str | None = None


class ControlImprovementGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    control_id: str | None = None
    improvement_measure: str | None = None


class DrawdownGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drawdown_ceiling: Decimal | None = None
    recovery_duration_limit_days: int | None = None  # optional per experiments.md; never blocks alone

    @field_validator("recovery_duration_limit_days")
    @classmethod
    def _check_recovery_duration(cls, v: int | None) -> int | None:
        if v is not None and not _positive_int(v):
            raise ValueError(f"recovery_duration_limit_days must be a positive integer, got {v!r}")
        return v


class UncertaintyGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confidence_level: Decimal | None = None
    resampling_design: str | None = None
    acceptance_criterion: str | None = None


class CostStressGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: list[str] | None = None
    acceptance_criteria: str | None = None


class ConcentrationGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limits: str | None = None
    diagnostics: str | None = None


class PaperObservationGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    forward_duration_days: int | None = None
    sample_requirement: str | None = None
    incident_handling: str | None = None


class PromotionGatesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_observations: MinimumObservationsGate = Field(default_factory=MinimumObservationsGate)
    net_return: NetReturnGate = Field(default_factory=NetReturnGate)
    control_improvement: ControlImprovementGate = Field(default_factory=ControlImprovementGate)
    drawdown: DrawdownGate = Field(default_factory=DrawdownGate)
    uncertainty: UncertaintyGate = Field(default_factory=UncertaintyGate)
    cost_stress: CostStressGate = Field(default_factory=CostStressGate)
    concentration: ConcentrationGate = Field(default_factory=ConcentrationGate)
    paper_observation: PaperObservationGate = Field(default_factory=PaperObservationGate)


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int
    status: str
    mode: Literal["research"]
    hypothesis: str
    decision_refs: list[str]
    capital_usd: Decimal
    # Decided markets/provider (D02): stocks/ETFs via Alpaca only in v1.
    provider: Literal["alpaca"]
    feed: str | None = None
    data_manifest: str | None = None
    base_interval_minutes: int
    # Supported v1 behavior (D06/D07): regular-session data and trading only; premarket is a later ticket.
    data_session: Literal["regular"]
    trading_session: Literal["regular"]
    # Supported v1 behavior (D06): long-only.
    side: Literal["long"]
    # Supported v1 behavior (D08): deterministic mechanical acceptance only.
    acceptance: Literal["mechanical"]
    account: AccountConfig
    risk: RiskConfig
    execution: ExecutionConfig
    scanner: ScannerConfig
    strategy: StrategyConfig
    exit_: Annotated[ExitConfig, Field(alias="exit")]
    experiment: ExperimentConfig
    promotion_gates: PromotionGatesConfig | None = None
    data_adjustment: DataAdjustmentConfig
    reporting: ReportingConfig

    @field_validator("schema_version", mode="before")
    @classmethod
    def _check_schema_version(cls, v: Any) -> Any:
        if not _positive_int(v):
            raise ValueError(f"schema_version must be a positive integer, got {v!r}")
        return v

    @field_validator("capital_usd")
    @classmethod
    def _check_capital_positive(cls, v: Decimal) -> Decimal:
        if not _positive_number(v):
            raise ValueError(f"capital_usd must be a finite positive value, got {v!r}")
        return v


RsysConfig = FixtureConfig | ResearchConfig

_PROFILES: dict[str, type[BaseModel]] = {"fixture": FixtureConfig, "research": ResearchConfig}


def load_raw(path: str | Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read config file ({exc})") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        raise ConfigError(f"{path}: malformed YAML{location}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level YAML must be a mapping")
    return data


def _format_validation_error(exc: ValidationError) -> str:
    """Field paths and messages only; never echoes raw input values (may contain secrets)."""
    parts = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return "; ".join(parts)


def parse_config(data: dict[str, Any]) -> RsysConfig:
    mode = data.get("mode")
    if not isinstance(mode, str):
        raise ConfigError(f"'mode' field must be a string; got {type(mode).__name__}")
    model = _PROFILES.get(mode)
    if model is None:
        raise ConfigError(f"unknown mode: {mode!r}; expected one of {sorted(_PROFILES)}")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc


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

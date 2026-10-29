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

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from rsys.calendar import (
    CALENDAR_VERSION,
    CalendarCoverageError,
    session_close_utc,
    session_open_utc,
)
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.registry import REGISTRY, _nonnegative, _positive_int, _positive_number

# Implemented schema versions only; a parsed, in-range integer is not proof a newer/older
# schema is actually supported by this codebase.
_IMPLEMENTED_SCHEMA_VERSIONS = frozenset({1})

# `bool` is an `int` subclass, so pydantic's default (lax) coercion silently turns a
# boolean into 0/1 for any int/Decimal field. Reject it *before* that coercion happens,
# so counts, budgets, seeds, delays and money fields can't be satisfied by `true`/`false`.


def _reject_bool(v: Any) -> Any:
    if isinstance(v, bool):
        raise ValueError("boolean values are not accepted for this numeric field")  # noqa: TRY004 (pydantic only converts ValueError/AssertionError)
    return v


NonBoolInt = Annotated[int, BeforeValidator(_reject_bool)]
OptionalNonBoolInt = Annotated[int | None, BeforeValidator(_reject_bool)]
NonBoolDecimal = Annotated[Decimal, BeforeValidator(_reject_bool)]
OptionalNonBoolDecimal = Annotated[Decimal | None, BeforeValidator(_reject_bool)]

# Offset-free HH:MM, 24-hour, exchange-local time (strategy.entry_cutoff_local).
_LOCAL_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


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
    initial_cash_default: NonBoolDecimal
    quantity_step: NonBoolDecimal
    price_tick: NonBoolDecimal
    cash_model: str
    spread: NonBoolDecimal
    slippage: NonBoolDecimal
    fees: NonBoolDecimal
    feed_delay_ms: NonBoolInt
    decision_latency_ms: NonBoolInt
    overrides: str
    warning: str

    @field_validator("schema_version", mode="before")
    @classmethod
    def _check_schema_version(cls, v: Any) -> Any:
        if isinstance(v, bool) or v not in _IMPLEMENTED_SCHEMA_VERSIONS:
            raise ValueError("schema_version must be one of the implemented schema versions")
        return v

    @field_validator("initial_cash_default", "quantity_step", "price_tick")
    @classmethod
    def _check_positive(cls, v: Decimal) -> Decimal:
        if not _positive_number(v):
            raise ValueError("must be a finite positive value")
        return v

    @field_validator("spread", "slippage", "fees")
    @classmethod
    def _check_nonnegative_cost(cls, v: Decimal) -> Decimal:
        if not _nonnegative(v):
            raise ValueError("must be a finite nonnegative value")
        return v

    @field_validator("feed_delay_ms", "decision_latency_ms")
    @classmethod
    def _check_nonnegative_delay(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be a nonnegative integer")
        return v


# ---- Research profile (configs/baseline_pending.yaml) ----------------------

class AccountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str | None = None
    quantity_step: OptionalNonBoolDecimal = None
    minimum_quantity: OptionalNonBoolDecimal = None
    proceeds_availability: str | None = None


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fraction_per_trade: OptionalNonBoolDecimal = None
    daily_loss_limit_usd: OptionalNonBoolDecimal = None
    drawdown_limit_fraction: OptionalNonBoolDecimal = None
    max_positions: OptionalNonBoolInt = None
    max_position_notional_usd: OptionalNonBoolDecimal = None


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spread_bps: OptionalNonBoolDecimal = None
    slippage_bps: OptionalNonBoolDecimal = None
    fee_profile: str | None = None
    feed_delay_ms: OptionalNonBoolInt = None
    decision_latency_ms: OptionalNonBoolInt = None
    # Decided behavior (contracts.md "Position and exit behavior"): ambiguous bars use stop-first.
    ambiguous_exit: Literal["stop_first"]


class ScannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Only scanner implemented in v1 (D14); a parsed ID is not proof another scanner exists.
    id: Literal["rvol_then_gain_v1"]
    top_n: NonBoolInt
    lookback_sessions: NonBoolInt
    min_prior_dollar_volume: OptionalNonBoolDecimal = None
    max_lookback_calendar_sessions: NonBoolInt

    @model_validator(mode="after")
    def _check_lookback_cap(self) -> ScannerConfig:
        if self.max_lookback_calendar_sessions < self.lookback_sessions:
            raise ValueError("max_lookback_calendar_sessions must be >= lookback_sessions")
        return self


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Only strategy implemented in v1 (D15); VWAP remains unimplemented/unspecified.
    id: Literal["orb_close_cross_v1"]
    range_minutes: NonBoolInt
    breakout_buffer_ticks: NonBoolInt
    entry_cutoff_local: str
    max_fills_per_symbol_day: NonBoolInt

    @field_validator("entry_cutoff_local")
    @classmethod
    def _check_entry_cutoff_format(cls, v: str) -> str:
        if not _LOCAL_TIME_RE.fullmatch(v):
            raise ValueError("entry_cutoff_local must be an offset-free HH:MM exchange-local time")
        # Ordering against the actual session close/flatten timer needs calendar input; deferred to M1.
        return v


class ExitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_r: NonBoolDecimal
    maximum_hold_minutes: NonBoolInt
    flatten_buffer_minutes: NonBoolInt


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    development_start: str | None = None
    development_end: str | None = None
    validation_start: str | None = None
    validation_end: str | None = None
    final_start: str | None = None
    final_end: str | None = None
    trial_budget: OptionalNonBoolInt = None
    seed: OptionalNonBoolInt = None

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
            raise ValueError("must be an ISO 8601 date (YYYY-MM-DD)") from exc
        return v

    @model_validator(mode="after")
    def _check_interval_ordering(self) -> ExperimentConfig:
        """Enforce every chronological relationship whose two endpoints are both known,
        even while an unrelated endpoint elsewhere is still an unresolved blocker."""
        adjacent_pairs = (
            (self.development_start, self.development_end, False),
            (self.development_end, self.validation_start, True),
            (self.validation_start, self.validation_end, False),
            (self.validation_end, self.final_start, True),
            (self.final_start, self.final_end, False),
        )
        for left, right, strict in adjacent_pairs:
            if left is None or right is None:
                continue
            left_date, right_date = date.fromisoformat(left), date.fromisoformat(right)
            in_order = left_date < right_date if strict else left_date <= right_date
            if not in_order:
                raise ValueError(
                    "experiment date endpoints must be chronological and non-overlapping: "
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
    minimum_evaluation_days: OptionalNonBoolInt = None
    minimum_evaluation_trades: OptionalNonBoolInt = None
    dependent_observation_treatment: str | None = None


class NetReturnGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    net_return_requirement: OptionalNonBoolDecimal = None
    metric_definition: str | None = None


class ControlImprovementGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    control_id: str | None = None
    improvement_measure: str | None = None


class DrawdownGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drawdown_ceiling: OptionalNonBoolDecimal = None
    recovery_duration_limit_days: OptionalNonBoolInt = None  # optional; never blocks alone

    @field_validator("recovery_duration_limit_days")
    @classmethod
    def _check_recovery_duration(cls, v: int | None) -> int | None:
        if v is not None and not _positive_int(v):
            raise ValueError("recovery_duration_limit_days must be a positive integer")
        return v


class UncertaintyGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confidence_level: OptionalNonBoolDecimal = None
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
    forward_duration_days: OptionalNonBoolInt = None
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
    capital_usd: NonBoolDecimal
    # Decided markets/provider (D02): stocks/ETFs via Alpaca only in v1.
    provider: Literal["alpaca"]
    feed: str | None = None
    data_manifest: str | None = None
    base_interval_minutes: NonBoolInt
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
        if isinstance(v, bool) or v not in _IMPLEMENTED_SCHEMA_VERSIONS:
            raise ValueError("schema_version must be one of the implemented schema versions")
        return v

    @field_validator("capital_usd")
    @classmethod
    def _check_capital_positive(cls, v: Decimal) -> Decimal:
        if not _positive_number(v):
            raise ValueError("capital_usd must be a finite positive value")
        return v


# ---- Replay profile (R3-A): validated, offline-synthetic replay settings ----
# A legacy fixture (mode: fixture) need not become a replay fixture; this is a distinct,
# isolated profile with its own required fields (docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md 7.1).

class ReplayInputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Unsupported modes fail explicitly (pydantic Literal) rather than silently defaulting.
    input_mode: Literal["completed_bar", "historical_trade", "recorded_event"]
    ordering_profile: Literal["bar_v1", "trade_replay_v1"]
    # Never label reconstructed/modeled timing as captured-live.
    availability_model: Literal["recorded_receipt", "modeled_availability"]
    modeled_delay_ms: OptionalNonBoolInt = None

    @model_validator(mode="after")
    def _timing_model(self):
        if self.availability_model == "modeled_availability":
            if self.modeled_delay_ms is None or self.modeled_delay_ms < 0:
                raise ValueError("modeled timing requires an explicit nonnegative modeled_delay_ms")
        elif self.modeled_delay_ms is not None:
            raise ValueError("recorded timing must not declare a modeled delay")
        return self


class BarsAuthorityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Exactly one authoritative source per series.
    authority: Literal["provider_publication", "trade_builder"]
    rule_set_id: Literal["synthetic_test_fixture_rules:v1", "fixture_rules_v1"]


class IntervalPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval: Literal["1Min"]
    # Supported v1 behavior (D06/D07): regular-session data only.
    session: Literal["regular"]
    # Matches the decided units policy (D20) already enforced by DataAdjustmentConfig.
    unit_policy: Literal["raw"]


class RevisionRetentionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The only implemented version-selection semantics (C06's fix): never recompute-and-backdate.
    revision_policy: Literal["latest_version_asof"]
    retention_limit: OptionalNonBoolInt = None
    unavailable_history_policy: Literal["typed_unavailable"]

    @field_validator("retention_limit")
    @classmethod
    def _check_retention_limit(cls, v: int | None) -> int | None:
        if v is not None and not _positive_int(v):
            raise ValueError("retention_limit must be a positive integer when set")
        return v


class ObservationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["closed_only", "intrabar"]
    # Unsupported per-trade/quote cadence is rejected here, not silently downgraded.
    cadence: Literal["completed_bar_boundary", "availability_batch"]
    observer_id: Literal["none", "synthetic_threshold:v1", "synthetic_closed_recorder:v1"] = "none"
    threshold: OptionalNonBoolDecimal = None
    on_revisions: bool = False

    @model_validator(mode="after")
    def _check_mode_cadence_compatibility(self) -> ObservationConfig:
        if self.mode == "closed_only" and self.cadence != "completed_bar_boundary":
            raise ValueError("observation.mode=closed_only requires cadence=completed_bar_boundary")
        if self.observer_id == "synthetic_threshold:v1":
            if self.mode != "intrabar" or self.threshold is None or not _positive_number(self.threshold):
                raise ValueError("synthetic threshold observer requires intrabar mode and a positive threshold")
        elif self.threshold is not None:
            raise ValueError("threshold is only supported by the synthetic threshold observer")
        if self.observer_id == "synthetic_closed_recorder:v1" and self.mode != "closed_only":
            raise ValueError("synthetic closed recorder requires closed_only mode")
        return self


class QuoteSettingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    freshness_limit_ms: OptionalNonBoolInt = None
    quality_policy: Literal["synthetic_test_quote_policy:v1"] | None = None

    @model_validator(mode="after")
    def _check_enabled_requires_explicit_policy(self) -> QuoteSettingsConfig:
        if self.enabled and (self.freshness_limit_ms is None or self.quality_policy is None):
            raise ValueError(
                "quotes.enabled=true requires an explicit freshness_limit_ms and quality_policy "
                "(disabled is a supported complete configuration; do not silently default)"
            )
        if self.freshness_limit_ms is not None and not _positive_int(self.freshness_limit_ms):
            raise ValueError("freshness_limit_ms must be a positive integer when set")
        return self


class ReplayExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Recorded as explicitly inactive in R3 (7.1); later work distinguishes bar/subsequent-trade
    # execution bases.
    data_basis: Literal["inactive"]


class CoverageConfig(BaseModel):
    """Declared single-session replay window. Source completeness is checked separately."""
    model_config = ConfigDict(extra="forbid")
    required_boundary_name: Literal["window_end", "session_close"]
    required_boundary_at: str
    window_start_at: str
    available_through_at: str
    session_version: Literal[CALENDAR_VERSION]
    provider: str
    feed: str
    instruments: tuple[str, ...]

    @model_validator(mode="after")
    def _window(self):
        start = parse_utc_timestamp_ns(self.window_start_at)
        end = parse_utc_timestamp_ns(self.required_boundary_at)
        through = parse_utc_timestamp_ns(self.available_through_at)
        if not start < end <= through:
            raise ValueError("coverage requires start < boundary <= available_through")
        minute = 60_000_000_000
        if start % minute or end % minute:
            raise ValueError("coverage window must align to minute boundaries")
        day = datetime.fromtimestamp(start // 1_000_000_000, UTC).date()
        try:
            opening = parse_utc_timestamp_ns(session_open_utc(day).isoformat())
            closing = parse_utc_timestamp_ns(session_close_utc(day).isoformat())
        except CalendarCoverageError as exc:
            raise ValueError("coverage date is outside the supported trading calendar") from exc
        if not opening <= start < end <= closing:
            raise ValueError("coverage window must lie within one regular exchange session")
        if self.required_boundary_name == "session_close" and end != closing:
            raise ValueError("session_close must equal the exchange session close; use window_end for fixtures")
        if not self.provider.strip() or not self.feed.strip():
            raise ValueError("coverage provider and feed are required")
        if not self.instruments or any(not i.strip() for i in self.instruments) or len(set(self.instruments)) != len(self.instruments):
            raise ValueError("coverage instruments must be nonblank and unique")
        return self


class ReplayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    mode: Literal["replay"]
    evidence_type: Literal["synthetic_only"]
    network_allowed: Literal[False]
    input: str
    replay: ReplayInputConfig
    bars: BarsAuthorityConfig
    interval_policy: IntervalPolicyConfig
    revision: RevisionRetentionConfig
    observation: ObservationConfig
    quotes: QuoteSettingsConfig
    execution: ReplayExecutionConfig
    coverage: CoverageConfig

    @field_validator("schema_version", mode="before")
    @classmethod
    def _check_schema_version(cls, v: Any) -> Any:
        if isinstance(v, bool) or v not in _IMPLEMENTED_SCHEMA_VERSIONS:
            raise ValueError("schema_version must be one of the implemented schema versions")
        return v

    @model_validator(mode="after")
    def _check_input_mode_matches_authority(self) -> ReplayConfig:
        """IR-04: a completed-bar input stream has no trades to build from, and a trade stream
        has no provider publications to compare against -- input_mode and bars.authority must
        describe the SAME actual data, not merely parse as individually-valid strings."""
        compatible = {
            "completed_bar": "provider_publication",
            "historical_trade": "trade_builder",
        }
        expected = compatible.get(self.replay.input_mode)
        if expected is not None and self.bars.authority != expected:
            raise ValueError(
                f"replay.input_mode={self.replay.input_mode!r} requires bars.authority="
                f"{expected!r}, not {self.bars.authority!r}"
            )
        if self.replay.input_mode in ("historical_trade", "recorded_event") and self.replay.ordering_profile != "trade_replay_v1":
            raise ValueError("trade/recorded-event replay requires trade_replay_v1")
        if self.replay.input_mode == "completed_bar" and self.quotes.enabled:
            raise ValueError("quote streams require recorded_event input mode")
        if self.quotes.enabled and self.replay.ordering_profile != "trade_replay_v1":
            raise ValueError("quotes require trade_replay_v1")
        if self.observation.mode == "intrabar" and self.bars.authority != "trade_builder":
            raise ValueError("forming-bar observation requires trade_builder authority")
        return self


RsysConfig = FixtureConfig | ResearchConfig | ReplayConfig

_PROFILES: dict[str, type[BaseModel]] = {
    "fixture": FixtureConfig, "research": ResearchConfig, "replay": ReplayConfig,
}


def load_raw(path: str | Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read config file ({exc})") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: file is not valid UTF-8") from exc
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
    config = parse_config(load_raw(path))
    if isinstance(config, ReplayConfig):
        source = Path(config.input)
        if not source.is_absolute():
            config.input = str((Path(path).resolve().parent / source).resolve())
    return config


def _get_by_path(tree: dict[str, Any], dotted_path: str) -> Any:
    node: Any = tree
    for part in dotted_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def validate_config(config: RsysConfig) -> ConfigValidationResult:
    """Run registry-driven blocker/range checks. Fixture and replay profiles are offline
    synthetic by construction and have no registry-tracked fields."""
    if isinstance(config, (FixtureConfig, ReplayConfig)):
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

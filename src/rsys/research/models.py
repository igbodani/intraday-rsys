"""Research data models and their intrinsic validation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr


@dataclass(frozen=True)
class SyntheticRunAssumptions:
    universe: tuple[str, ...]
    cost_profile: str
    risk_profile: str
    execution_mode: str
    initial_cash: str
    session_date: str
    market_data_hash: str
    scanner_profile: str
    strategies: str
    code_hash: str
    dependency_lock_hash: str
    evidence_basis: str


@dataclass(frozen=True)
class CompletedTrade:
    symbol: str
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    pnl: Decimal


@dataclass(frozen=True)
class Metrics:
    starting_equity: Decimal
    ending_equity: Decimal
    net_change: Decimal
    realized_pnl: Decimal
    completed_trade_count: int
    win_count: int
    loss_count: int
    win_rate: Decimal | str
    expectancy: Decimal | str
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal | str


@dataclass(frozen=True)
class RunAssumptions:
    """The shared assumptions two runs must match before a diagnostic comparison is
    meaningful (docs/M2_IMPLEMENTATION_HANDOFF.md step 9 "Comparison must reject
    unexplained differences")."""

    universe: tuple[str, ...]
    cost_profile: str
    risk_profile: str
    execution_mode: str
    date_window: tuple[str, str]


@dataclass(frozen=True)
class PartitionManifest:
    """One declared, frozen segment of data. `sealed=True` marks the final holdout."""

    partition_id: str
    paths: tuple[str, ...]
    content_hash: str
    sealed: bool


@dataclass
class AccessLogEntry:
    partition_id: str
    path: str
    purpose: str
    allowed: bool


@dataclass(frozen=True)
class FinalEvaluateBlockedResult:
    blocked: bool
    missing_requirements: tuple[str, ...]


@dataclass(frozen=True)
class SessionEquityPoint:
    session_date: object  # None identifies starting capital before the first session
    ending_equity: Decimal


@dataclass(frozen=True)
class DrawdownResult:
    """`max_drawdown` is the largest peak-to-trough decline (in money) across the
    chronological session-end equity curve; `max_drawdown_pct` divides it by the peak at
    which it occurred and is `UNAVAILABLE_NONPOSITIVE_PEAK` if that peak is not positive.
    An empty curve reports zero drawdown with an explicit unavailable percentage, never a
    fabricated zero-is-good percentage."""

    equity_curve: tuple[SessionEquityPoint, ...]
    max_drawdown: Decimal
    max_drawdown_pct: Decimal | str
    peak_equity: Decimal
    trough_equity: Decimal


@dataclass(frozen=True)
class ExposureResult:
    """Time-weighted fraction of scheduled session time with at least one open position.
    Overlapping intervals across symbols are unioned (merged), never summed, so exposure
    can never exceed 1 even when several positions are open at once."""

    open_ms: int
    total_scheduled_ms: int
    fraction: Decimal | str


@dataclass(frozen=True)
class MultiSessionMetrics:
    session_count: int
    trade_metrics: Metrics
    drawdown: DrawdownResult
    exposure: ExposureResult


RunStatus = Literal["pending", "running", "success", "failed", "interrupted", "crashed"]


class RunRecord(BaseModel):
    """Minimum run record per docs/contracts.md's CLI target section."""

    run_id: str
    hypothesis: str
    phase: str
    status: RunStatus
    parent_run_id: str | None = None
    resolved_config: dict[str, Any]
    config_hash: str
    data_hash: str | None = None
    code_hash: str | None = None  # content fingerprint over the hashed source manifest
    code_manifest: list[str] = Field(
        default_factory=list
    )  # relative paths the fingerprint covers
    code_revision: str | None = (
        None  # git commit + dirty-flag metadata (informational, not the fingerprint)
    )
    dependency_lock_hash: str | None = None
    seed: int | None = None
    account_cost_profile: str | None = None
    started_at: str
    ended_at: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    decision_gate_status: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    owner_pid: int | None = None
    owner_host: str | None = None

    # In-memory only (never serialized): raw secret-field values found in the
    # original resolved_config, used to scrub them out of later error text.
    _secret_values: list[str] = PrivateAttr(default_factory=list)
    # Whether `_secret_values` was actually populated from a resolved_config in this process
    # (True, even if that config happened to contain zero secrets) vs. simply defaulted to
    # empty because the record was reloaded from disk (False, genuinely unknown) -- C04.
    _secret_values_known: bool = PrivateAttr(default=False)

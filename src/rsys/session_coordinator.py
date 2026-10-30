"""Multi-session composition without state leakage (Step 4 of
docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md).

A thin coordinator around the already-tested per-session `StandaloneRunner`. It carries
only persistent account/settlement state (`Portfolio`) across sessions; daily scanner,
strategy, one-fill and timer state is rebuilt from scratch by a fresh `StandaloneRunner`
(and a caller-supplied `strategy_factory`) every session -- nothing from one session's ORB
range, VWAP sums, fill counters, acceptance book or pending intents can leak into the next.

Only an exact-ratio split, fully known before its effective session, is a supported
corporate-action transformation (matching the existing `fixture_engine.py` F17 contract and
docs/data_rules.md). A late-known action or one that would produce a fractional quantity
under the declared step is an explicit `UnsupportedCorporateActionError`, never a rounded or
invented transfer. An open position at the start of a new session is an explicit
`UnreconciledPositionError`: it cannot silently carry overnight.

Market settlement and fractional-quantity account semantics beyond this exact-split case
remain blocked on D09 (docs/decisions.md); this module does not attempt them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from rsys.calendar import (
    CALENDAR_VERSION,
    CalendarCoverageError,
    is_trading_day,
    session_open_utc,
)
from rsys.money import amount, ceil_step, floor_step
from rsys.portfolio import Portfolio
from rsys.standalone_run import (
    DecisionRecord,
    ExecutionPolicy,
    FillRecord,
    IncompleteSessionError,
    SizingPolicy,
    StandaloneRunner,
    StrategyLike,
)

SPLIT_TICK = Decimal("0.01")  # matches strategies/orb.py's declared tick convention


class UnsupportedCorporateActionError(ValueError):
    """A corporate-action transformation whose units/known-at timing are not fully
    specified and supported (docs/data_rules.md); the affected session must retain this
    explicit result rather than silently continuing."""


class UnreconciledPositionError(ValueError):
    """An open position was still held when a new session began. It cannot silently carry
    overnight without an explicit, supported reconciliation."""


@dataclass(frozen=True)
class SplitAction:
    """A declared, fully-specified split: `ratio = new_shares / old_shares`, effective for
    `effective_date`'s session, known no later than that session's open."""

    symbol: str
    ratio: Decimal
    effective_date: date
    known_at_ns: int


@dataclass(frozen=True)
class SessionResult:
    session_date: date
    session_open_ns: int
    decisions: list[DecisionRecord]
    fills: list[FillRecord]
    contexts: list
    ending_equity: Decimal


class SessionCoordinator:
    def __init__(
        self,
        strategy_factory: Callable[[date], dict[str, StrategyLike]],
        portfolio: Portfolio,
        sizing: SizingPolicy,
        *,
        execution_factory: Callable[[date], ExecutionPolicy] | None = None,
        top_n: int = 20,
    ):
        self.strategy_factory = strategy_factory
        self.portfolio = portfolio
        self.sizing = sizing
        self.execution_factory = execution_factory or (lambda _session_date: ExecutionPolicy())
        self.top_n = top_n
        self.results: list[SessionResult] = []

    def apply_split(self, action: SplitAction) -> None:
        """Apply `action` to any currently open position in `action.symbol`. Raises
        `UnsupportedCorporateActionError` for a late-known action or a resulting
        fractional quantity; does nothing if no position is open in that symbol."""
        amount(action.ratio, "split ratio", positive=True)
        session_open_ns = int(session_open_utc(action.effective_date).timestamp() * 1_000_000_000)
        if action.known_at_ns > session_open_ns:
            raise UnsupportedCorporateActionError(
                f"split for {action.symbol!r} known at {action.known_at_ns} after its "
                f"effective session open {session_open_ns}: late-known actions are unsupported"
            )
        position = self.portfolio.positions.get(action.symbol)
        if position is None:
            return
        step = self.sizing.quantity_step
        new_quantity = position.quantity * action.ratio
        if (new_quantity / step).to_integral_value() * step != new_quantity:
            raise UnsupportedCorporateActionError(
                f"split for {action.symbol!r} produces a fractional quantity under step "
                f"{step}: cash-in-lieu is not an implemented policy (D09)"
            )
        position.quantity = new_quantity
        position.entry_price = position.entry_price / action.ratio
        if position.stop is not None:
            position.stop = floor_step(position.stop / action.ratio, SPLIT_TICK)
        if position.target is not None:
            position.target = ceil_step(position.target / action.ratio, SPLIT_TICK)
        position.mark = position.mark / action.ratio
        # `basis` (total cash paid) and all portfolio cash fields are unchanged by a
        # consistent price/quantity conversion (docs/data_rules.md).

    def run_session(
        self,
        session_date: date,
        bars_by_symbol: dict[str, list],
        scanner_inputs: dict[int, dict],
    ) -> StandaloneRunner:
        """Run one calendar session. Raises `CalendarCoverageError` for a non-trading day,
        `UnreconciledPositionError` if a position survived from a prior session, and
        whatever `StandaloneRunner.run_session` itself raises (e.g.
        `IncompleteSessionError`) for this session's own execution data."""
        if not is_trading_day(session_date):
            raise CalendarCoverageError(
                f"{session_date.isoformat()} is not a trading day under {CALENDAR_VERSION}"
            )
        if self.portfolio.positions:
            raise UnreconciledPositionError(
                f"{session_date.isoformat()} cannot start a new session with open "
                f"position(s) {sorted(self.portfolio.positions)} still carried from a "
                "prior session"
            )
        strategies = self.strategy_factory(session_date)
        execution = self.execution_factory(session_date)
        runner = StandaloneRunner(
            strategies,
            self.portfolio,
            self.sizing,
            execution=execution,
            top_n=self.top_n,
            session_date=session_date.isoformat(),
        )
        runner.run_session(bars_by_symbol, scanner_inputs)
        self.results.append(
            SessionResult(
                session_date=session_date,
                session_open_ns=runner.origin_ns,
                decisions=list(runner.decisions),
                fills=list(runner.fills),
                contexts=list(runner.contexts),
                ending_equity=runner.portfolio.equity({}),
            )
        )
        return runner


__all__ = [
    "IncompleteSessionError",
    "SessionCoordinator",
    "SessionResult",
    "SplitAction",
    "UnreconciledPositionError",
    "UnsupportedCorporateActionError",
]

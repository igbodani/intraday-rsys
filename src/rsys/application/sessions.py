"""Multi-session composition without state leakage (Step 4 of
docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md).

A thin coordinator around the already-tested per-session `BarSessionRunner`. It carries
persistent account/settlement state (`Portfolio`) and optional account risk/peak state across sessions; daily scanner,
strategy, one-fill and timer state is rebuilt from scratch by a fresh `BarSessionRunner`
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
from datetime import date
from decimal import Decimal

from rsys.account.models import SizingPolicy
from rsys.account.money import amount, ceil_step, floor_step
from rsys.account.portfolio import Portfolio
from rsys.account.risk import RiskMonitor
from rsys.application.errors import (
    UnreconciledPositionError,
    UnsupportedCorporateActionError,
)
from rsys.application.models import SessionResult, SplitAction
from rsys.data.calendar import CALENDAR_VERSION, is_trading_day, session_open_utc
from rsys.data.errors import CalendarCoverageError
from rsys.execution.bar import BarSessionRunner
from rsys.execution.errors import IncompleteSessionError
from rsys.execution.models import ExecutionCosts, ExecutionPolicy
from rsys.strategies.interfaces import StrategyLike

SPLIT_TICK = Decimal("0.01")  # matches strategies/orb.py's declared tick convention


class SessionCoordinator:
    def __init__(
        self,
        strategy_factory: Callable[[date], dict[str, StrategyLike]],
        portfolio: Portfolio,
        sizing: SizingPolicy,
        *,
        execution_factory: Callable[[date], ExecutionPolicy] | None = None,
        top_n: int = 20,
        runner_factory: Callable = BarSessionRunner,
        risk: RiskMonitor | None = None,
        costs: ExecutionCosts | None = None,
    ):
        self.strategy_factory = strategy_factory
        self.portfolio = portfolio
        self.sizing = sizing
        self.execution_factory = execution_factory or (
            lambda _session_date: ExecutionPolicy()
        )
        self.top_n = top_n
        self.runner_factory = runner_factory
        self.risk = risk
        self.costs = costs
        self.last_runner = None
        self.results: list[SessionResult] = []
        self._last_attempted_date: date | None = None
        self._failed = False
        self._last_available_ns: int | None = None

    def apply_split(self, action: SplitAction) -> None:
        """Apply `action` to any currently open position in `action.symbol`. Raises
        `UnsupportedCorporateActionError` for a late-known action or a resulting
        fractional quantity; does nothing if no position is open in that symbol."""
        amount(action.ratio, "split ratio", positive=True)
        session_open_ns = int(
            session_open_utc(action.effective_date).timestamp() * 1_000_000_000
        )
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
        corrections_by_symbol: dict | None = None,
    ) -> BarSessionRunner:
        """Run one calendar session. Raises `CalendarCoverageError` for a non-trading day,
        `UnreconciledPositionError` if a position survived from a prior session, and
        whatever `BarSessionRunner.run_session` itself raises (e.g.
        `IncompleteSessionError`) for this session's own execution data."""
        if not is_trading_day(session_date):
            raise CalendarCoverageError(
                f"{session_date.isoformat()} is not a trading day under {CALENDAR_VERSION}"
            )
        if (
            self._last_attempted_date is not None
            and session_date <= self._last_attempted_date
        ):
            raise ValueError(
                "session dates must be strictly increasing; duplicate sessions are forbidden"
            )
        if self._failed:
            raise ValueError(
                "a failed session requires a fresh coordinator and reconciled account"
            )
        opening_ns = int(session_open_utc(session_date).timestamp()) * 1_000_000_000
        if (
            self._last_available_ns is not None
            and self._last_available_ns >= opening_ns
        ):
            raise IncompleteSessionError(
                "prior session execution clock overlaps the next session open"
            )
        if self.portfolio.positions:
            raise UnreconciledPositionError(
                f"{session_date.isoformat()} cannot start a new session with open "
                f"position(s) {sorted(self.portfolio.positions)} still carried from a "
                "prior session"
            )
        strategies = self.strategy_factory(session_date)
        execution = self.execution_factory(session_date)
        runner = self.runner_factory(
            strategies,
            self.portfolio,
            self.sizing,
            execution=execution,
            top_n=self.top_n,
            session_date=session_date.isoformat(),
            risk=self.risk,
            costs=self.costs,
        )
        self.last_runner = runner
        # A runtime failure may have committed account transitions. Never silently retry
        # or advance that partially executed account as a successful session.
        self._last_attempted_date = session_date
        try:
            if corrections_by_symbol is None:
                runner.run_session(bars_by_symbol, scanner_inputs)
            else:
                runner.run_session(
                    bars_by_symbol, scanner_inputs, corrections_by_symbol
                )
        except BaseException:
            self._failed = True
            raise
        if runner.trace:
            self._last_available_ns = runner.trace[-1]["available_at_ns"]
        self.results.append(
            SessionResult(
                session_date=session_date,
                session_open_ns=int(session_open_utc(session_date).timestamp())
                * 1_000_000_000,
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

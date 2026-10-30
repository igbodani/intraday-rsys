"""Explicit synthetic account risk/cost policies; no provider or promotion defaults."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from rsys.account.models import RiskPolicy
from rsys.account.money import amount


class RiskMonitor:
    """One experiment's account lifetime. Daily latch resets; account latch never does.

    Observe only published account marks and committed fees/fills. A halt blocks entries,
    releases pending reservations and preserves exits. It cannot guarantee a loss ceiling
    across gaps. Starting a new CLI attempt starts a separate experimental account.
    """

    def __init__(self, policy: RiskPolicy, initial_equity: Decimal):
        amount(initial_equity, "initial equity", positive=True)
        self.policy = policy
        self.peak_equity = initial_equity
        self.session_start_equity = initial_equity
        self.current_equity = initial_equity
        self.session_date: str | None = None
        self.daily_halted = False
        self.account_halted = False

    def begin_session(self, session_date: str, equity: Decimal):
        if self.session_date is not None and session_date <= self.session_date:
            raise ValueError("risk sessions must be strictly increasing")
        self.session_date = session_date
        self.session_start_equity = equity
        self.daily_halted = False
        self.observe(equity)

    def observe(self, equity: Decimal) -> str | None:
        if not equity.is_finite():
            raise ValueError("nonfinite account equity")
        self.current_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        if equity <= self.session_start_equity * (1 - self.policy.daily_loss_fraction):
            self.daily_halted = True
        if equity <= self.peak_equity * (1 - self.policy.maximum_drawdown_fraction):
            self.account_halted = True
        return self.halt_reason

    @property
    def halt_reason(self) -> str | None:
        if self.account_halted:
            return "ACCOUNT_DRAWDOWN_HALT"
        return "DAILY_LOSS_HALT" if self.daily_halted else None

    @property
    def budget(self) -> Decimal:
        return max(Decimal(0), self.current_equity * self.policy.per_trade_fraction)

    def snapshot(self):
        return {
            "policy": asdict(self.policy),
            "session_date": self.session_date,
            "session_start_equity": self.session_start_equity,
            "peak_equity": self.peak_equity,
            "current_equity": self.current_equity,
            "daily_halted": self.daily_halted,
            "account_halted": self.account_halted,
            "halt_reason": self.halt_reason,
            "per_trade_budget": self.budget,
        }


class RiskManagedRunner:
    """Shared risk boundary for bar/trade runners; policy-free legacy runs use None."""

    def _begin_risk_session(self):
        if self.risk is not None:
            self.risk.begin_session(self.session_date, self.portfolio.equity({}))

    def _risk_budget(self):
        if self.risk is None:
            return self.sizing.risk_budget
        self.risk.observe(self.portfolio.equity({}))
        return self.risk.budget

    def _enforce_risk(self, point):
        if self.risk is None:
            return None
        reason = self.risk.observe(self.portfolio.equity({}))
        if reason:
            for symbol in list(self.pending_intents):
                self._cancel(symbol, point, reason)
        return reason

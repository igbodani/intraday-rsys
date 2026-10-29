"""End-to-end standalone integration: scanner ranking -> ORB/VWAP strategy proposals ->
`PhaseEngine`-ordered acceptance -> next-bar-open entry fill -> protective stop/target exits
(gap and intrabar) and max-hold/flatten timers, using `rsys.portfolio`, `rsys.sizing` and
`rsys.acceptance` for the account side.

Honesty note: this proves proposal -> acceptance -> entry-fill -> exit composition across
the standalone modules built so far. It does NOT model splits, corporate actions or
subsequent-trade execution (M2-01T); it is not wired into `ReplayRunner` or real market
data; and it is still a standalone scheduler, not an extension of the existing replay
runtime -- see docs/M2_IMPLEMENTATION_STATUS.md.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Protocol

from rsys.acceptance import AcceptanceBook, sort_proposals
from rsys.orders import AcceptedIntent, Proposal
from rsys.phase_engine import PhaseEngine, PhaseEvent
from rsys.portfolio import AcceptanceError, Portfolio
from rsys.scanner import CandidateInput, rank_candidates
from rsys.sizing import size_quantity


class CompletedBarLike(Protocol):
    available_at_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class StrategyLike(Protocol):
    def on_completed_bar(self, bar: Any, *, is_candidate: bool) -> Proposal | None: ...
    def on_filled(self, entry_fill: Decimal) -> Decimal: ...
    def on_rejected_or_expired(self) -> None: ...
    def on_position_closed(self) -> None: ...


@dataclass
class DecisionRecord:
    available_at_ms: int
    candidates: list[str]
    proposed: list[str]
    accepted: list[str]
    rejected: dict[str, str]


@dataclass
class FillRecord:
    available_at_ms: int
    symbol: str
    status: str
    price: Decimal | None = None
    quantity: Decimal | None = None
    target: Decimal | None = None


@dataclass
class SizingPolicy:
    risk_budget: Decimal | None = None
    max_quantity: Decimal | None = None
    quantity_step: Decimal = Decimal(1)
    entry_fee: Decimal = Decimal(0)
    exit_fee: Decimal = Decimal(0)


@dataclass
class ExecutionPolicy:
    """`flatten_at_ms` is an absolute exchange-local ms-of-day; `max_hold_ms` is a duration
    from each position's own fill time."""

    max_hold_ms: int | None = None
    flatten_at_ms: int | None = None


class StandaloneRunner:
    def __init__(
        self, strategies: dict[str, StrategyLike], portfolio: Portfolio, sizing: SizingPolicy, *,
        execution: ExecutionPolicy | None = None, top_n: int = 20,
    ):
        self.strategies = strategies
        self.portfolio = portfolio
        self.sizing = sizing
        self.execution = execution or ExecutionPolicy()
        self.top_n = top_n
        self.book = AcceptanceBook()
        self.latest_bars: dict[str, CompletedBarLike] = {}
        self.pending_intents: dict[str, AcceptedIntent] = {}
        self.entry_times: dict[str, int] = {}
        self.max_hold_deadlines: dict[str, int] = {}
        self.decisions: list[DecisionRecord] = []
        self.fills: list[FillRecord] = []
        self.engine = PhaseEngine(
            on_settle=lambda _event: None, on_publish=self._on_publish,
            on_decision=self._on_decision, on_fill=self._on_fill,
        )

    def run_session(
        self, bars_by_symbol: dict[str, list[CompletedBarLike]], scanner_inputs: dict[int, dict[str, tuple[Decimal, Decimal]]],
    ) -> None:
        """`scanner_inputs[available_at_ms][symbol] = (rvol, gain)`; a symbol with no entry
        at a given timestamp is excluded from candidacy at that decision, never defaulted."""
        bars_by_time: dict[int, dict[str, CompletedBarLike]] = {}
        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                bars_by_time.setdefault(bar.available_at_ms, {})[symbol] = bar

        for timestamp in sorted(bars_by_time):
            self.engine.enqueue(PhaseEvent(
                f"publish-{timestamp}", timestamp, 20, "publish", None, {"bars": bars_by_time[timestamp]},
            ))
            self.engine.enqueue(PhaseEvent(f"fill-{timestamp}", timestamp, 70, "fill", None, {}))
            self.engine.enqueue(PhaseEvent(
                f"decide-{timestamp}", timestamp, 40, "decision", None,
                {"scanner": scanner_inputs.get(timestamp, {})},
            ))
        self.engine.run()

    def _on_publish(self, event: PhaseEvent) -> None:
        for symbol, bar in event.payload["bars"].items():
            self.latest_bars[symbol] = bar

    def _on_decision(self, event: PhaseEvent, _frozen_view: dict[str, Any]) -> DecisionRecord:
        scanner_rows = [
            CandidateInput(symbol, rvol, gain) for symbol, (rvol, gain) in event.payload["scanner"].items()
        ]
        scan = rank_candidates(scanner_rows, top_n=self.top_n)
        rank_by_symbol = {symbol: rank for rank, symbol in enumerate(scan.top, start=1)}

        proposals: list[Proposal] = []
        for symbol, strategy in self.strategies.items():
            bar = self.latest_bars.get(symbol)
            if bar is None or bar.available_at_ms != event.available_at_ns:
                continue  # no publish for this symbol at this instant
            proposal = strategy.on_completed_bar(bar, is_candidate=symbol in rank_by_symbol)
            if proposal is not None:
                proposals.append(replace(proposal, scanner_rank=rank_by_symbol.get(symbol)))

        accepted, rejected = [], {}
        for proposal in sort_proposals(proposals):
            symbol = proposal.symbol
            try:
                quantity, _risk = size_quantity(
                    entry=proposal.signal_price, stop=proposal.stop, entry_fee=self.sizing.entry_fee,
                    exit_fee=self.sizing.exit_fee, risk_budget=self.sizing.risk_budget,
                    available_cash=self.portfolio.available_cash, quantity_step=self.sizing.quantity_step,
                    max_quantity=self.sizing.max_quantity,
                )
            except AcceptanceError as exc:
                rejected[symbol] = exc.reason
                self.strategies[symbol].on_rejected_or_expired()
                continue
            sized = replace(proposal, quantity=quantity)
            needed_cash = quantity * proposal.signal_price + self.sizing.entry_fee
            try:
                intent = self.book.accept(self.portfolio, sized, needed_cash)
            except AcceptanceError as exc:
                rejected[symbol] = exc.reason
                self.strategies[symbol].on_rejected_or_expired()
                continue
            self.pending_intents[symbol] = intent
            accepted.append(symbol)

        record = DecisionRecord(
            available_at_ms=event.available_at_ns, candidates=list(scan.top),
            proposed=[p.symbol for p in proposals], accepted=accepted, rejected=rejected,
        )
        self.decisions.append(record)
        return record

    def _check_exit(self, symbol: str, position: Any, bar: CompletedBarLike) -> Decimal | None:
        """Scheduled flatten/max-hold exits first, then protective gap exits, then intrabar
        touches; stop wins over target when both are touched in the same bar."""
        flatten_at = self.execution.flatten_at_ms
        if flatten_at is not None and bar.available_at_ms >= flatten_at:
            return bar.open
        max_hold_at = self.max_hold_deadlines.get(symbol)
        if max_hold_at is not None and bar.available_at_ms >= max_hold_at:
            return bar.open
        if position.stop is not None and bar.open <= position.stop:
            return bar.open
        if position.target is not None and bar.open >= position.target:
            return position.target
        if position.stop is not None and bar.low <= position.stop:
            return position.stop
        if position.target is not None and bar.high >= position.target:
            return position.target
        return None

    def _on_fill(self, _event: PhaseEvent) -> list[FillRecord]:
        results = []
        for symbol, position in list(self.portfolio.positions.items()):
            bar = self.latest_bars.get(symbol)
            if bar is None or bar.available_at_ms <= self.entry_times.get(symbol, -1):
                continue  # never exit on the bar that created the position
            exit_price = self._check_exit(symbol, position, bar)
            if exit_price is None:
                continue
            quantity = position.quantity
            self.portfolio.sell(symbol, exit_price, self.sizing.exit_fee)
            self.strategies[symbol].on_position_closed()
            del self.entry_times[symbol]
            self.max_hold_deadlines.pop(symbol, None)
            record = FillRecord(bar.available_at_ms, symbol, "EXIT", exit_price, quantity)
            self.fills.append(record)
            results.append(record)

        for symbol in list(self.pending_intents):
            intent = self.pending_intents[symbol]
            proposal = intent.proposal
            bar = self.latest_bars.get(symbol)
            if bar is None or bar.available_at_ms <= proposal.decision_time_ms:
                continue  # no eligible later open yet: never fill at the signal's own bar
            del self.pending_intents[symbol]
            if bar.open <= proposal.stop:
                self.portfolio.release_reservation(intent.reserved_cash)
                self.strategies[symbol].on_rejected_or_expired()
                record = FillRecord(bar.available_at_ms, symbol, "ENTRY_THROUGH_STOP")
            else:
                self.portfolio.buy(
                    symbol, proposal.quantity, bar.open, self.sizing.entry_fee, intent.reserved_cash,
                    proposal.stop, proposal.target,
                )
                target = self.strategies[symbol].on_filled(bar.open)
                self.portfolio.positions[symbol].target = target
                self.entry_times[symbol] = bar.available_at_ms
                if self.execution.max_hold_ms is not None:
                    self.max_hold_deadlines[symbol] = bar.available_at_ms + self.execution.max_hold_ms
                record = FillRecord(bar.available_at_ms, symbol, "FILLED", bar.open, proposal.quantity, target)
            self.fills.append(record)
            results.append(record)
        return results

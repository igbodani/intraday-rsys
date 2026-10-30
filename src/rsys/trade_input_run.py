"""Trade-input synthetic execution mode (M2-01T, Step 2 of
docs/CURRENT_SYSTEM_AND_NEXT_STEPS.md).

A second, separately labeled single-session account path alongside `standalone_run.py`.
Decisions still happen on trade-built completed bars (`SeriesRouter`/`ReplayRunner` with
`authority="trade_builder"`), reusing the same scanner/strategy/acceptance machinery -- but a
fill always waits for a genuinely later, eligible trade print, via the isolated
`subsequent_trade_estimate_v1` adapter in `trade_execution.py`. `rsys replay` stays
data-only; this module adds a new synthetic account path, it does not change that one.

Execution mode identifier: `subsequent_trade_estimate_v1` (`TradeInputRunner.EXECUTION_MODE`).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal

from rsys.acceptance import AcceptanceBook, sort_proposals
from rsys.calendar import to_exchange_utc
from rsys.data.bar_rules import BarRuleSet, FixtureBarRuleSet
from rsys.data.events import BarCloseEvent, TradeCorrectionEvent, TradeEvent
from rsys.execution_contract import (
    RejectedEntry,
    commit_entry,
    commit_exit,
    decide_bar_open_entry,
    from_trade_outcome,
)
from rsys.orders import AcceptedIntent
from rsys.phase_engine import PhaseEvent
from rsys.portfolio import AcceptanceError, Portfolio
from rsys.replay import ReplayProfile
from rsys.replay_runtime import ReplayRunner, SeriesRouter
from rsys.scanner import CandidateInput, rank_candidates
from rsys.sizing import size_quantity
from rsys.standalone_run import (
    DecisionRecord,
    ExecutionPolicy,
    FillRecord,
    IncompleteSessionError,
    SizingPolicy,
    StrategyLike,
)
from rsys.strategies.orb import CompletedBar
from rsys.trade_execution import Trade, TradeExecutionAdapter

__all__ = [
    "DecisionRecord",
    "ExecutionPolicy",
    "FillRecord",
    "IncompleteSessionError",
    "SizingPolicy",
    "TradeInputRunner",
]

_EXIT_LABELS = {"stop": "STOP_TRADE", "target": "TARGET_TRADE"}


class TradeInputRunner:
    """Single-session, single-use: raw per-symbol trade prints in, a reconciled account
    and trace out. Unlike `StandaloneRunner`, an entry/exit never fires at a bar open; it
    fires on the first later trade the adapter accepts as an eligible reference."""

    EXECUTION_MODE = "subsequent_trade_estimate_v1"

    def __init__(
        self,
        strategies: dict[str, StrategyLike],
        portfolio: Portfolio,
        sizing: SizingPolicy,
        *,
        execution: ExecutionPolicy | None = None,
        top_n: int = 20,
        session_date: str = "2024-11-29",
        feed: str = "fixture",
        interval: str = "1Min",
        rules: BarRuleSet | None = None,
    ):
        self.strategies = strategies
        self.portfolio = portfolio
        self.sizing = sizing
        self.execution = execution or ExecutionPolicy()
        self.top_n = top_n
        self.feed = feed
        self.interval = interval
        self.rules = rules or FixtureBarRuleSet()
        self.origin_ns = (
            int(to_exchange_utc(datetime.fromisoformat(session_date)).timestamp())
            * 1_000_000_000
        )
        self.book = AcceptanceBook(reservation_namespace=str(self.origin_ns))
        self.pending_intents: dict[str, AcceptedIntent] = {}
        self.consumed_trade_ids: dict[str, frozenset[str]] = {}
        self.entry_times: dict[str, int] = {}
        self.exit_due: set[str] = set()
        self.exit_reasons: dict[str, str] = {}
        self.exit_requested_at: dict[str, int] = {}
        self.adapters: dict[str, TradeExecutionAdapter] = {}
        self.decisions: list[DecisionRecord] = []
        self.fills: list[FillRecord] = []
        self.trace: list[dict] = []
        self.contexts = []
        self._decision_points: set[int] = set()
        self._trade_batches: dict[int, list[TradeEvent]] = {}
        self._ran = False

    def _ns(self, ms):
        return self.origin_ns + ms * 1_000_000

    def _ms(self, ns):
        return (ns - self.origin_ns) // 1_000_000

    def _event(self, identity, ms, phase, kind, symbol=None, payload=None):
        return PhaseEvent(identity, self._ns(ms), phase, kind, symbol, payload or {})

    def _adapter(self, symbol: str) -> TradeExecutionAdapter:
        if symbol not in self.adapters:
            self.adapters[symbol] = TradeExecutionAdapter(
                self.sizing.entry_fee, self.sizing.exit_fee
            )
        return self.adapters[symbol]

    def run_session(self, trades_by_symbol, scanner_inputs, corrections_by_symbol=None):
        if self._ran:
            raise ValueError("a runner is single-session and single-use")
        self._ran = True
        self.scanner_inputs = scanner_inputs
        sources = []
        for symbol in sorted(trades_by_symbol):
            previous = None
            for trade in trades_by_symbol[symbol]:
                if getattr(trade, "is_correction", False):
                    raise ValueError("correction inputs must use corrections_by_symbol with an original trade ID")
                if previous is not None and trade.event_time_ms <= previous:
                    raise ValueError("duplicate or unordered trade event times")
                previous = trade.event_time_ms
                sources.append(
                    TradeEvent(
                        event_id=f"{symbol}:{trade.trade_id}",
                        instrument_id=symbol,
                        provider="synthetic",
                        feed=self.feed,
                        event_time_ns=self._ns(trade.event_time_ms),
                        available_at_ns=self._ns(trade.available_at_ms),
                        event_time_meaning="provider_market_time",
                        session_version="nyse-2024-v1",
                        quality_state="ok" if getattr(trade, "quality_ok", True) else "invalid",
                        timing_label="modeled",
                        price=trade.price,
                        size=getattr(trade, "size", Decimal(1)),
                        trade_id=trade.trade_id,
                        venue="synthetic",
                        conditions=getattr(trade, "conditions", ()),
                        tape=getattr(trade, "tape", None),
                    )
                )
        for symbol, corrections in (corrections_by_symbol or {}).items():
            for correction in corrections:
                sources.append(
                    TradeCorrectionEvent(
                        event_id=f"corr:{symbol}:{correction['original_trade_id']}:"
                        f"{correction['available_at_ms']}",
                        instrument_id=symbol,
                        provider="synthetic",
                        feed=self.feed,
                        event_time_ns=self._ns(correction["available_at_ms"]),
                        available_at_ns=self._ns(correction["available_at_ms"]),
                        event_time_meaning="provider_market_time",
                        session_version="nyse-2024-v1",
                        quality_state="ok",
                        timing_label="modeled",
                        original_trade_id=correction["original_trade_id"],
                        action=correction.get("action", "cancel"),
                        corrected_price=correction.get("corrected_price"),
                        corrected_size=correction.get("corrected_size"),
                        conditions=correction.get("conditions"),
                        tape=correction.get("tape"),
                    )
                )
        router = SeriesRouter(self.feed, self.interval, self.rules)
        self.router = router
        window_end_ns = max((e.available_at_ns for e in sources), default=self.origin_ns)
        if self.execution.flatten_at_ms is not None:
            window_end_ns = max(window_end_ns, self._ns(self.execution.flatten_at_ms))
        self.replay = ReplayRunner(
            sources,
            ReplayProfile.TRADE_REPLAY_V1,
            router,
            authority="trade_builder",
            window_end_ns=window_end_ns,
        )
        self.engine = self.replay.domain_engine(self._dispatch)
        if self.execution.flatten_at_ms is not None:
            self.engine.enqueue(
                self._event("flatten", self.execution.flatten_at_ms, 30, "flatten")
            )
        self.engine.run()
        if self.exit_due & self.portfolio.positions.keys():
            raise IncompleteSessionError(
                "required flatten/max-hold exit has no eligible subsequent trade"
            )
        if self.pending_intents:
            raise IncompleteSessionError(
                "pending intent has no expiry or execution data"
            )

    def _snapshot(self):
        p = self.portfolio
        return {
            "total_cash": p.total_cash,
            "spendable_cash": p.spendable_cash,
            "unsettled_cash": p.unsettled_cash,
            "reserved_cash": p.reserved_cash,
            "equity": p.equity({}),
            "realized_pnl": p.realized_pnl,
            "positions": {s: asdict(v) for s, v in sorted(p.positions.items())},
        }

    def _dispatch(self, event):
        point = self._ms(event.available_at_ns)
        before = len(self.fills)
        result = None
        if event.kind == "replay_source":
            source = event.payload["source"]
            if isinstance(source, TradeEvent):
                result = self._on_trade(source, point)
            elif isinstance(source, TradeCorrectionEvent):
                self._on_correction(source)
            elif isinstance(source, BarCloseEvent) and point not in self._decision_points:
                self._decision_points.add(point)
                self.engine.enqueue(self._event(f"decision:{point}", point, 40, "decision"))
        elif event.kind == "decision":
            result = self._decide(event, point)
        elif event.kind == "trade_exits":
            self._execute_trades(point, entries=False)
        elif event.kind == "trade_entries":
            self._execute_trades(point, entries=True)
            del self._trade_batches[point]
        elif event.kind == "accept":
            result = self._accept(
                event.payload["proposals"], event.payload["record"], point
            )
        elif event.kind == "expiry":
            intent = self.pending_intents.get(event.symbol)
            if (
                intent is not None
                and intent.reservation_id == event.payload["reservation_id"]
            ):
                self._cancel(event.symbol, point, "EXPIRED")
        elif event.kind == "flatten":
            self.exit_due.update(self.portfolio.positions)
            for symbol in self.portfolio.positions:
                self.exit_reasons.setdefault(symbol, "FLATTEN")
                self.exit_requested_at.setdefault(symbol, point)
            for symbol in list(self.pending_intents):
                self._cancel(symbol, point, "FLATTEN")
        elif event.kind == "max_hold":
            if self.entry_times.get(event.symbol) == event.payload["entry_time"]:
                self.exit_due.add(event.symbol)
                self.exit_reasons.setdefault(event.symbol, "MAX_HOLD")
                self.exit_requested_at.setdefault(event.symbol, point)
        self.portfolio.assert_invariants()
        row = {
            "kind": event.kind,
            "event_id": event.event_id,
            "available_at_ns": event.available_at_ns,
            "phase": event.phase,
            "available_at_ms": point,
            "symbol": event.symbol,
            "account": self._snapshot(),
        }
        if isinstance(result, DecisionRecord):
            row.update(asdict(result))
        self.trace.append(row)
        for fill in self.fills[before:]:
            self.trace.append(
                {
                    "kind": "fill",
                    "event_id": event.event_id,
                    "available_at_ns": event.available_at_ns,
                    "phase": event.phase,
                    **asdict(fill),
                    "account": self._snapshot(),
                }
            )
        return result

    def _on_trade(self, source: TradeEvent, point: int) -> None:
        # Apply all publications before decisions, and all exits before rank-ordered
        # entries. Source dispatch order must not become portfolio acceptance priority.
        if point not in self._trade_batches:
            self._trade_batches[point] = []
            self.engine.enqueue(self._event(f"trade-exits:{point}", point, 60, "trade_exits"))
            self.engine.enqueue(self._event(f"trade-entries:{point}", point, 70, "trade_entries"))
        self._trade_batches[point].append(source)

    def _execute_trades(self, point: int, *, entries: bool) -> None:
        sources = self._trade_batches[point]
        symbols = (
            [p.symbol for p in sort_proposals([i.proposal for i in self.pending_intents.values()])]
            if entries else sorted(self.portfolio.positions)
        )
        for symbol in symbols:
            for source in sources:
                if source.instrument_id != symbol:
                    continue
                trade = Trade(
                    source.trade_id, self._ms(source.event_time_ns), point, source.price,
                    quality_ok=source.quality_state == "ok", size=source.size,
                    conditions=source.conditions, tape=source.tape,
                )
                if entries and symbol in self.pending_intents:
                    self._attempt_entry(symbol, trade, point)
                elif not entries and symbol in self.portfolio.positions:
                    self.portfolio.mark_position(symbol, source.price)
                    self._attempt_exit(symbol, trade, point)

    def _on_correction(self, source: TradeCorrectionEvent) -> None:
        self._adapter(source.instrument_id).apply_correction(source.original_trade_id)

    def _decide(self, event: PhaseEvent, point: int) -> DecisionRecord:
        available_at_ns = event.available_at_ns
        context = self.replay.context_after(event.sort_key, intrabar=False)
        self.contexts.append(context)
        scan = rank_candidates(
            [CandidateInput(s, *v) for s, v in self.scanner_inputs.get(point, {}).items()],
            top_n=self.top_n,
        )
        ranks = {s: i for i, s in enumerate(scan.top, 1)}
        proposals = []
        for symbol, strategy in sorted(self.strategies.items()):
            bars = context.closed_bars(symbol)
            if not bars or bars[-1].available_at_ns != available_at_ns:
                continue
            self.consumed_trade_ids[symbol] = frozenset(
                trade_id for b in bars for trade_id in b.source_event_ids
            )
            source = bars[-1]
            bar = CompletedBar(
                self._ms(source.bar_start_ns),
                point,
                source.open,
                source.high,
                source.low,
                source.close,
                source.volume,
            )
            proposal = strategy.on_completed_bar(bar, is_candidate=symbol in ranks)
            if proposal is not None:
                proposals.append(
                    replace(
                        proposal,
                        scanner_rank=ranks.get(symbol),
                        decision_latency_ms=self.execution.decision_latency_ms,
                    )
                )
        record = DecisionRecord(
            point,
            list(scan.top),
            [p.symbol for p in proposals],
            [],
            {},
            context.input_references(),
            list(scan.ranked),
            dict(scan.excluded),
        )
        self.decisions.append(record)
        if proposals:
            self.engine.enqueue(
                self._event(
                    f"accept:{point}",
                    point,
                    50,
                    "accept",
                    payload={"proposals": proposals, "record": record},
                )
            )
        return record

    def _accept(self, proposals, record, point):
        for p in sort_proposals(proposals):
            try:
                if (
                    self.execution.flatten_at_ms is not None
                    and point >= self.execution.flatten_at_ms
                ):
                    raise AcceptanceError("ENTRY_WINDOW_CLOSED")
                if p.symbol in self.pending_intents:
                    raise AcceptanceError("DUPLICATE_PENDING_OR_HELD")
                qty, _ = self._quantity(
                    p,
                    p.signal_price,
                    self.portfolio.available_cash,
                    self.sizing.max_quantity,
                )
                p = replace(p, quantity=qty)
                intent = self.book.accept(
                    self.portfolio, p, qty * p.signal_price + self.sizing.entry_fee
                )
            except AcceptanceError as exc:
                record.rejected[p.symbol] = exc.reason
                self.strategies[p.symbol].on_rejected_or_expired()
                continue
            self.pending_intents[p.symbol] = intent
            record.accepted.append(p.symbol)
            if p.expires_at_ms is not None:
                self.engine.enqueue(
                    self._event(
                        f"expiry:{intent.reservation_id}",
                        p.expires_at_ms,
                        30,
                        "expiry",
                        p.symbol,
                        {"reservation_id": intent.reservation_id},
                    )
                )
        self.decisions[-1] = record
        return record

    def _quantity(self, p, price, cash, maximum):
        return size_quantity(
            entry=price,
            stop=p.stop,
            entry_fee=self.sizing.entry_fee,
            exit_fee=self.sizing.exit_fee,
            risk_budget=self.sizing.risk_budget,
            available_cash=cash,
            quantity_step=self.sizing.quantity_step,
            max_quantity=maximum,
        )

    def _cancel(self, symbol, point, reason):
        intent = self.pending_intents.pop(symbol)
        self.portfolio.release_reservation(intent.reserved_cash, intent.reservation_id)
        self.strategies[symbol].on_rejected_or_expired()
        self.fills.append(FillRecord(point, symbol, reason, reason=reason))

    def _attempt_entry(self, symbol, trade: Trade, point: int) -> None:
        intent = self.pending_intents[symbol]
        proposal = intent.proposal
        adapter = deepcopy(self._adapter(symbol))
        consumed = self.consumed_trade_ids.get(symbol, frozenset())
        outcome = adapter.consider_entry(intent, consumed, trade)
        if outcome.status != "FILLED":
            return
        decision = decide_bar_open_entry(
            proposal,
            point,
            outcome.price,
            Decimal(0),
            self.portfolio.available_cash + intent.reserved_cash,
            entry_fee=self.sizing.entry_fee,
            exit_fee=self.sizing.exit_fee,
            risk_budget=self.sizing.risk_budget,
            quantity_step=self.sizing.quantity_step,
        )
        if isinstance(decision, RejectedEntry):
            self._cancel(symbol, point, decision.reason)
            return
        _, qty = decision
        transition = from_trade_outcome(
            outcome,
            instrument=symbol,
            intent_identity=intent.reservation_id,
            activation_key=f"{symbol}:{self._ns(point)}",
            availability_key=point,
            market_time_ms=trade.event_time_ms,
            quantity=qty,
        )
        transition = replace(
            transition, execution_reference_id=f"entry:{self.origin_ns}:{symbol}:{trade.trade_id}"
        )
        commit_entry(
            self.portfolio,
            transition,
            stop=proposal.stop,
            target=None,
            reserved_cash=intent.reserved_cash,
        )
        self.adapters[symbol] = adapter
        target = self.strategies[symbol].on_filled(transition.price)
        self.portfolio.positions[symbol].target = target
        del self.pending_intents[symbol]
        self.entry_times[symbol] = point
        self.fills.append(
            FillRecord(
                point,
                symbol,
                "FILLED",
                transition.price,
                qty,
                target,
                self.sizing.entry_fee,
                event_time_ms=trade.event_time_ms,
            )
        )
        if self.execution.max_hold_ms is not None:
            self.engine.enqueue(
                self._event(
                    f"hold:{symbol}:{point}",
                    point + self.execution.max_hold_ms,
                    30,
                    "max_hold",
                    symbol,
                    {"entry_time": point},
                )
            )

    def _attempt_exit(self, symbol, trade: Trade, point: int) -> None:
        adapter = self._adapter(symbol)
        position = self.portfolio.positions[symbol]
        if symbol in self.exit_due:
            outcome = adapter.force_exit(
                trade, self.exit_reasons[symbol], requested_at_ms=self.exit_requested_at[symbol]
            )
        else:
            outcome = adapter.consider_exit(position, trade)
        if outcome.status != "FILLED":
            return
        transition = from_trade_outcome(
            outcome,
            instrument=symbol,
            intent_identity=f"position:{symbol}",
            activation_key=f"{symbol}:{self._ns(self.entry_times[symbol])}",
            availability_key=point,
            market_time_ms=trade.event_time_ms,
            quantity=position.quantity,
        )
        transition = replace(
            transition, execution_reference_id=f"exit:{self.origin_ns}:{symbol}:{trade.trade_id}"
        )
        qty = position.quantity
        pnl = transition.price * qty - transition.fee - position.basis
        commit_exit(self.portfolio, transition)
        self.strategies[symbol].on_position_closed()
        self.entry_times.pop(symbol)
        self.exit_due.discard(symbol)
        self.exit_reasons.pop(symbol, None)
        self.exit_requested_at.pop(symbol, None)
        self.fills.append(
            FillRecord(
                point,
                symbol,
                "EXIT",
                transition.price,
                qty,
                fee=transition.fee,
                net_pnl=pnl,
                reason=_EXIT_LABELS.get(outcome.reason, outcome.reason),
                event_time_ms=trade.event_time_ms,
            )
        )

"""Single-session synthetic bar execution on ReplayRunner's bounded source views.

An open is a modeled execution observation at bar_start, carrying only its price.
Interval extrema settle at publication; they never enter an earlier decision view.
This is not a provider adapter or a trade-replay execution implementation.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal

from rsys.account.acceptance import AcceptanceBook, sort_proposals
from rsys.account.errors import AcceptanceError
from rsys.account.models import AcceptedIntent, SizingPolicy
from rsys.account.portfolio import Portfolio
from rsys.account.risk import RiskManagedRunner, RiskMonitor
from rsys.account.sizing import size_quantity
from rsys.data.calendar import to_exchange_utc
from rsys.data.models import ProviderBarEvent
from rsys.execution.contracts import (
    commit_entry,
    commit_exit,
    decide_bar_open_entry,
    decide_bar_open_gap_exit,
)
from rsys.execution.errors import IncompleteSessionError
from rsys.execution.models import (
    DecisionRecord,
    ExecutionCosts,
    ExecutionPolicy,
    FillRecord,
    FillTransition,
    RejectedEntry,
)
from rsys.replay.models import PhaseEvent, ReplayProfile
from rsys.replay.runtime import ReplayRunner
from rsys.strategies.interfaces import StrategyLike
from rsys.strategies.models import CandidateInput, CompletedBar
from rsys.strategies.scanner import rank_candidates


class BarSessionRunner(RiskManagedRunner):
    def __init__(
        self,
        strategies: dict[str, StrategyLike],
        portfolio: Portfolio,
        sizing: SizingPolicy,
        *,
        execution: ExecutionPolicy | None = None,
        top_n: int = 20,
        session_date: str = "2024-11-29",
        risk: RiskMonitor | None = None,
        costs: ExecutionCosts | None = None,
    ):
        self.strategies = strategies
        self.portfolio = portfolio
        self.sizing = sizing
        self.execution = execution or ExecutionPolicy()
        self.top_n = top_n
        self.session_date = session_date
        self.risk = risk
        self.costs = costs or ExecutionCosts()
        self.origin_ns = (
            int(to_exchange_utc(datetime.fromisoformat(session_date)).timestamp())
            * 1_000_000_000
        )
        self.book = AcceptanceBook(reservation_namespace=str(self.origin_ns))
        self.pending_intents: dict[str, AcceptedIntent] = {}
        self.entry_times: dict[str, int] = {}
        self.exit_due: set[str] = set()
        self.exit_requested_at: dict[str, int] = {}
        self.decisions: list[DecisionRecord] = []
        self.fills: list[FillRecord] = []
        self.trace: list[dict] = []
        self.contexts = []
        self._intervals: dict[str, int] = {}
        self._ran = False

    def _ns(self, ms):
        return self.origin_ns + ms * 1_000_000

    def _ms(self, ns):
        return (ns - self.origin_ns) // 1_000_000

    def _event(self, identity, ms, phase, kind, symbol=None, payload=None):
        return PhaseEvent(identity, self._ns(ms), phase, kind, symbol, payload or {})

    def run_session(self, bars_by_symbol, scanner_inputs):
        if self._ran:
            raise ValueError("a runner is single-session and single-use")
        self._ran = True
        self._begin_risk_session()
        sources, domain = [], []
        opens = {}
        times = set()
        for symbol in sorted(bars_by_symbol):
            previous = None
            for bar in bars_by_symbol[symbol]:
                bar = replace(
                    bar, available_at_ms=bar.available_at_ms + self.costs.feed_delay_ms
                )
                if previous is not None and bar.bar_start_ms <= previous:
                    raise ValueError("duplicate or unordered bar intervals")
                previous = bar.bar_start_ms
                identity = f"{symbol}:{bar.bar_start_ms}"
                source = ProviderBarEvent(
                    event_id=identity,
                    instrument_id=symbol,
                    provider="synthetic",
                    feed="fixture",
                    event_time_ns=self._ns(bar.bar_start_ms),
                    available_at_ns=self._ns(bar.available_at_ms),
                    event_time_meaning="provider_market_time",
                    session_version="nyse-2024-v1",
                    quality_state="ok",
                    timing_label="modeled",
                    interval="1Min",
                    bar_start_ns=self._ns(bar.bar_start_ms),
                    bar_end_ns=self._ns(bar.bar_start_ms + 60_000),
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    adjustment_mode="raw",
                )
                sources.append(source)
                times.add(bar.available_at_ms)
                domain.append(
                    self._event(
                        f"settle:{identity}",
                        bar.available_at_ms,
                        10,
                        "interval_settle",
                        symbol,
                        {"bar": bar},
                    )
                )
                # The open events deliberately contain no close/high/low or future reference.
                open_point = bar.bar_start_ms + self.costs.feed_delay_ms
                opens.setdefault(open_point, {})[symbol] = bar.open
                for phase, kind in ((60, "open_exit"),):
                    domain.append(
                        self._event(
                            f"{kind}:{identity}",
                            open_point,
                            phase,
                            kind,
                            symbol,
                            {"open": bar.open},
                        )
                    )
        for point, prices in sorted(opens.items()):
            domain.append(
                self._event(
                    f"open_entries:{point}",
                    point,
                    70,
                    "open_entries",
                    payload={"opens": prices},
                )
            )
        for point in sorted(times):
            domain.append(
                self._event(
                    f"decision:{point}",
                    point,
                    40,
                    "decision",
                    payload={
                        "scanner": scanner_inputs.get(
                            point - self.costs.feed_delay_ms, {}
                        )
                    },
                )
            )
        if self.execution.flatten_at_ms is not None:
            domain.append(
                self._event("flatten", self.execution.flatten_at_ms, 30, "flatten")
            )
        self.replay = ReplayRunner(sources, ReplayProfile.BAR_V1)
        self.engine = self.replay.domain_engine(self._dispatch)
        for event in domain:
            self.engine.enqueue(event)
        self.engine.run()
        if self.exit_due & self.portfolio.positions.keys():
            raise IncompleteSessionError(
                "required flatten/max-hold exit has no eligible subsequent open"
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
            bar = event.payload["source"]
            if event.symbol in self.portfolio.positions:
                self.portfolio.mark_position(event.symbol, bar.close)
        elif event.kind == "interval_settle":
            self._settle(event.symbol, event.payload["bar"])
        elif event.kind == "decision":
            result = self._decide(event, point)
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
                self.exit_requested_at.setdefault(symbol, point)
            for symbol in list(self.pending_intents):
                self._cancel(symbol, point, "FLATTEN")
        elif event.kind == "max_hold":
            if self.entry_times.get(event.symbol) == event.payload["entry_time"]:
                self.exit_due.add(event.symbol)
                self.exit_requested_at.setdefault(event.symbol, point)
        elif event.kind == "open_exit":
            self._open_exit(event.symbol, point, event.payload["open"])
        elif event.kind == "open_entries":
            prices = event.payload["opens"]
            symbols = sorted(
                prices,
                key=lambda s: (
                    self.pending_intents[s].acceptance_sequence
                    if s in self.pending_intents
                    else 2**63,
                    s,
                ),
            )
            for symbol in symbols:
                self._open_entry(symbol, point, prices[symbol])
        self._enforce_risk(point)
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
        if self.risk is not None:
            row["risk"] = self.risk.snapshot()
        if event.kind == "decision":
            row["scanner_inputs"] = event.payload["scanner"]
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

    def _decide(self, event, point):
        context = self.replay.context_after(event.sort_key, intrabar=False)
        self.contexts.append(context)
        scan = rank_candidates(
            [CandidateInput(s, *v) for s, v in event.payload["scanner"].items()],
            top_n=self.top_n,
        )
        ranks = {s: i for i, s in enumerate(scan.top, 1)}
        proposals = []
        for symbol, strategy in sorted(self.strategies.items()):
            bars = context.closed_bars(symbol)
            if not bars or bars[-1].available_at_ns != event.available_at_ns:
                continue
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
            # The acceptance callback owns a copied record; update the public decision there.
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
                if reason := self._enforce_risk(point):
                    raise AcceptanceError(reason)
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
                    self.portfolio,
                    p,
                    qty * self.costs.buy_price(p.signal_price) + self.sizing.entry_fee,
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
            entry=self.costs.buy_price(price),
            stop=self.costs.sell_price(p.stop),
            entry_fee=self.sizing.entry_fee,
            exit_fee=self.sizing.exit_fee,
            risk_budget=self._risk_budget(),
            available_cash=cash,
            quantity_step=self.sizing.quantity_step,
            max_quantity=maximum,
        )

    def _cancel(self, symbol, point, reason):
        intent = self.pending_intents.pop(symbol)
        self.portfolio.release_reservation(intent.reserved_cash, intent.reservation_id)
        self.strategies[symbol].on_rejected_or_expired()
        self.fills.append(FillRecord(point, symbol, reason, reason=reason))

    def _open_entry(self, symbol, point, price):
        self._enforce_risk(point)
        market_point = point - self.costs.feed_delay_ms
        intent = self.pending_intents.get(symbol)
        if intent is not None:
            p = intent.proposal
            if market_point < p.ready_at_ms:
                return
            if p.expires_at_ms is not None and point >= p.expires_at_ms:
                self._cancel(symbol, point, "EXPIRED")
            else:
                risk_budget = self._risk_budget()
                decision = decide_bar_open_entry(
                    p,
                    point,
                    price,
                    self.costs.buy_price(price) - price,
                    self.portfolio.available_cash + intent.reserved_cash,
                    entry_fee=self.sizing.entry_fee,
                    exit_fee=self.sizing.exit_fee,
                    risk_budget=risk_budget,
                    quantity_step=self.sizing.quantity_step,
                    exit_slippage_bps=self.costs.adverse_bps,
                )
                if isinstance(decision, RejectedEntry):
                    self._cancel(symbol, point, decision.reason)
                    return
                modeled_price, qty = decision
                transition = FillTransition(
                    instrument=symbol,
                    intent_identity=intent.reservation_id,
                    activation_key=f"{symbol}:{self._ns(point)}",
                    execution_reference_id=f"entry:{intent.reservation_id}",
                    availability_key=point,
                    quantity=qty,
                    price=modeled_price,
                    fee=self.sizing.entry_fee,
                    reason="FILLED",
                )
                commit_entry(
                    self.portfolio,
                    transition,
                    stop=p.stop,
                    target=None,
                    reserved_cash=intent.reserved_cash,
                )
                self.portfolio.mark_position(symbol, price)
                target = self.strategies[symbol].on_filled(modeled_price)
                self.portfolio.positions[symbol].target = target
                del self.pending_intents[symbol]
                self.entry_times[symbol] = point
                self.fills.append(
                    FillRecord(
                        point,
                        symbol,
                        "FILLED",
                        modeled_price,
                        qty,
                        target,
                        self.sizing.entry_fee,
                        event_time_ms=market_point,
                        reference_id=transition.execution_reference_id,
                        reference_price=price,
                        execution_cost=(modeled_price - price) * qty,
                        risk_budget=risk_budget,
                        estimated_stop_loss=qty
                        * (modeled_price - self.costs.sell_price(p.stop))
                        + self.sizing.entry_fee
                        + self.sizing.exit_fee,
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
        if symbol in self.portfolio.positions:
            self._intervals[symbol] = market_point

    def _exit(self, symbol, point, price, reason, *, event_time=None):
        reference_price = price
        price = self.costs.sell_price(price)
        pos = self.portfolio.positions[symbol]
        qty = pos.quantity
        pnl = price * qty - self.sizing.exit_fee - pos.basis
        transition = FillTransition(
            instrument=symbol,
            intent_identity=f"position:{symbol}",
            activation_key=f"{symbol}:{self._ns(self.entry_times[symbol])}",
            execution_reference_id=f"exit:{symbol}:{self._ns(self.entry_times[symbol])}",
            availability_key=point,
            quantity=qty,
            price=price,
            fee=self.sizing.exit_fee,
            reason=reason,
        )
        commit_exit(self.portfolio, transition)
        self.strategies[symbol].on_position_closed()
        self.entry_times.pop(symbol)
        self.exit_due.discard(symbol)
        self.exit_requested_at.pop(symbol, None)
        self._intervals.pop(symbol, None)
        self.fills.append(
            FillRecord(
                point,
                symbol,
                "EXIT",
                price,
                qty,
                fee=self.sizing.exit_fee,
                net_pnl=pnl,
                reason=reason,
                event_time_ms=event_time,
                reference_id=transition.execution_reference_id,
                reference_price=reference_price,
                execution_cost=(reference_price - price) * qty,
            )
        )

    def _open_exit(self, symbol, point, price):
        pos = self.portfolio.positions.get(symbol)
        if pos is None:
            return
        self.portfolio.mark_position(symbol, price)
        self._enforce_risk(point)
        market_point = point - self.costs.feed_delay_ms
        if symbol in self.exit_due and market_point < self.exit_requested_at[symbol]:
            return
        if symbol in self._intervals:
            raise IncompleteSessionError(
                "overlapping unpublished position intervals are unsupported"
            )
        decision = decide_bar_open_gap_exit(
            pos, price, Decimal(0), symbol in self.exit_due
        )
        if decision is None:
            return
        label = {"pending": "TIMER", "stop": "STOP_GAP", "target": "TARGET_GAP"}[
            decision.kind
        ]
        self._exit(symbol, point, decision.price, label, event_time=market_point)

    def _settle(self, symbol, bar):
        if self._intervals.get(symbol) != bar.bar_start_ms:
            return
        self._intervals.pop(symbol)
        pos = self.portfolio.positions.get(symbol)
        if pos is None:
            return
        if pos.stop is not None and bar.low <= pos.stop:
            self._exit(symbol, bar.available_at_ms, pos.stop, "STOP_TOUCH")
        elif pos.target is not None and bar.high >= pos.target:
            self._exit(symbol, bar.available_at_ms, pos.target, "TARGET_TOUCH")

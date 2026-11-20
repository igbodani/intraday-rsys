"""Replay pinned, final historical prints with an explicitly modeled delivery clock."""

from dataclasses import asdict, replace
from decimal import Decimal
from heapq import heappop, heappush, merge
from itertools import groupby

from rsys.account.acceptance import sort_proposals
from rsys.account.money import ceil_step, floor_step
from rsys.execution.errors import IncompleteSessionError
from rsys.execution.models import DecisionRecord, Trade
from rsys.execution.trade import TradeInputRunner
from rsys.strategies.intrabar import preview_entry
from rsys.strategies.models import CompletedBar


class SessionTimers:
    """Only pending timers are retained between market batches."""

    def __init__(self):
        self.pending = []

    def enqueue(self, event):
        heappush(self.pending, (event.sort_key, event.event_id, event))

    def through(self, instant_ns):
        while self.pending and self.pending[0][2].available_at_ns <= instant_ns:
            yield heappop(self.pending)[2]


class IntrabarSession(TradeInputRunner):
    """Share sizing, acceptance, fills, timers and risk with the trade execution path."""

    def play(
        self,
        tapes,
        scanner,
        opening,
        closing,
        write,
        *,
        order_window,
        strategy_open,
        limit_bps,
        full_evidence=True,
    ):
        if self._ran:
            raise ValueError("A session can only be replayed once")
        self._ran = True
        self.live_scan, self.write = scanner, write
        self.full_evidence = full_evidence
        self.forming, self.minute_volume = {}, {}
        self.opening = opening
        self.order_open, self.order_close = order_window
        self.strategy_open = strategy_open
        self.limit_bps = limit_bps
        self.sell_limits = {}
        self.ready_ns = {}
        self.engine = SessionTimers()
        self._begin_risk_session()
        if self.execution.flatten_at_ms is not None:
            self.engine.enqueue(
                self._event("flatten", self.execution.flatten_at_ms, 30, "flatten")
            )
        prints = merge(*tapes, key=lambda p: (p.offset_ns, p.symbol, p.identity))
        groups = groupby(prints, key=lambda p: p.offset_ns)
        boundary = 60_000
        lag = self.costs.feed_delay_ms
        for elapsed_ns, group in groups:
            while boundary * 1_000_000 <= elapsed_ns:
                self._close_minute(boundary, lag)
                boundary += 60_000
            point = opening + elapsed_ns // 1_000_000 + lag
            self._timers(point)
            self._market_batch(list(group), elapsed_ns, point)
        while opening + boundary <= closing:
            self._close_minute(boundary, lag)
            boundary += 60_000
        if self.portfolio.positions or self.pending_intents:
            raise IncompleteSessionError(
                "Session ended with an open position or pending entry"
            )

    def _timers(self, point):
        for event in self.engine.through(self._ns(point)):
            start = len(self.fills)
            self._dispatch(event)
            self.write(self.trace[0])
            self.trace.clear()
            if self.limit_bps is not None:
                for symbol in sorted(self.exit_due - self.sell_limits.keys()):
                    self._arm_sell_limit(
                        symbol, self.live_scan.prices[symbol], event.available_at_ns
                    )
            self._write_fills(start, event.available_at_ns)

    def _close_minute(self, boundary, lag):
        point = self.opening + boundary + lag
        self._timers(point)
        start = self.opening + boundary - 60_000
        scan, rows = self.live_scan.rank(boundary * 1_000_000 - 1)
        before = len(self.fills)
        decision = self._decide_forming(
            scan, self.live_scan.prices, point, self._ns(point)
        )
        for symbol, strategy in self.strategies.items():
            bar = self.forming.get(symbol)
            if start < self.strategy_open:
                continue
            if bar is None:
                strategy.on_missing_minute(start, known=True)
            else:
                strategy.on_completed_bar(
                    replace(bar, available_at_ms=point), is_candidate=False
                )
        if self.full_evidence or decision["signals"]:
            self.write(
                {
                    "kind": "minute_close",
                    "available_at_ns": self._ns(point),
                    "available_at_ms": point,
                    "bar_start_ms": start,
                    **decision,
                    "bars": {
                        s: asdict(replace(b, available_at_ms=point))
                        for s, b in self.forming.items()
                    },
                    "ranked": scan.ranked,
                    "top": scan.top,
                    "scanner": rows,
                }
            )
        self._write_fills(before, self._ns(point))
        self.forming.clear()
        self.minute_volume.clear()

    def _write_fills(self, start, available_ns):
        for fill in self.fills[start:]:
            self.write(
                {"kind": "fill", "available_at_ns": available_ns, **asdict(fill)}
            )

    def _market_batch(self, prints, elapsed_ns, point):
        before = len(self.fills)
        previous = self.live_scan.prices.copy()
        batch = []
        source_ns = self._ns(self.opening) + elapsed_ns
        available_ns = source_ns + self.costs.feed_delay_ms * 1_000_000
        start = self.opening + elapsed_ns // 60_000_000_000 * 60_000
        changed = set()
        for p in prints:
            s = p.symbol
            self.live_scan.observe(p)
            if p.updates_volume:
                self.minute_volume[s] = self.minute_volume.get(s, 0) + p.size
            bar = self.forming.get(s)
            if p.updates_price:
                changed.add(s)
                self.forming[s] = CompletedBar(
                    start,
                    point,
                    bar.open if bar else p.price,
                    max(bar.high, p.price) if bar else p.price,
                    min(bar.low, p.price) if bar else p.price,
                    p.price,
                    self.minute_volume[s],
                )
                if s in self.portfolio.positions:
                    self.portfolio.mark_position(s, p.price)
            elif bar and p.updates_volume:
                changed.add(s)
                self.forming[s] = replace(
                    bar, available_at_ms=point, volume=self.minute_volume[s]
                )
            if (
                p.executable
                and self.order_open
                <= self.opening + elapsed_ns // 1_000_000
                < self.order_close
            ):
                batch.append(
                    (
                        s,
                        Trade(
                            p.identity,
                            self.opening + elapsed_ns // 1_000_000,
                            point,
                            p.price,
                            size=p.size,
                        ),
                    )
                )
        scan, rows = self.live_scan.rank(elapsed_ns)
        decision = self._decide_forming(scan, previous, point, available_ns)
        # Manage all held positions, even when the symbol has left the top group.
        for s, t in batch:
            if s in self.portfolio.positions:
                if self.limit_bps is None:
                    self._attempt_exit(s, t, point)
                else:
                    self._check_day_limit_exit(s, t, point, source_ns, available_ns)
        for proposal in sort_proposals(
            [i.proposal for i in self.pending_intents.values()]
        ):
            s = proposal.symbol
            for symbol, t in batch:
                if (
                    symbol == s
                    and s in self.pending_intents
                    and source_ns >= self.ready_ns[s]
                    and (
                        self.limit_bps is None
                        or self.costs.buy_price(t.price)
                        <= self._limit_price(proposal.signal_price, buy=True)
                    )
                ):
                    self._attempt_entry(s, t, point)
        self._enforce_risk(point)
        self.portfolio.assert_invariants()
        if self.full_evidence or decision["signals"]:
            self.write(
                {
                    "kind": "trade_batch",
                    "available_at_ms": point,
                    "available_at_ns": available_ns,
                    "market_time_ns": source_ns,
                    "prints": [asdict(p) for p in prints],
                    "forming": {
                        s: asdict(b) for s, b in self.forming.items() if s in changed
                    },
                    "scanner": rows,
                    "ranked": scan.ranked,
                    "top": scan.top,
                    "excluded": scan.excluded,
                    **decision,
                    "risk_halt": self.risk.halt_reason,
                    "equity": self.portfolio.equity({}),
                }
            )
        self._write_fills(before, available_ns)

    def _decide_forming(self, scan, previous, point, available_ns):
        proposals = []
        for rank, s in enumerate(scan.top, 1):
            bar = self.forming.get(s)
            # Evaluate fresh prices on eligible ticks, with the cross-symbol rank
            # after every publication sharing this instant has been applied.
            if (
                bar is None
                or bar.bar_start_ms < self.strategy_open
                or not self.order_open <= point < self.order_close
            ):
                continue
            proposal = preview_entry(
                self.strategies[s],
                replace(bar, available_at_ms=point),
                previous.get(s),
                available_ns,
            )
            if proposal:
                proposals.append(
                    replace(
                        proposal,
                        scanner_rank=rank,
                        decision_latency_ms=self.execution.decision_latency_ms,
                    )
                )
        self._enforce_risk(point)
        record = DecisionRecord(point, scan.top, [p.symbol for p in proposals], [], {})
        if proposals:
            self.decisions[:] = [record]
            self._accept(proposals, record, point)
            for symbol in record.accepted:
                self.ready_ns[symbol] = (
                    available_ns + self.execution.decision_latency_ms * 1_000_000
                )
        return {
            "proposed": record.proposed,
            "signals": [
                {
                    **asdict(p),
                    **(
                        {"buy_limit": self._limit_price(p.signal_price, buy=True)}
                        if self.limit_bps is not None
                        else {}
                    ),
                }
                for p in proposals
            ],
            "accepted": record.accepted,
            "rejected": record.rejected,
        }

    def _limit_price(self, reference, *, buy):
        offset = self.limit_bps / 10000
        raw = reference * (1 + offset if buy else 1 - offset)
        tick = Decimal("0.01") if raw >= 1 else Decimal("0.0001")
        return floor_step(raw, tick) if buy else ceil_step(raw, tick)

    def _arm_sell_limit(self, symbol, reference, at_ns):
        limit = self._limit_price(reference, buy=False)
        self.sell_limits[symbol] = (
            limit,
            at_ns + self.execution.decision_latency_ms * 1_000_000,
        )
        self.write(
            {
                "kind": "sell_limit",
                "available_at_ns": at_ns,
                "available_at_ms": self._ms(at_ns),
                "symbol": symbol,
                "limit_price": limit,
                "reason": self.exit_reasons[symbol],
            }
        )

    def _check_day_limit_exit(self, symbol, trade, point, source_ns, available_ns):
        if symbol not in self.sell_limits:
            position = self.portfolio.positions[symbol]
            if position.stop is not None and trade.price <= position.stop:
                reason = "stop"
            elif position.target is not None and trade.price >= position.target:
                reason = "target"
            else:
                return
            self.exit_due.add(symbol)
            self.exit_reasons[symbol] = reason
            self.exit_requested_at[symbol] = point
            self._arm_sell_limit(symbol, trade.price, available_ns)
            return
        limit, ready = self.sell_limits[symbol]
        if source_ns >= ready and self.costs.sell_price(trade.price) >= limit:
            self._attempt_exit(symbol, trade, point)
            if symbol not in self.portfolio.positions:
                del self.sell_limits[symbol]

"""Fixture-case interpreter for F01-F17 (docs/M2_IMPLEMENTATION_HANDOFF.md steps 2-4).

Processes one `fixtures/cases.json` case's declared event stream through the real
`Portfolio`/`sizing` modules and records the resulting checkpoint sequence plus final
account state, so `uv run rsys fixture` and the test suite exercise one production path
instead of returning hand-copied expected values.

Scope: one synthetic account case on the shared PhaseEngine queue. This exercises the
ledger and declared account-event rules; it is not a real market-data loader. The separate
synthetic session runner supplies ReplayRunner-backed strategy decisions. Split conversion
here supports exact quantity increments and known-at-effective actions only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from rsys.money import amount
from rsys.orders import (
    ENTRY_THROUGH_STOP,
    INVALID_STOP_DISTANCE,
    AcceptedIntent,
    Checkpoint,
    Proposal,
)
from rsys.phase_engine import PhaseEngine, PhaseEvent
from rsys.portfolio import AcceptanceError, Portfolio
from rsys.sizing import size_quantity

TICK = Decimal("0.01")


def _floor_tick(value: Decimal) -> Decimal:
    return value.quantize(TICK, rounding=ROUND_FLOOR)


def _ceil_tick(value: Decimal) -> Decimal:
    return value.quantize(TICK, rounding=ROUND_CEILING)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _time_ms(time_local: str) -> int:
    hours, minutes, seconds = (int(part) for part in time_local.split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


@dataclass
class PendingExit:
    scheduled_time_ms: int
    reason: str  # "flatten" or "time_exit"


@dataclass
class FixtureCaseResult:
    case_id: str
    checkpoints: list[Checkpoint] = field(default_factory=list)
    rejection: str | None = None
    final_quantity: Decimal = Decimal(0)
    final_cash: Decimal = Decimal(0)
    final_equity: Decimal = Decimal(0)
    final_unsettled_cash: Decimal = Decimal(0)
    reserved_cash: Decimal = Decimal(0)
    sized_quantity: Decimal | None = None
    sized_reservation: Decimal | None = None
    sized_initial_risk: Decimal | None = None
    entry_fill: Decimal | None = None
    exit_price: Decimal | None = None
    total_fees: Decimal = Decimal(0)
    first_rejection: str | None = None
    entry_time_ms: int | None = None
    flatten_time_ms: int | None = None
    positions: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    acceptance_order: list[str] = field(default_factory=list)
    features: dict[str, Decimal] = field(default_factory=dict)


class FixtureEngine:
    """Processes one fixture case's declared events in order."""

    def __init__(self, case: dict[str, Any]):
        self.case = case
        self.costs = case.get("costs", {})
        self.sizing_cfg = case.get("sizing", {})
        self.entry_fee = _dec(
            self.costs.get(
                "fixed_entry_fee", self.costs.get("fixed_fee_each_side", "0")
            )
        )
        self.exit_fee = _dec(
            self.costs.get("fixed_exit_fee", self.costs.get("fixed_fee_each_side", "0"))
        )
        self.slippage = _dec(self.costs.get("adverse_slippage_per_share", "0"))
        self.decision_latency_ms = int(case.get("decision_latency_ms", 0))

        total_cash = _dec(case["initial_cash"])
        spendable = (
            _dec(case["initial_spendable_cash"])
            if "initial_spendable_cash" in case
            else total_cash
        )
        unsettled = (
            _dec(case["initial_unsettled_cash"])
            if "initial_unsettled_cash" in case
            else Decimal(0)
        )
        self.portfolio = Portfolio(
            total_cash=total_cash, spendable_cash=spendable, unsettled_cash=unsettled
        )

        self.pending_intents: dict[str, AcceptedIntent] = {}
        self.pending_exits: dict[str, PendingExit] = {}
        self.last_mark: dict[str, Decimal] = {}
        self.fills_count: dict[str, int] = {}
        self.result = FixtureCaseResult(case_id=case["id"])
        self.engine = PhaseEngine(on_dispatch=self._dispatch)
        self._timer_counter = 0
        for value in (self.entry_fee, self.exit_fee, self.slippage):
            amount(value, "execution cost")

        if "initial_position" in case:
            pos = case["initial_position"]
            symbol = pos["symbol"]
            quantity = _dec(pos["quantity"])
            entry = _dec(pos["entry"])
            self.portfolio.positions[symbol] = self._make_seeded_position(
                pos, quantity, entry
            )
            self.last_mark[symbol] = entry

    def _make_seeded_position(
        self, pos: dict[str, Any], quantity: Decimal, entry: Decimal
    ):
        from rsys.orders import Position

        return Position(
            symbol=pos["symbol"],
            quantity=quantity,
            entry_price=entry,
            basis=quantity * entry,
            stop=_dec(pos["stop"]) if pos.get("stop") is not None else None,
            target=_dec(pos["target"]) if pos.get("target") is not None else None,
            mark=entry,
        )

    def run(self) -> FixtureCaseResult:
        phases = {
            "proposal": 50,
            "risk_sized_proposal": 50,
            "simultaneous_proposals": 50,
            "bar_complete": 10,
            "stop_change": 50,
            "flatten_timer": 30,
            "time_exit_request": 30,
            "settlement_release": 26,
            "scanner_snapshot": 40,
            "split_effective": 25,
        }
        for index, event in enumerate(self.case.get("events", [])):
            kind = event["type"]
            identity = event.get("event_id", f"fixture:{index:06}")
            point = _time_ms(event["time_local"])
            if kind in ("bar_open", "simultaneous_opens"):
                for phase in (60, 70):
                    self.engine.enqueue(
                        PhaseEvent(
                            f"{identity}:{phase}",
                            point * 1_000_000,
                            phase,
                            "open",
                            payload=event,
                        )
                    )
            else:
                if kind not in phases:
                    raise ValueError(f"unsupported fixture event type: {kind}")
                self.engine.enqueue(
                    PhaseEvent(
                        identity, point * 1_000_000, phases[kind], kind, payload=event
                    )
                )
        self.engine.run()
        if set(self.pending_exits) & set(self.portfolio.positions):
            raise ValueError("incomplete fixture: required exit has no subsequent open")
        self._finalize()
        return self.result

    def _timer(self, point, kind, payload):
        self._timer_counter += 1
        self.engine.enqueue(
            PhaseEvent(
                f"timer:{self._timer_counter}",
                point * 1_000_000,
                30,
                kind,
                payload=payload,
            )
        )

    def _dispatch(self, scheduled):
        event, point = scheduled.payload, scheduled.available_at_ns // 1_000_000
        if scheduled.kind == "expiry":
            symbol = event["symbol"]
            intent = self.pending_intents.get(symbol)
            if intent is not None and intent.reservation_id == event["reservation_id"]:
                self.portfolio.release_reservation(
                    intent.reserved_cash, intent.reservation_id
                )
                del self.pending_intents[symbol]
                self.result.rejection = "EXPIRED"
                self._record_checkpoint(symbol, intent.proposal.signal_price, "expired")
        elif scheduled.kind == "hold":
            if (
                event["symbol"] in self.portfolio.positions
                and self.fills_count.get(event["symbol"]) == event["fill_number"]
            ):
                self.pending_exits[event["symbol"]] = PendingExit(point, "max_hold")
        elif scheduled.kind == "open":
            opens = (
                event["opens"]
                if event["type"] == "simultaneous_opens"
                else {event["symbol"]: event["open"]}
            )
            for symbol, raw in sorted(opens.items()):
                price = _dec(raw)
                self.last_mark[symbol] = price
                if scheduled.phase == 60 and symbol in self.portfolio.positions:
                    before = len(self.result.checkpoints)
                    self._check_open_exit(
                        symbol, point, price, self.portfolio.positions[symbol]
                    )
                    if len(self.result.checkpoints) == before:
                        self.portfolio.mark_position(symbol, price)
                        self._record_checkpoint(symbol, price, "mark")
                elif scheduled.phase == 70 and symbol in self.pending_intents:
                    self._attempt_entry(
                        symbol, point, price, self.pending_intents[symbol]
                    )
        else:
            self._process_event(event)
        if self.result.first_rejection is None and self.result.rejection is not None:
            self.result.first_rejection = self.result.rejection
        self.portfolio.assert_invariants()

    # -- checkpoint recording -------------------------------------------------

    def _record_checkpoint(
        self, primary_symbol: str, mark: Decimal, label: str = ""
    ) -> None:
        marks = dict(self.last_mark)
        marks[primary_symbol] = mark
        position = self.portfolio.positions.get(primary_symbol)
        quantity = position.quantity if position else Decimal(0)
        equity = self.portfolio.equity(marks)
        self.result.checkpoints.append(
            Checkpoint(
                cash=self.portfolio.total_cash,
                quantity=quantity,
                mark=mark,
                equity=equity,
                realized_pnl=self.portfolio.realized_pnl,
                label=label,
            )
        )

    # -- event handlers --------------------------------------------------------

    def _process_event(self, event: dict[str, Any]) -> None:
        kind = event["type"]
        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            raise ValueError(f"unsupported fixture event type: {kind}")
        handler(event)

    def _on_proposal(self, event: dict[str, Any]) -> None:
        symbol = event["symbol"]
        quantity = _dec(event["quantity"])
        self._accept_or_reject(symbol, quantity, event, event["time_local"])

    def _on_risk_sized_proposal(self, event: dict[str, Any]) -> None:
        symbol = event["symbol"]
        signal_price = _dec(event["signal_price"])
        stop = _dec(event["stop"])
        time_ms = _time_ms(event["time_local"])
        risk_budget = self.sizing_cfg.get("per_proposal_risk_budget")
        max_quantity = self.sizing_cfg.get("max_quantity")
        quantity_step = _dec(self.sizing_cfg.get("quantity_step", "1"))
        try:
            quantity, intended_risk = size_quantity(
                entry=signal_price,
                stop=stop,
                entry_fee=self.entry_fee,
                exit_fee=self.exit_fee,
                risk_budget=_dec(risk_budget) if risk_budget is not None else None,
                available_cash=self.portfolio.available_cash,
                quantity_step=quantity_step,
                max_quantity=_dec(max_quantity) if max_quantity is not None else None,
            )
        except AcceptanceError as exc:
            self.result.rejection = exc.reason
            self._record_checkpoint(symbol, signal_price, "rejection")
            return
        self.result.sized_quantity = quantity
        self.result.sized_initial_risk = intended_risk
        self._accept_or_reject(
            symbol, quantity, event, event["time_local"], time_ms=time_ms
        )

    def _accept_or_reject(
        self,
        symbol: str,
        quantity: Decimal,
        event: dict[str, Any],
        time_local: str,
        time_ms: int | None = None,
    ) -> None:
        signal_price = _dec(event["signal_price"])
        stop = _dec(event["stop"]) if event.get("stop") is not None else None
        target = _dec(event["target"]) if event.get("target") is not None else None
        time_ms = time_ms if time_ms is not None else _time_ms(time_local)

        if symbol in self.portfolio.positions or symbol in self.pending_intents:
            self.result.rejection = "DUPLICATE_PENDING_OR_HELD"
            self._record_checkpoint(symbol, signal_price, "rejection")
            return

        max_fills = self.case.get("max_fills_per_symbol_day")
        if max_fills is not None and self.fills_count.get(symbol, 0) >= int(max_fills):
            self.result.rejection = "MAX_FILLS_PER_DAY"
            self._record_checkpoint(symbol, signal_price, "rejection")
            return

        if stop is not None and stop >= signal_price:
            self.result.rejection = INVALID_STOP_DISTANCE
            self._record_checkpoint(symbol, signal_price, "rejection")
            return

        needed = quantity * signal_price + self.entry_fee
        try:
            reservation_id = self.portfolio.reserve(needed)
        except AcceptanceError as exc:
            self.result.rejection = exc.reason
            self._record_checkpoint(symbol, signal_price, "rejection")
            return

        self.result.rejection = None
        self.result.reserved_cash = needed
        self.result.sized_reservation = needed
        proposal = Proposal(
            symbol=symbol,
            quantity=quantity,
            signal_price=signal_price,
            stop=stop,
            target=target,
            decision_time_ms=time_ms,
            decision_latency_ms=self.decision_latency_ms,
            expiry_ms=event.get("expiry_ms"),
            max_hold_ms=event.get("max_hold_ms"),
        )
        self.pending_intents[symbol] = AcceptedIntent(
            proposal=proposal, reserved_cash=needed, reservation_id=reservation_id
        )
        if proposal.expires_at_ms is not None:
            if proposal.expires_at_ms <= proposal.decision_time_ms:
                self.portfolio.release_reservation(needed, reservation_id)
                del self.pending_intents[symbol]
                self.result.rejection = "EXPIRED"
            else:
                self._timer(
                    proposal.expires_at_ms,
                    "expiry",
                    {"symbol": symbol, "reservation_id": reservation_id},
                )

    def _on_simultaneous_proposals(self, event: dict[str, Any]) -> None:
        time_ms = _time_ms(event["time_local"])
        quantity_step = _dec(self.sizing_cfg.get("quantity_step", "1"))
        max_quantity = self.sizing_cfg.get("max_quantity")
        risk_budget = self.sizing_cfg.get("per_proposal_risk_budget")
        proposals = sorted(
            event["proposals"],
            key=lambda p: (-_dec(p["rvol"]), -_dec(p["gain"]), p["symbol"]),
        )
        for raw in proposals:
            symbol = raw["symbol"]
            signal_price = _dec(raw["signal_price"])
            stop = _dec(raw["stop"])
            try:
                quantity, _risk = size_quantity(
                    entry=signal_price,
                    stop=stop,
                    entry_fee=self.entry_fee,
                    exit_fee=self.exit_fee,
                    risk_budget=_dec(risk_budget) if risk_budget is not None else None,
                    available_cash=self.portfolio.available_cash,
                    quantity_step=quantity_step,
                    max_quantity=_dec(max_quantity)
                    if max_quantity is not None
                    else None,
                )
            except AcceptanceError:
                continue
            needed = quantity * signal_price + self.entry_fee
            reservation_id = self.portfolio.reserve(needed)
            proposal = Proposal(
                symbol=symbol,
                quantity=quantity,
                signal_price=signal_price,
                stop=stop,
                target=None,
                decision_time_ms=time_ms,
                decision_latency_ms=self.decision_latency_ms,
            )
            self.pending_intents[symbol] = AcceptedIntent(
                proposal=proposal, reserved_cash=needed, reservation_id=reservation_id
            )
            self.result.acceptance_order.append(symbol)

    def _check_open_exit(
        self, symbol: str, time_ms: int, open_price: Decimal, position
    ) -> None:
        pending_exit = self.pending_exits.get(symbol)
        if pending_exit is not None and time_ms >= pending_exit.scheduled_time_ms:
            exit_price = open_price - self.slippage
            self._exit_position(
                symbol, exit_price, time_ms, pending_exit.reason, mark=open_price
            )
            self.pending_exits.pop(symbol, None)
            return
        if position.stop is not None and open_price <= position.stop:
            self._exit_position(
                symbol, open_price - self.slippage, time_ms, "gap_stop", mark=open_price
            )
            return
        if position.target is not None and open_price >= position.target:
            self._exit_position(
                symbol, position.target, time_ms, "gap_target", mark=open_price
            )
            return
        # No exit triggered: mark-only update, not checkpoint-worthy on its own.

    def _attempt_entry(
        self, symbol: str, time_ms: int, open_price: Decimal, intent: AcceptedIntent
    ) -> None:
        proposal = intent.proposal
        if proposal.expires_at_ms is not None and time_ms >= proposal.expires_at_ms:
            self.portfolio.release_reservation(
                intent.reserved_cash, intent.reservation_id
            )
            del self.pending_intents[symbol]
            self.result.rejection = "EXPIRED"
            self.result.reserved_cash = Decimal(0)
            self._record_checkpoint(symbol, open_price, "expired")
            return
        if time_ms < proposal.ready_at_ms:
            self._record_checkpoint(symbol, open_price, "entry_skipped")
            return
        modeled_entry = open_price + self.slippage
        if proposal.stop is not None and modeled_entry <= proposal.stop:
            self.portfolio.release_reservation(
                intent.reserved_cash, intent.reservation_id
            )
            del self.pending_intents[symbol]
            self.result.rejection = ENTRY_THROUGH_STOP
            self.result.reserved_cash = Decimal(0)
            self._record_checkpoint(symbol, open_price, "entry_rejected")
            return
        try:
            quantity, _ = size_quantity(
                entry=modeled_entry,
                stop=proposal.stop,
                entry_fee=self.entry_fee,
                exit_fee=self.exit_fee,
                risk_budget=_dec(self.sizing_cfg["per_proposal_risk_budget"])
                if self.sizing_cfg.get("per_proposal_risk_budget") is not None
                else None,
                available_cash=self.portfolio.available_cash + intent.reserved_cash,
                quantity_step=_dec(self.sizing_cfg.get("quantity_step", "1")),
                max_quantity=proposal.quantity,
            )
        except AcceptanceError as exc:
            self.portfolio.release_reservation(
                intent.reserved_cash, intent.reservation_id
            )
            del self.pending_intents[symbol]
            self.result.rejection = exc.reason
            self._record_checkpoint(symbol, open_price, "entry_rejected")
            return
        self.portfolio.buy(
            symbol,
            quantity,
            modeled_entry,
            self.entry_fee,
            intent.reserved_cash,
            proposal.stop,
            proposal.target,
            reservation_id=intent.reservation_id,
        )
        self.fills_count[symbol] = self.fills_count.get(symbol, 0) + 1
        if proposal.max_hold_ms is not None:
            if proposal.max_hold_ms <= 0:
                raise ValueError("max_hold_ms must be positive")
            self._timer(
                time_ms + proposal.max_hold_ms,
                "hold",
                {"symbol": symbol, "fill_number": self.fills_count[symbol]},
            )
        self.result.total_fees += self.entry_fee
        self.result.entry_fill = modeled_entry
        self.result.entry_time_ms = time_ms
        self.result.reserved_cash = Decimal(0)
        del self.pending_intents[symbol]
        self._record_checkpoint(symbol, open_price, "entry")

    def _exit_position(
        self,
        symbol: str,
        exit_price: Decimal,
        time_ms: int,
        reason: str,
        mark: Decimal | None = None,
    ) -> None:
        self.portfolio.sell(symbol, exit_price, self.exit_fee)
        self.result.total_fees += self.exit_fee
        self.result.exit_price = exit_price
        self.pending_exits.pop(symbol, None)
        self._record_checkpoint(
            symbol, mark if mark is not None else exit_price, reason
        )

    def _on_bar_complete(self, event: dict[str, Any]) -> None:
        symbol = event["symbol"]
        close = _dec(event["close"])
        self.last_mark[symbol] = close
        position = self.portfolio.positions.get(symbol)
        if position is None:
            return
        high, low = _dec(event["high"]), _dec(event["low"])
        stop_touched = position.stop is not None and low <= position.stop
        target_touched = position.target is not None and high >= position.target
        if stop_touched:
            self._exit_position(
                symbol,
                position.stop,
                _time_ms(event["time_local"]),
                "intrabar_stop",
                mark=close,
            )
            return
        if target_touched:
            self._exit_position(
                symbol,
                position.target,
                _time_ms(event["time_local"]),
                "intrabar_target",
                mark=close,
            )
            return
        self.portfolio.mark_position(symbol, close)
        self._record_checkpoint(symbol, close, "bar_close")

    def _on_stop_change(self, event: dict[str, Any]) -> None:
        position = self.portfolio.positions.get(event["symbol"])
        if position is not None:
            position.stop = _dec(event["stop"])

    def _on_flatten_timer(self, event: dict[str, Any]) -> None:
        scheduled = _time_ms(event["time_local"])
        self.result.flatten_time_ms = scheduled
        for symbol, intent in list(self.pending_intents.items()):
            self.portfolio.release_reservation(
                intent.reserved_cash, intent.reservation_id
            )
            del self.pending_intents[symbol]
        for symbol in self.portfolio.positions:
            self.pending_exits[symbol] = PendingExit(
                scheduled_time_ms=scheduled, reason="flatten"
            )

    def _on_time_exit_request(self, event: dict[str, Any]) -> None:
        self.pending_exits[event["symbol"]] = PendingExit(
            scheduled_time_ms=_time_ms(event["time_local"]),
            reason="time_exit",
        )

    def _on_settlement_release(self, event: dict[str, Any]) -> None:
        self.portfolio.apply_settlement_release(_dec(event["amount"]))

    def _on_scanner_snapshot(self, event: dict[str, Any]) -> None:
        pass  # Candidate membership never gates existing position management (F16).

    def _on_split_effective(self, event: dict[str, Any]) -> None:
        symbol = event["symbol"]
        ratio = _dec(event["ratio_new_to_old"])
        amount(ratio, "split ratio", positive=True)
        if _time_ms(event.get("known_at", event["time_local"])) > _time_ms(
            event["time_local"]
        ):
            raise ValueError(
                "late-known split requires unit reconciliation, unsupported in account fixtures"
            )
        if symbol in self.pending_intents:
            raise ValueError("split with pending intent is unsupported")
        position = self.portfolio.positions.get(symbol)
        if position is not None:
            step = _dec(self.sizing_cfg.get("quantity_step", "1"))
            if (position.quantity * ratio) % step:
                raise ValueError(
                    "fractional split quantity requires explicit cash-in-lieu policy"
                )
            position.quantity *= ratio
            position.entry_price /= ratio
            if position.stop is not None:
                position.stop = _floor_tick(position.stop / ratio)
            if position.target is not None:
                position.target = _ceil_tick(position.target / ratio)
            position.mark = self.last_mark.get(symbol, position.mark) / ratio
            self.last_mark[symbol] = position.mark

        reference = self.case.get("historical_reference")
        if reference is not None:
            prev_close = _dec(reference["previous_raw_close"]) / ratio
            prev_volume = _dec(reference["prior_raw_volume"]) * ratio
            self.result.features["previous_close_current_units"] = prev_close
            self.result.features["prior_volume_current_units"] = prev_volume

    def _finalize(self) -> None:
        marks = dict(self.last_mark)
        self.result.final_cash = self.portfolio.total_cash
        self.result.final_equity = self.portfolio.equity(marks)
        self.result.final_unsettled_cash = self.portfolio.unsettled_cash
        self.result.reserved_cash = self.portfolio.reserved_cash
        for symbol, position in self.portfolio.positions.items():
            self.result.positions[symbol] = {
                "quantity": position.quantity,
                "entry": position.entry_price,
                "stop": position.stop,
                "target": position.target,
            }
        if len(self.portfolio.positions) == 1:
            ((_symbol, position),) = self.portfolio.positions.items()
            self.result.final_quantity = position.quantity
        if (
            "session_gap_fraction" in self.result.features
            or "previous_close_current_units" in self.result.features
        ):
            open_events = [
                e for e in self.case.get("events", []) if e["type"] == "bar_open"
            ]
            if open_events and "previous_close_current_units" in self.result.features:
                last_open = _dec(open_events[-1]["open"])
                prev_close = self.result.features["previous_close_current_units"]
                self.result.features["session_gap_fraction"] = (
                    (last_open / prev_close) - 1 if prev_close else None
                )


def run_case(case: dict[str, Any]) -> FixtureCaseResult:
    return FixtureEngine(case).run()


def compare_case(case: dict[str, Any], result: FixtureCaseResult) -> list[str]:
    """Shared CLI/test oracle: returns a list of human-readable mismatches, empty if every
    checkpoint and final-state field the fixture declares matches exactly."""
    problems: list[str] = []

    if "expected" in case:
        pointer = 0
        for want in case["expected"]:
            found = False
            while pointer < len(result.checkpoints):
                actual = result.checkpoints[pointer]
                pointer += 1
                aliases = {
                    "first_entry": "entry",
                    "first_bar_close": "bar_close",
                    "target_exit": "intrabar_target",
                    "stop_exit": "intrabar_stop",
                    "gap_exit": "gap_stop",
                    "flatten_exit": "flatten",
                    "before_stop_change": "bar_close",
                    "next_open_gap_exit": "gap_stop",
                    "after_entry": "entry",
                    "after_exit": "time_exit",
                    "first_open_skipped": "entry_skipped",
                    "delayed_entry": "entry",
                    "target_gap_exit": "gap_target",
                    "first_rejection": "rejection",
                    "second_entry": "entry",
                    "post_split_open": "mark",
                }
                if (
                    actual.label == aliases.get(want["checkpoint"], want["checkpoint"])
                    and actual.cash == _dec(want["cash"])
                    and actual.quantity == _dec(want["quantity"])
                    and actual.mark == _dec(want["mark"])
                    and actual.equity == _dec(want["equity"])
                    and actual.realized_pnl == _dec(want["realized_pnl"])
                ):
                    found = True
                    break
            if not found:
                problems.append(
                    f"no remaining checkpoint matches {want['checkpoint']!r}"
                )

    for key, actual in (
        ("exit_price", result.exit_price),
        ("expected_entry_time", result.entry_time_ms),
        ("expected_flatten_time", result.flatten_time_ms),
    ):
        if key in case:
            want = _time_ms(case[key]) if key.endswith("_time") else _dec(case[key])
            if actual != want:
                problems.append(f"{key} mismatch")
    if "first_rejection" in case and result.first_rejection != case["first_rejection"]:
        problems.append("first_rejection mismatch")
    if "rejection" in case and result.rejection != case["rejection"]:
        problems.append(
            f"rejection: expected {case['rejection']!r}, got {result.rejection!r}"
        )
    if "expected_reserved_cash" in case and result.reserved_cash != _dec(
        case["expected_reserved_cash"]
    ):
        problems.append("expected_reserved_cash mismatch")
    if "expected_reservation" in case and result.sized_reservation != _dec(
        case["expected_reservation"]
    ):
        problems.append("expected_reservation mismatch")
    if "expected_quantity" in case and result.sized_quantity != _dec(
        case["expected_quantity"]
    ):
        problems.append("expected_quantity mismatch")
    if "expected_initial_stop_risk" in case and result.sized_initial_risk != _dec(
        case["expected_initial_stop_risk"]
    ):
        problems.append("expected_initial_stop_risk mismatch")
    if "expected_final_unsettled_cash" in case and result.final_unsettled_cash != _dec(
        case["expected_final_unsettled_cash"]
    ):
        problems.append("expected_final_unsettled_cash mismatch")
    if "entry_fill" in case and result.entry_fill != _dec(case["entry_fill"]):
        problems.append("entry_fill mismatch")
    if "total_fees" in case and result.total_fees != _dec(case["total_fees"]):
        problems.append("total_fees mismatch")

    if "expected_positions" in case:
        if set(result.positions) != set(case["expected_positions"]):
            problems.append("expected_positions symbols mismatch")
        for symbol, pos in case["expected_positions"].items():
            got = result.positions.get(symbol)
            if (
                got is None
                or got["quantity"] != _dec(pos["quantity"])
                or got["entry"] != _dec(pos["entry"])
            ):
                problems.append(f"expected_positions[{symbol}] mismatch")
        if result.final_cash != _dec(case["expected_cash"]):
            problems.append("expected_cash mismatch")
        if result.final_equity != _dec(case["expected_equity"]):
            problems.append("expected_equity mismatch")
        if result.acceptance_order != case["expected_order"]:
            problems.append("expected_order mismatch")

    if "expected_position" in case:
        if len(result.positions) == 1:
            (symbol, got) = next(iter(result.positions.items()))
            want = case["expected_position"]
            if (
                got["quantity"] != _dec(want["quantity"])
                or got["entry"] != _dec(want["entry"])
                or got["stop"] != _dec(want["stop"])
                or got["target"] != _dec(want["target"])
            ):
                problems.append("expected_position mismatch")
        else:
            problems.append("expected_position: no single open position found")

    if "expected_features" in case:
        for key, value in case["expected_features"].items():
            if result.features.get(key) != _dec(value):
                problems.append(f"expected_features[{key}] mismatch")

    return problems

"""End-to-end standalone integration tests: scanner -> ORB proposal -> PhaseEngine-ordered
acceptance -> next-bar-open fill, through the real `Portfolio`/`sizing`/`acceptance` modules
(docs/M2_IMPLEMENTATION_HANDOFF.md step 1 composed with steps 2 and 7)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from rsys.standalone_run import ExecutionPolicy, SizingPolicy, StandaloneRunner
from rsys.strategies.orb import CompletedBar, OrbState, OrbStrategy

CASES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "strategy_cases.json"
_S01 = next(c for c in json.loads(CASES_PATH.read_text())["cases"] if c["id"] == "S01")


def _ms(timestamp: str) -> int:
    hours, minutes, seconds = (int(part) for part in timestamp.split("T")[1].split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _bar(raw: dict) -> CompletedBar:
    return CompletedBar(
        bar_start_ms=_ms(raw["bar_start"]), available_at_ms=_ms(raw["available_at"]),
        open=Decimal(raw["open"]), high=Decimal(raw["high"]), low=Decimal(raw["low"]),
        close=Decimal(raw["close"]), volume=Decimal(raw["volume"]),
    )


SESSION_OPEN_MS = _ms("2024-11-29T09:30:00")
_NEXT_OPEN_BAR = {
    "bar_start": "2024-11-29T09:46:00", "bar_end": "2024-11-29T09:47:00",
    "available_at": "2024-11-29T09:47:00", "open": "102", "high": "103", "low": "101.5",
    "close": "102.5", "volume": "100",
}


def _build_runner(*, cash="10000", risk_budget=None, max_quantity="10") -> tuple[StandaloneRunner, dict]:
    strategies = {"AAA": OrbStrategy("AAA", SESSION_OPEN_MS), "BBB": OrbStrategy("BBB", SESSION_OPEN_MS)}
    portfolio_cash = Decimal(cash)
    from rsys.portfolio import Portfolio
    portfolio = Portfolio(total_cash=portfolio_cash, spendable_cash=portfolio_cash, unsettled_cash=Decimal(0))
    sizing = SizingPolicy(
        risk_budget=Decimal(risk_budget) if risk_budget is not None else None,
        max_quantity=Decimal(max_quantity) if max_quantity is not None else None,
    )
    runner = StandaloneRunner(strategies, portfolio, sizing)
    bars_aaa = [_bar(raw) for raw in [*_S01["bars"], _NEXT_OPEN_BAR]]
    bars_bbb = [_bar(raw) for raw in [*_S01["bars"], _NEXT_OPEN_BAR]]  # identical breakout, but never a candidate
    scanner_inputs = {_ms("2024-11-29T09:46:00"): {"AAA": (Decimal(2), Decimal("0.03"))}}  # BBB absent: excluded
    return runner, {"bars": {"AAA": bars_aaa, "BBB": bars_bbb}, "scanner": scanner_inputs}


def test_candidate_proposal_is_accepted_and_filled_at_the_next_bar_open():
    runner, session = _build_runner()
    runner.run_session(session["bars"], session["scanner"])

    decision = next(d for d in runner.decisions if d.available_at_ms == _ms("2024-11-29T09:46:00"))
    assert decision.proposed == ["AAA"]  # BBB had the same breakout but was never a candidate
    assert decision.accepted == ["AAA"]

    fill = next(f for f in runner.fills if f.symbol == "AAA")
    assert fill.status == "FILLED"
    assert fill.price == Decimal(102)  # the NEXT bar's open, never the 101.02 signal close
    assert fill.target is not None
    assert "BBB" not in [f.symbol for f in runner.fills]


def test_noncandidate_breakout_never_produces_a_proposal():
    runner, session = _build_runner()
    runner.run_session(session["bars"], session["scanner"])
    assert all(d.symbol != "BBB" for d in runner.fills)
    bbb_proposed = any("BBB" in d.proposed for d in runner.decisions)
    assert bbb_proposed is False


def test_insufficient_cash_rejection_releases_reservation_and_rearms_the_strategy():
    runner, session = _build_runner(cash="50", max_quantity="1000")  # far too little for 10 shares
    runner.run_session(session["bars"], session["scanner"])

    decision = next(d for d in runner.decisions if d.available_at_ms == _ms("2024-11-29T09:46:00"))
    assert decision.accepted == []
    assert decision.rejected.get("AAA") in ("INSUFFICIENT_CASH", "INSUFFICIENT_SPENDABLE_CASH")
    assert runner.portfolio.reserved_cash == Decimal(0)
    assert runner.strategies["AAA"].state == OrbState.ARMED  # rearmed, ready for a fresh crossing


def test_entry_through_stop_releases_reservation_without_a_position():
    gapped_bar = dict(_NEXT_OPEN_BAR)
    gapped_bar.update(open="90", low="90")  # below the 99.00 stop
    runner, session = _build_runner()
    session["bars"]["AAA"] = [_bar(raw) for raw in _S01["bars"]] + [_bar(gapped_bar)]
    runner.run_session(session["bars"], session["scanner"])

    fill = next(f for f in runner.fills if f.symbol == "AAA")
    assert fill.status == "ENTRY_THROUGH_STOP"
    assert runner.portfolio.reserved_cash == Decimal(0)
    assert "AAA" not in runner.portfolio.positions


def _followup_bar(time_local: str, **overrides) -> dict:
    base = {
        "bar_start": "2024-11-29T09:47:00", "bar_end": time_local, "available_at": time_local,
        "open": "102", "high": "102", "low": "102", "close": "102", "volume": "100",
    }
    base.update(overrides)
    return base


def _run_to_fill_then(followups: list[dict], *, execution: ExecutionPolicy | None = None) -> StandaloneRunner:
    runner, session = _build_runner()
    runner.execution = execution or ExecutionPolicy()
    session["bars"]["AAA"] = [_bar(raw) for raw in [*_S01["bars"], _NEXT_OPEN_BAR, *followups]]
    session["bars"]["BBB"] = [_bar(raw) for raw in _S01["bars"]]  # no breakout follow-up needed for BBB
    runner.run_session(session["bars"], session["scanner"])
    return runner


def test_gap_through_stop_exits_at_the_observed_open():
    followup = _followup_bar("2024-11-29T09:48:00", open="90", high="90", low="90", close="90")
    runner = _run_to_fill_then([followup])
    exit_record = next(f for f in runner.fills if f.symbol == "AAA" and f.status == "EXIT")
    assert exit_record.price == Decimal(90)
    assert "AAA" not in runner.portfolio.positions
    assert runner.strategies["AAA"].state.value == "DONE"


def test_intrabar_stop_touch_exits_at_the_stop_level():
    # Stop is 99.00; this bar's open (100) doesn't gap through it, but its low does.
    followup = _followup_bar("2024-11-29T09:48:00", open="100", high="101", low="98", close="100.5")
    runner = _run_to_fill_then([followup])
    exit_record = next(f for f in runner.fills if f.symbol == "AAA" and f.status == "EXIT")
    assert exit_record.price == Decimal("99.00")


def test_intrabar_target_touch_exits_at_the_target_level():
    # Target is 108.00 (102 entry + 2R * 3); this bar's high touches it without gapping open.
    followup = _followup_bar("2024-11-29T09:48:00", open="103", high="110", low="102.5", close="105")
    runner = _run_to_fill_then([followup])
    exit_record = next(f for f in runner.fills if f.symbol == "AAA" and f.status == "EXIT")
    assert exit_record.price == Decimal("108.00")


def test_max_hold_timer_flattens_after_the_scheduled_duration():
    followup = _followup_bar("2024-11-29T09:48:00")  # flat bar: no gap/intrabar trigger on its own
    runner = _run_to_fill_then([followup], execution=ExecutionPolicy(max_hold_ms=60_000))
    exit_record = next(f for f in runner.fills if f.symbol == "AAA" and f.status == "EXIT")
    assert exit_record.price == Decimal(102)  # the scheduled bar's own open


def test_flatten_timer_closes_the_position_independent_of_protection():
    followup = _followup_bar("2024-11-29T09:48:00")
    at_timer = _followup_bar("2024-11-29T09:49:00", bar_start="2024-11-29T09:48:00",
                             open="103", high="103", low="103", close="103")
    runner = _run_to_fill_then([followup, at_timer], execution=ExecutionPolicy(flatten_at_ms=_ms("2024-11-29T09:48:00")))
    exit_record = next(f for f in runner.fills if f.symbol == "AAA" and f.status == "EXIT")
    assert exit_record.price == Decimal(103)
    assert exit_record.available_at_ms == _ms("2024-11-29T09:48:00")


def test_untouched_entry_interval_keeps_the_position_open():
    runner, session = _build_runner()
    runner.run_session(session["bars"], session["scanner"])
    assert not any(f.symbol == "AAA" and f.status == "EXIT" for f in runner.fills)
    assert "AAA" in runner.portfolio.positions  # still open; no follow-up bar was supplied


def test_reconciled_metrics_from_a_real_run_match_the_portfolio():
    from rsys.evaluation import compute_metrics

    followup = _followup_bar("2024-11-29T09:48:00", open="90", high="90", low="90", close="90")  # gap stop exit
    starting_equity = Decimal(10000)
    runner = _run_to_fill_then([followup])
    ending_equity = runner.portfolio.equity({})
    metrics = compute_metrics(starting_equity, ending_equity, runner.portfolio.realized_pnl, runner.fills)

    assert metrics.completed_trade_count == 1
    assert metrics.loss_count == 1
    assert metrics.net_change == ending_equity - starting_equity
    assert metrics.realized_pnl == runner.portfolio.realized_pnl

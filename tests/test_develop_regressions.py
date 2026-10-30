"""Whole-pipeline regressions for the develop bab9082 review counterexamples."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest
from test_session_coordinator import (
    _SCANNER_AT_0946,
    _coordinator,
    _daily_bars,
    _daily_bars_with_followup,
)
from test_trade_input_run import _bar_to_trades, _build_runner, _ms

from rsys.calendar import session_open_utc
from rsys.features import HistorySession
from rsys.multi_session_evaluation import (
    compute_exposure,
    compute_multi_session_metrics,
)
from rsys.portfolio import Portfolio
from rsys.scanner_history import (
    FixtureHistoryProvider,
    SyntheticSymbolHistory,
    build_scanner_inputs,
    candidate_input,
)
from rsys.session_coordinator import SessionResult
from rsys.standalone_run import ExecutionPolicy, FillRecord, IncompleteSessionError
from rsys.strategies.orb import OrbStrategy
from rsys.trade_execution import Trade


def test_three_sessions_reconcile_exits_cash_and_fees_at_identical_entry_times():
    portfolio = Portfolio(D(10000), D(10000), D(0))
    coordinator = _coordinator(
        portfolio, execution_factory=lambda _: ExecutionPolicy(flatten_at_ms=35280000)
    )
    coordinator.sizing = replace(coordinator.sizing, entry_fee=D(1), exit_fee=D(2))
    expected_cash = D(10000)
    for day, exit_price in zip(
        (date(2024, 11, 29), date(2024, 12, 2), date(2024, 12, 3)),
        (D(102), D(102), D(103)), strict=True,
    ):
        bars = _daily_bars_with_followup()
        bars['AAA'][-1] = replace(
            bars['AAA'][-1], open=exit_price, high=exit_price, low=exit_price, close=exit_price
        )
        coordinator.run_session(day, bars, _SCANNER_AT_0946)
        expected_cash += (exit_price - D(102)) * 10 - 3
        assert portfolio.positions == {}
        assert portfolio.total_cash == portfolio.spendable_cash == expected_cash
        assert portfolio.reserved_cash == 0
        assert portfolio.realized_pnl == expected_cash - 10000
        assert [f.status for f in coordinator.results[-1].fills] == ['FILLED', 'EXIT']
        assert coordinator.results[-1].session_open_ns == int(
            session_open_utc(day).timestamp() * 1_000_000_000
        )


def test_future_and_current_history_cannot_change_prior_session_rvol():
    def row(future_volume):
        provider = FixtureHistoryProvider({'AAA': SyntheticSymbolHistory([
            HistorySession(date(2024, 12, 2), 390, D(future_volume), True),
            HistorySession(date(2024, 11, 29), 210, D(future_volume), True),
            HistorySession(date(2024, 11, 26), 390, D(400), True),
            HistorySession(date(2024, 11, 27), 390, D(800), True),
        ], D(100), D(2000000), D(50))})
        return candidate_input(
            symbol='AAA', decision_date=date(2024, 11, 29), elapsed_minutes=60,
            current_volume=D(1200), latest_close=D(103), provider=provider,
            lookback_sessions=2, min_dollar_volume=D(1000000),
        )
    assert row(800).rvol == row(8000).rvol == D(2)


@pytest.mark.parametrize('day,minutes', [(date(2024, 11, 29), 210), (date(2024, 12, 2), 390)])
def test_exposure_uses_real_runner_clock_and_scheduled_close(day, minutes):
    coordinator = _coordinator(Portfolio(D(10000), D(10000), D(0)))
    coordinator.run_session(day, _daily_bars(), _SCANNER_AT_0946)
    result = compute_exposure(coordinator.results)
    assert result.open_ms == (minutes - 16) * 60_000
    assert result.total_scheduled_ms == minutes * 60_000
    assert result.fraction == D(minutes - 16) / D(minutes)


@pytest.mark.parametrize('ending,drawdown,pct', [(900, 100, D('0.1')), (1000, 0, D(0)), (1100, 0, D(0))])
def test_drawdown_includes_starting_equity_and_handles_no_decline(ending, drawdown, pct):
    result = SessionResult(date(2024, 11, 29), 0, [], [], [], D(ending))
    metrics = compute_multi_session_metrics([result], D(1000))
    assert metrics.drawdown.max_drawdown == D(drawdown)
    assert metrics.drawdown.max_drawdown_pct == pct


def test_bad_quality_trade_cannot_fill_or_enter_a_built_bar():
    runner, session = _build_runner()
    bad_time = _ms('2024-11-29T09:46:15')
    session['trades']['AAA'] = [
        Trade(t.trade_id, t.event_time_ms, t.available_at_ms, t.price,
              quality_ok=t.event_time_ms != bad_time)
        for t in session['trades']['AAA']
    ]
    # Match ReplayRunner's declared fail-closed quality policy; do not silently wash
    # the flag or drop an observation and claim complete coverage.
    with pytest.raises(ValueError, match='non-OK input'):
        runner.run_session(session['trades'], session['scanner'])
    assert not runner.fills
    assert runner.portfolio.positions == {}
    assert all(e.event_time_ns != runner._ns(bad_time) for e in runner.replay.processed)


@pytest.mark.parametrize('timer', ['flatten', 'max_hold'])
def test_timer_exit_waits_for_post_request_market_print_even_on_late_delivery(timer):
    flatten = _ms('2024-11-29T09:47:05')
    execution = (ExecutionPolicy(flatten_at_ms=flatten) if timer == 'flatten'
                 else ExecutionPolicy(max_hold_ms=50_000))
    runner, session = _build_runner(execution=execution)
    trailer = _bar_to_trades({
        'bar_start': '2024-11-29T09:47:00', 'open': '102.5',
        'high': '102.6', 'low': '102.4', 'close': '102.5',
    }, 'AAA')
    trailer[0].available_at_ms += 10_000
    session['trades']['AAA'].extend(trailer)
    runner.run_session(session['trades'], session['scanner'])
    fill = next(f for f in runner.fills if f.status == 'EXIT')
    assert fill.event_time_ms == _ms('2024-11-29T09:47:15')
    assert fill.available_at_ms == fill.event_time_ms
    assert fill.reason == ('FLATTEN' if timer == 'flatten' else 'MAX_HOLD')
    assert all(r['phase'] == 60 for r in runner.trace if r.get('status') == 'EXIT')


def test_decision_phase_observes_all_same_time_trade_publications():
    runner, session = _build_runner()
    closing = _ms('2024-11-29T09:45:45')
    decision_time = _ms('2024-11-29T09:46:00')
    next(t for t in session['trades']['AAA'] if t.event_time_ms == closing).available_at_ms = decision_time
    runner.run_session(session['trades'], session['scanner'])
    decisions = [d for d in runner.decisions if d.available_at_ms == decision_time]
    assert len(decisions) == 1
    assert decisions[0].proposed == decisions[0].accepted == ['AAA']
    decision_trace = [r for r in runner.trace if r.get('kind') == 'decision'
                      and r['available_at_ms'] == decision_time]
    assert len(decision_trace) == 1
    assert decision_trace[0]['phase'] == 40


@pytest.mark.parametrize('next_day', [date(2024, 11, 29), date(2024, 12, 2)])
def test_duplicate_or_reversed_session_is_rejected_before_state_changes(next_day):
    portfolio = Portfolio(D(10000), D(10000), D(0))
    coordinator = _coordinator(portfolio)
    coordinator.run_session(date(2024, 12, 2), {}, {})
    with pytest.raises(ValueError, match='strictly increasing'):
        coordinator.run_session(next_day, {}, {})
    assert len(coordinator.results) == 1
    assert portfolio.total_cash == 10000


def test_trade_fills_follow_rank_and_declared_phases_with_shared_cash():
    def run(reverse):
        runner, session = _build_runner(cash='1000', max_quantity='6')
        runner.strategies['ZZZ'] = OrbStrategy('ZZZ', _ms('2024-11-29T09:30:00'))
        # Deliberately reuse provider trade IDs on another instrument. They must not
        # collide in the replay or account even when their prices/quantities differ.
        session['trades']['ZZZ'] = deepcopy(session['trades']['AAA'])
        session['scanner'][_ms('2024-11-29T09:46:00')]['ZZZ'] = (D(3), D('.03'))
        if reverse:
            session['trades'] = dict(reversed(list(session['trades'].items())))
        runner.run_session(session['trades'], session['scanner'])
        return runner
    first, second = run(False), run(True)
    assert next(d for d in first.decisions if d.accepted).accepted == ['ZZZ', 'AAA']
    assert [(f.symbol, f.quantity) for f in first.fills if f.status == 'FILLED'] == [
        ('ZZZ', D(6)), ('AAA', D(3)),
    ]
    assert first.portfolio.total_cash == 73
    assert first.portfolio.reserved_cash == 0
    assert first.trace == second.trace
    assert all(r['phase'] == 70 for r in first.trace if r['kind'] == 'fill')


def test_typed_trade_size_conditions_and_tape_survive_into_the_builder():
    runner, session = _build_runner()
    original = session['trades']['AAA']
    session['trades']['AAA'] = [
        Trade(t.trade_id, t.event_time_ms, t.available_at_ms, t.price,
              size=D(i % 4 + 1), conditions=('ALL',), tape='fixture-tape')
        for i, t in enumerate(original)
    ]
    runner.run_session(session['trades'], session['scanner'])
    first_bar = runner.contexts[0].closed_bars('AAA')[0]
    assert first_bar.volume == 10
    first_source = next(e for e in runner.replay.processed if e.event_id.endswith(original[1].trade_id))
    assert first_source.size == 2
    assert first_source.conditions == ('ALL',)
    assert first_source.tape == 'fixture-tape'


@pytest.mark.parametrize('action', ['cancel', 'correct'])
def test_same_time_correction_cannot_be_used_as_a_fresh_entry(action):
    runner, session = _build_runner()
    point = _ms('2024-11-29T09:46:15')
    original = next(t for t in session['trades']['AAA'] if t.event_time_ms == point)
    correction = {'original_trade_id': original.trade_id, 'available_at_ms': point, 'action': action}
    if action == 'correct':
        correction.update(corrected_price=D('102.75'), corrected_size=D(7))
    runner.run_session(session['trades'], session['scanner'], {'AAA': [correction]})
    fill = next(f for f in runner.fills if f.status == 'FILLED')
    assert fill.event_time_ms == point + 15_000
    assert fill.price == D('101.5')


def test_correction_flag_cannot_be_washed_into_an_ordinary_trade():
    runner, session = _build_runner()
    original = session['trades']['AAA'][0]
    session['trades']['AAA'][0] = Trade(
        original.trade_id, original.event_time_ms, original.available_at_ms,
        original.price, is_correction=True,
    )
    with pytest.raises(ValueError, match='corrections_by_symbol'):
        runner.run_session(session['trades'], session['scanner'])
    assert runner.portfolio.total_cash == 10000
    assert not runner.fills


def test_future_trade_perturbation_preserves_actual_runner_prefix_with_negative_control():
    cutoff = _ms('2024-11-29T09:46:15')
    def run(change_future=False, change_signal=False):
        runner, session = _build_runner()
        for t in session['trades']['AAA']:
            if change_future and t.event_time_ms > cutoff:
                t.price = D(999)
            if change_signal and t.event_time_ms == _ms('2024-11-29T09:45:45'):
                t.price = D(100)
        runner.run_session(session['trades'], session['scanner'])
        return [r for r in runner.trace if r['available_at_ms'] <= cutoff]
    baseline = run()
    assert run(change_future=True) == baseline
    assert run(change_signal=True) != baseline


def test_rejected_fill_time_cash_check_does_not_commit_adapter_activation():
    runner, session = _build_runner(cash='102', max_quantity='1')
    runner.run_session(session['trades'], session['scanner'])
    assert any(d.accepted == ['AAA'] for d in runner.decisions)
    assert not any(f.status == 'FILLED' for f in runner.fills)
    assert runner.portfolio.total_cash == 102
    assert runner.portfolio.reserved_cash == 0
    assert runner.portfolio.positions == {}
    assert runner.adapters['AAA'].entry_trade_id is None


def test_history_provider_boundary_is_enforced_even_if_provider_is_unbounded():
    class UnboundedProvider(FixtureHistoryProvider):
        def sessions_before(self, symbol, decision_date):
            return self._history[symbol].sessions
    provider = UnboundedProvider({'AAA': SyntheticSymbolHistory([
        HistorySession(date(2024, 12, 2), 390, D(9999), True),
        HistorySession(date(2024, 11, 27), 390, None, False),
        HistorySession(date(2024, 11, 26), 390, D(600), True),
    ], D(100), D(2000000), D(50))})
    row = candidate_input(
        symbol='AAA', decision_date=date(2024, 11, 29), elapsed_minutes=60,
        current_volume=D(1200), latest_close=D(103), provider=provider,
        lookback_sessions=1, min_dollar_volume=D(1000000),
    )
    assert row.rvol is None  # selected missing prior day cannot be backfilled


def test_scanner_rejects_future_first_input_order():
    bars = _daily_bars()['AAA']
    provider = FixtureHistoryProvider({})
    with pytest.raises(ValueError, match='strictly increasing availability'):
        build_scanner_inputs(
            bars_by_symbol={'AAA': list(reversed(bars))}, provider=provider,
            decision_date=date(2024, 11, 29), lookback_sessions=1, min_dollar_volume=D(1),
        )


def test_failed_session_cannot_be_silently_reused_or_advanced():
    coordinator = _coordinator(
        Portfolio(D(10000), D(10000), D(0)),
        execution_factory=lambda _: ExecutionPolicy(flatten_at_ms=35280000),
    )
    with pytest.raises(IncompleteSessionError):
        coordinator.run_session(date(2024, 11, 29), _daily_bars(), _SCANNER_AT_0946)
    with pytest.raises(ValueError, match='strictly increasing'):
        coordinator.run_session(date(2024, 11, 29), {}, {})
    with pytest.raises(ValueError, match='failed session'):
        coordinator.run_session(date(2024, 12, 2), {}, {})
    assert coordinator.results == []


def test_exposure_clips_to_regular_session_and_unions_real_clock_intervals():
    opening = _ms('2024-11-29T09:30:00')
    fills = [
        FillRecord(opening - 60_000, 'AAA', 'FILLED'),
        FillRecord(opening + 60_000, 'AAA', 'EXIT'),
        FillRecord(opening + 30_000, 'BBB', 'FILLED'),
        FillRecord(_ms('2024-11-29T13:10:00'), 'BBB', 'EXIT'),
    ]
    result = compute_exposure([SessionResult(date(2024, 11, 29), 0, [], fills, [], D(1000))])
    assert result.fraction == 1
    assert result.open_ms == 210 * 60_000

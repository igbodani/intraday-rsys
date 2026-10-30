"""Subsequent-trade execution adapter tests (M2-01T, docs/M2_IMPLEMENTATION_HANDOFF.md
step 5): T01-T09."""
from __future__ import annotations

from decimal import Decimal

from rsys.orders import AcceptedIntent, Position, Proposal
from rsys.trade_execution import FILLED, SKIPPED, Trade, TradeExecutionAdapter


def _intent(*, decision_time_ms=1_000, decision_latency_ms=0, stop="98") -> AcceptedIntent:
    proposal = Proposal(
        symbol="AAA", quantity=Decimal(5), signal_price=Decimal(100), stop=Decimal(stop), target=None,
        decision_time_ms=decision_time_ms, decision_latency_ms=decision_latency_ms,
    )
    return AcceptedIntent(proposal=proposal, reserved_cash=Decimal(500))


def test_t01_signal_trade_and_other_consumed_trades_cannot_self_fill():
    adapter = TradeExecutionAdapter()
    intent = _intent()
    consumed = frozenset({"t-signal", "t-also-consumed"})
    for trade_id in ("t-signal", "t-also-consumed"):
        trade = Trade(trade_id, event_time_ms=1_500, available_at_ms=1_500, price=Decimal(101))
        outcome = adapter.consider_entry(intent, consumed, trade)
        assert outcome.status == "REJECTED_SELF_CONSUMED"
    assert adapter.entry_trade_id is None


def test_t02_subsequent_eligible_trade_fills_zero_and_positive_latency_boundaries():
    # Zero latency: a trade timestamped at/after decision_time_ms is eligible immediately.
    adapter = TradeExecutionAdapter()
    intent = _intent(decision_time_ms=1_000, decision_latency_ms=0)
    trade = Trade("t-1", event_time_ms=1_001, available_at_ms=1_001, price=Decimal(101))
    outcome = adapter.consider_entry(intent, frozenset(), trade)
    assert outcome.status == FILLED
    assert outcome.price == Decimal(101)

    # Positive latency: a trade available before ready_at is not ready yet.
    adapter2 = TradeExecutionAdapter()
    intent2 = _intent(decision_time_ms=1_000, decision_latency_ms=500)
    too_early = Trade("t-2", event_time_ms=1_100, available_at_ms=1_100, price=Decimal(101))
    assert adapter2.consider_entry(intent2, frozenset(), too_early).status == "REJECTED_NOT_READY"
    ready = Trade("t-3", event_time_ms=1_600, available_at_ms=1_600, price=Decimal(101))
    assert adapter2.consider_entry(intent2, frozenset(), ready).status == FILLED


def test_t03_pre_activation_print_cannot_fill_even_if_delivered_later():
    adapter = TradeExecutionAdapter()
    intent = _intent(decision_time_ms=1_000)
    # Event time predates the decision, but it is "delivered" (available) after it.
    stale = Trade("t-late-arrival", event_time_ms=900, available_at_ms=1_500, price=Decimal(101))
    assert adapter.consider_entry(intent, frozenset(), stale).status == "REJECTED_STALE"


def test_t04_stop_and_target_use_the_observed_trade_sequence():
    position = Position(
        symbol="AAA", quantity=Decimal(5), entry_price=Decimal(100), basis=Decimal(500),
        stop=Decimal(98), target=Decimal(104), mark=Decimal(100),
    )
    adapter = TradeExecutionAdapter()
    adapter.activate_seeded_position("t-entry", 1000, 1000)
    # Target is touched BEFORE the stop in this observed sequence: target wins (no bar-mode
    # "stop always wins" rule applies, since the real order is known).
    target_first = Trade("t-a", event_time_ms=2_000, available_at_ms=2_000, price=Decimal(105))
    outcome = adapter.consider_exit(position, target_first)
    assert outcome.status == FILLED
    assert outcome.price == Decimal(104)  # conservative target reference, not the 105 print


def test_t05_entry_trade_cannot_also_close_the_new_position():
    position = Position(
        symbol="AAA", quantity=Decimal(5), entry_price=Decimal(100), basis=Decimal(500),
        stop=Decimal(98), target=Decimal(104), mark=Decimal(100),
    )
    adapter = TradeExecutionAdapter()
    adapter.activate_seeded_position("t-entry", 1000, 1000)
    entry_trade = Trade("t-entry", event_time_ms=1_500, available_at_ms=1_500, price=Decimal(97))
    outcome = adapter.consider_exit(position, entry_trade)
    assert outcome.status == "REJECTED_SELF_CONSUMED"


def test_t05b_gap_through_stop_uses_the_adverse_print_and_actual_entry_rejects_through_stop():
    intent = _intent(stop="98")
    adapter = TradeExecutionAdapter()
    gapped_entry = Trade("t-1", event_time_ms=1_500, available_at_ms=1_500, price=Decimal(95))
    assert adapter.consider_entry(intent, frozenset(), gapped_entry).status == "REJECTED_ENTRY_THROUGH_STOP"

    position = Position(
        symbol="AAA", quantity=Decimal(5), entry_price=Decimal(100), basis=Decimal(500),
        stop=Decimal(98), target=Decimal(104), mark=Decimal(100),
    )
    adapter.activate_seeded_position("t-entry", 1000, 1000)
    adverse_print = Trade("t-2", event_time_ms=2_000, available_at_ms=2_000, price=Decimal(96))
    outcome = adapter.consider_exit(position, adverse_print)
    assert outcome.status == FILLED
    assert outcome.price == Decimal(96)  # adverse print reference, not the old stop level


def test_t07_duplicate_delivery_and_correction_produce_no_duplicate_fill():
    adapter = TradeExecutionAdapter()
    intent = _intent()
    trade = Trade("t-1", event_time_ms=1_500, available_at_ms=1_500, price=Decimal(101))
    first = adapter.consider_entry(intent, frozenset(), trade)
    assert first.status == FILLED
    duplicate = adapter.consider_entry(intent, frozenset(), trade)  # redelivered
    assert duplicate.status == "ALREADY_FILLED"

    # A correction referencing a trade never already used as a fill reference blocks it.
    adapter2 = TradeExecutionAdapter()
    intent2 = _intent()
    adapter2.apply_correction("t-corrected")
    corrected = Trade("t-corrected", event_time_ms=1_500, available_at_ms=1_500, price=Decimal(101))
    assert adapter2.consider_entry(intent2, frozenset(), corrected).status == "REJECTED_STALE"


def test_t08_bad_quality_trade_is_unusable():
    adapter = TradeExecutionAdapter()
    intent = _intent()
    bad = Trade("t-1", event_time_ms=1_500, available_at_ms=1_500, price=Decimal(101), quality_ok=False)
    assert adapter.consider_entry(intent, frozenset(), bad).status == "REJECTED_BAD_QUALITY"


def test_t09_a_future_trade_cannot_change_an_earlier_fill():
    adapter = TradeExecutionAdapter()
    intent = _intent()
    first_eligible = Trade("t-1", event_time_ms=1_500, available_at_ms=1_500, price=Decimal(101))
    outcome = adapter.consider_entry(intent, frozenset(), first_eligible)
    assert outcome.status == FILLED and outcome.price == Decimal(101)

    later_trade = Trade("t-2", event_time_ms=3_000, available_at_ms=3_000, price=Decimal(999))
    replay_outcome = adapter.consider_entry(intent, frozenset(), later_trade)
    assert replay_outcome.status == "ALREADY_FILLED"
    assert outcome.price == Decimal(101)  # the original fill record is untouched


def test_no_eligible_trade_yet_is_skipped_not_a_fabricated_fill():
    position = Position(
        symbol="AAA", quantity=Decimal(5), entry_price=Decimal(100), basis=Decimal(500),
        stop=Decimal(98), target=Decimal(104), mark=Decimal(100),
    )
    adapter = TradeExecutionAdapter()
    adapter.activate_seeded_position("t-entry", 1000, 1000)
    flat_print = Trade("t-1", event_time_ms=2_000, available_at_ms=2_000, price=Decimal(101))
    assert adapter.consider_exit(position, flat_print).status == SKIPPED

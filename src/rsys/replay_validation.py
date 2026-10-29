"""Offline input-contract and source-coverage checks, separate from the replay clock."""

from __future__ import annotations

from collections import Counter

from rsys.data.events import (
    CorporateActionEvent,
    ProviderBarEvent,
    TimerEvent,
    TradeEvent,
)
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.replay import (
    IncompleteReplayError,
    ReplayProfile,
    UnsupportedPhaseError,
    event_phase,
)


class ReplayInputError(Exception):
    """Input cannot satisfy the declared offline replay contract."""


def validate_input_events(config, events):
    c = config.coverage
    start = parse_utc_timestamp_ns(c.window_start_at)
    end = parse_utc_timestamp_ns(c.required_boundary_at)
    through = parse_utc_timestamp_ns(c.available_through_at)
    identities = {}
    for event in events:
        try:
            event_phase(event, ReplayProfile(config.replay.ordering_profile))
        except UnsupportedPhaseError:
            raise ReplayInputError(
                "event is incompatible with the declared ordering profile"
            ) from None
        identity = (event.provider, event.feed, event.instrument_id, event.event_id)
        payload = event.model_dump()
        previous = identities.get(identity)
        if previous is not None and previous != payload:
            raise ReplayInputError("conflicting event identity in replay input")
        identities[identity] = payload
        if (
            event.provider != c.provider
            or event.feed != c.feed
            or event.session_version != c.session_version
        ):
            raise ReplayInputError(
                "event provider/feed/session does not match declared coverage"
            )
        if event.instrument_id not in c.instruments:
            raise ReplayInputError("event instrument is outside declared coverage")
        if not start <= event.available_at_ns <= through:
            raise ReplayInputError(
                "event availability is outside the declared replay window"
            )
        if isinstance(event, TimerEvent):
            if (
                event.calendar_version != c.session_version
                or event.event_time_meaning != "scheduled_boundary"
            ):
                raise ReplayInputError("timer calendar or time meaning is incompatible")
            continue
        if event.event_time_meaning != "provider_market_time":
            raise ReplayInputError("external observation requires provider market time")
        if config.replay.availability_model == "recorded_receipt":
            if event.timing_label != "recorded" or event.received_at_ns is None:
                raise ReplayInputError(
                    "recorded external observations require received_at evidence"
                )
        else:
            base = (
                event.bar_end_ns
                if isinstance(event, ProviderBarEvent)
                else event.event_time_ns
            )
            if isinstance(event, CorporateActionEvent):
                base = max(base, event.effective_at_ns, event.known_at_ns)
            expected = base + config.replay.modeled_delay_ms * 1_000_000
            if (
                event.timing_label != "modeled"
                or event.received_at_ns is not None
                or event.available_at_ns != expected
            ):
                raise ReplayInputError(
                    "event timing does not match the declared modeled delay"
                )
        if isinstance(event, TradeEvent) and not start <= event.event_time_ns < end:
            raise ReplayInputError(
                "trade falls outside the declared regular-session interval"
            )
        if isinstance(event, ProviderBarEvent):
            if not start <= event.bar_start_ns < event.bar_end_ns <= end:
                raise ReplayInputError(
                    "provider bar falls outside the declared interval"
                )
            if (
                event.interval != config.interval_policy.interval
                or event.adjustment_mode != "raw"
            ):
                raise ReplayInputError("provider interval/unit policy is incompatible")
        if config.replay.input_mode == "completed_bar" and isinstance(
            event, TradeEvent
        ):
            raise ReplayInputError("completed-bar input mode cannot contain raw trades")


def coverage_errors(config, document, events) -> list[str]:
    """Compare a source declaration to the requested window, not to generated timers.

    These are synthetic source assertions, not provider certification. Exact expected
    identities detect truncation; each requested minute separately states observation,
    verified no-trade, or missing delivery.
    """
    coverage = document.get("coverage")
    if not isinstance(coverage, dict):
        return ["source coverage declaration is missing"]
    try:
        covered_through = parse_utc_timestamp_ns(coverage["available_through"])
        expected = coverage["expected_event_ids"]
        intervals = coverage["intervals"]
        if not isinstance(expected, list) or not all(
            isinstance(i, str) for i in expected
        ):
            raise ValueError
        if not isinstance(intervals, list):
            raise TypeError
        if Counter(expected) != Counter(e.event_id for e in events):
            return ["source event inventory differs from delivered events"]
        if covered_through < parse_utc_timestamp_ns(
            config.coverage.available_through_at
        ):
            return ["source availability coverage ends before the requested boundary"]
        states = {}
        for item in intervals:
            key = (item["instrument_id"], parse_utc_timestamp_ns(item["bar_start"]))
            if key in states or item["state"] not in (
                "observed",
                "verified_no_trade",
                "missing_delivery",
            ):
                raise ValueError
            states[key] = item["state"]
        start = parse_utc_timestamp_ns(config.coverage.window_start_at)
        end = parse_utc_timestamp_ns(config.coverage.required_boundary_at)
        required = {
            (instrument, ns)
            for instrument in config.coverage.instruments
            for ns in range(start, end, 60_000_000_000)
        }
        if set(states) != required:
            return [
                "source interval coverage does not match requested instruments/window"
            ]
        for (instrument, ns), state in states.items():
            observations = [
                e
                for e in events
                if e.instrument_id == instrument
                and (
                    (
                        isinstance(e, TradeEvent)
                        and ns <= e.event_time_ns < ns + 60_000_000_000
                    )
                    or (isinstance(e, ProviderBarEvent) and e.bar_start_ns == ns)
                )
            ]
            authoritative = [
                e
                for e in observations
                if isinstance(
                    e,
                    TradeEvent
                    if config.bars.authority == "trade_builder"
                    else ProviderBarEvent,
                )
            ]
            if state == "missing_delivery":
                return [f"missing delivery: {instrument} at {ns}"]
            if state == "observed" and not authoritative:
                return [f"declared observation absent: {instrument} at {ns}"]
            if state == "verified_no_trade" and observations:
                return [
                    f"no-trade declaration conflicts with observations: {instrument} at {ns}"
                ]
            if any(e.quality_state != "ok" for e in authoritative):
                return [f"invalid authoritative observations: {instrument} at {ns}"]
    except (KeyError, TypeError, ValueError):
        return ["source coverage declaration is malformed"]
    return []


def require_coverage(config, document, events):
    errors = coverage_errors(config, document, events)
    if errors:
        raise IncompleteReplayError("; ".join(errors))

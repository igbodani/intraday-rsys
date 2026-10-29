"""Offline replay I/O: JSON fixture loading, the deterministic JSONL trace writer and the
`rsys replay` orchestration glue (R3-C, docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md 7.6).

This module only reads local fixture files and writes local output; it never makes a network
call or downloads market data.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from rsys.data.bar_rules import BarRuleSet, FixtureBarRuleSet
from rsys.data.events import (
    CorporateActionEvent,
    EventKind,
    MarketEvent,
    ProviderBarEvent,
    QuoteEvent,
    TimerEvent,
    TradeCorrectionEvent,
    TradeEvent,
)
from rsys.data.quotes import QuoteFreshnessPolicy
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.replay import Phase, ReplayProfile, event_phase
from rsys.replay_runtime import ReplayRunner, SeriesRouter


class ReplayInputError(Exception):
    """A replay input fixture file could not be read or parsed into valid events."""


class UnsupportedRuleSetError(Exception):
    """`bars.rule_set_id` does not resolve to a registered rule set; fixture-only rules cannot
    certify market data, and an unregistered name must never be silently accepted."""


# Declared fixture-only rule sets usable by an offline replay config. Never a stand-in for a
# verified provider rule table.
_REGISTERED_RULE_SETS: dict[str, BarRuleSet] = {
    "synthetic_test_fixture_rules:v1": FixtureBarRuleSet(),
    FixtureBarRuleSet.rule_set_id: FixtureBarRuleSet(),
}

_EVENT_MODELS: dict[EventKind, type[MarketEvent]] = {
    EventKind.TRADE: TradeEvent,
    EventKind.TRADE_CORRECTION: TradeCorrectionEvent,
    EventKind.QUOTE: QuoteEvent,
    EventKind.PROVIDER_BAR: ProviderBarEvent,
    EventKind.CORPORATE_ACTION: CorporateActionEvent,
    EventKind.TIMER: TimerEvent,
}

# Human-readable ISO-8601 field names in the fixture file -> the model's nanosecond field name.
_TIME_FIELD_MAP = {
    "event_time": "event_time_ns",
    "available_at": "available_at_ns",
    "received_at": "received_at_ns",
    "bar_start": "bar_start_ns",
    "bar_end": "bar_end_ns",
    "effective_at": "effective_at_ns",
    "known_at": "known_at_ns",
}


def _convert_fixture_record(raw: dict[str, Any]) -> dict[str, Any]:
    converted = dict(raw)
    for text_field, ns_field in _TIME_FIELD_MAP.items():
        if text_field in converted and converted[text_field] is not None:
            converted[ns_field] = parse_utc_timestamp_ns(converted.pop(text_field))
        else:
            converted.pop(text_field, None)
    if converted.get("conditions") is not None:
        converted["conditions"] = tuple(converted["conditions"])
    return converted


def load_events_from_json(path: str | Path) -> list[MarketEvent]:
    """Load a declared, offline-synthetic event fixture: `{"events": [...]}`, each entry
    carrying a `kind` discriminator and human-readable ISO-8601 time fields."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ReplayInputError(f"{path}: cannot read replay input file ({exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReplayInputError(f"{path}: malformed JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ReplayInputError(f"{path}: expected a top-level object with an 'events' list")

    events: list[MarketEvent] = []
    for index, raw in enumerate(data["events"]):
        if not isinstance(raw, dict) or "kind" not in raw:
            raise ReplayInputError(f"{path}: events[{index}] is missing a 'kind'")
        try:
            kind = EventKind(raw["kind"])
        except ValueError as exc:
            raise ReplayInputError(f"{path}: events[{index}] has unsupported kind {raw['kind']!r}") from exc
        model = _EVENT_MODELS[kind]
        try:
            events.append(model.model_validate(_convert_fixture_record(raw)))
        except (ValueError, TypeError) as exc:
            event_id = raw.get("event_id", "?")
            raise ReplayInputError(f"{path}: events[{index}] ({event_id!r}) is invalid: {exc}") from exc
    return events


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)


def compute_semantic_hash(records: list[dict[str, Any]]) -> str:
    """A deterministic hash over trace content only -- never over volatile run_id/wall-clock
    fields, which this function never receives in the first place."""
    return hashlib.sha256(_canonical_json(records).encode("utf-8")).hexdigest()


def write_trace_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(_canonical_json(record))
            handle.write("\n")


def _bar_version_record(snapshot: Any) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "bar_start_ns": snapshot.bar_start_ns, "bar_end_ns": snapshot.bar_end_ns,
        "status": snapshot.status.value, "revision": snapshot.revision,
        "open": snapshot.open, "high": snapshot.high, "low": snapshot.low, "close": snapshot.close,
        "volume": snapshot.volume, "trade_count": snapshot.trade_count,
        "vwap_numerator": snapshot.vwap_numerator, "vwap_denominator": snapshot.vwap_denominator,
        "rule_set_id": snapshot.rule_set_id,
    }


def build_trace_records(
    runner: ReplayRunner, router: SeriesRouter | None, *, observation_mode: str, quotes_enabled: bool,
) -> list[dict[str, Any]]:
    """One record per dispatched event: event/publication keys, source identity, quality and,
    when a trade-authority router is wired, the snapshot/closure directly RESULTING from that
    one event -- never a post-hoc "current state" lookup, which would leak later events into an
    earlier record (the exact C06 defect this module exists to avoid)."""
    records: list[dict[str, Any]] = []
    for event, result in zip(runner.processed, runner.results, strict=True):
        record: dict[str, Any] = {
            "event_id": event.event_id,
            "kind": event.kind.value,
            "instrument_id": event.instrument_id,
            "provider": event.provider,
            "feed": event.feed,
            "event_time_ns": event.event_time_ns,
            "available_at_ns": event.available_at_ns,
            "source_sequence": event.source_sequence,
            "quality_state": event.quality_state.value,
            "timing_label": event.timing_label.value,
            "observation_mode": observation_mode,
        }
        if isinstance(event, TimerEvent) and isinstance(result, dict):
            closures = {
                instrument_id: [_bar_version_record(s) for s in snapshots]
                for instrument_id, snapshots in result.items() if snapshots
            }
            if closures:
                record["closures"] = closures
        else:
            bar_reference = _bar_version_record(result)
            if bar_reference is not None:
                record["bar_version"] = bar_reference
        if quotes_enabled and isinstance(event, QuoteEvent) and router is not None:
            quote_state = router.quote_state_for(event.instrument_id)
            if quote_state is not None and quote_state.latest() is not None:
                latest = quote_state.latest()
                record["quote"] = {"condition": latest.condition.value, "source_event_id": latest.source_event_id}
        records.append(record)
    return records


@dataclass(frozen=True)
class ReplayRunResult:
    trace_path: Path
    manifest_path: Path
    semantic_hash: str
    complete: bool
    incomplete_reason: str | None
    event_count: int


def run_replay_from_config(config: Any, out_dir: Path) -> ReplayRunResult:
    """Drive one offline replay end to end from an already-validated `ReplayConfig`."""
    events = load_events_from_json(config.input)
    profile = ReplayProfile(config.replay.ordering_profile)

    router: SeriesRouter | None = None
    if config.bars.authority == "trade_builder":
        rule_set = _REGISTERED_RULE_SETS.get(config.bars.rule_set_id)
        if rule_set is None:
            raise UnsupportedRuleSetError(f"bars.rule_set_id {config.bars.rule_set_id!r} is not registered")
        quote_policy = None
        if config.quotes.enabled:
            quote_policy = QuoteFreshnessPolicy(freshness_limit_ms=config.quotes.freshness_limit_ms)
        feed = events[0].feed if events else "fixture"
        router = SeriesRouter(
            feed, config.interval_policy.interval, rule_set,
            retention_limit=config.revision.retention_limit, quote_freshness_policy=quote_policy,
        )

    runner = ReplayRunner(events, profile, router)
    processed = runner.run_to_completion()

    complete = any(event_phase(e, profile) == Phase.TIMER and isinstance(e, TimerEvent) for e in processed)
    incomplete_reason = None if complete else "no scheduled-boundary timer was dispatched; coverage is unproven"

    records = build_trace_records(
        runner, router, observation_mode=config.observation.mode, quotes_enabled=config.quotes.enabled,
    )
    semantic_hash = compute_semantic_hash(records)

    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / "trace.jsonl"
    manifest_path = out_dir / "manifest.json"
    write_trace_jsonl(records, trace_path)
    manifest_path.write_text(
        _canonical_json({
            "schema_version": 1,
            "mode": config.mode,
            "complete": complete,
            "incomplete_reason": incomplete_reason,
            "semantic_trace_hash": semantic_hash,
            "event_count": len(processed),
            "bars_authority": config.bars.authority,
            "ordering_profile": config.replay.ordering_profile,
        }),
        encoding="utf-8",
    )
    return ReplayRunResult(trace_path, manifest_path, semantic_hash, complete, incomplete_reason, len(processed))

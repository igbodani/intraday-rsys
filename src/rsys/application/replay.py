"""Offline replay I/O: JSON fixture loading, the deterministic JSONL trace writer and the
`rsys replay` orchestration glue (R3-C, docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md 7.6).

This module only reads local fixture files and writes local output; it never makes a network
call or downloads market data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from rsys.application.errors import _IncompleteReplayMarker
from rsys.application.models import ReplayRunResult
from rsys.data.bar_rules import BarRuleSet, FixtureBarRuleSet
from rsys.data.errors import ReplayInputError
from rsys.data.models import (
    BarCloseEvent,
    CorporateActionEvent,
    EventKind,
    MarketEvent,
    ProviderBarEvent,
    QuoteEvent,
    QuoteFreshnessPolicy,
    TimerEvent,
    TradeCorrectionEvent,
    TradeEvent,
)
from rsys.data.timestamps import parse_utc_timestamp_ns
from rsys.replay.clock import event_phase
from rsys.replay.models import ReplayProfile
from rsys.replay.runtime import ReplayRunner, SeriesRouter
from rsys.replay.validation import coverage_errors, validate_input_events
from rsys.research.experiments import run_context, start_run
from rsys.research.storage import RunStorage

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


def decode_input(input_bytes: bytes) -> tuple[dict, list[MarketEvent]]:
    try:
        data = json.loads(input_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayInputError("replay input must be valid UTF-8 JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ReplayInputError("replay input requires an events list")
    events = []
    for index, raw in enumerate(data["events"]):
        try:
            if not isinstance(raw, dict):
                raise TypeError
            kind = EventKind(raw["kind"])
            events.append(
                _EVENT_MODELS[kind].model_validate(_convert_fixture_record(raw))
            )
        except (KeyError, ValueError, TypeError):
            # Validation may include submitted values. Keep persisted/CLI error text safe.
            raise ReplayInputError(f"invalid replay event at index {index}") from None
    return data, events


def load_events_from_json(path: str | Path) -> list[MarketEvent]:
    try:
        return decode_input(Path(path).read_bytes())[1]
    except OSError as exc:
        raise ReplayInputError("cannot read replay input file") from exc


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=_json_default
    )


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
        "bar_start_ns": snapshot.bar_start_ns,
        "bar_end_ns": snapshot.bar_end_ns,
        "status": snapshot.status.value,
        "revision": snapshot.revision,
        "available_at_ns": snapshot.available_at_ns,
        "publication_phase": snapshot.publication_phase,
        "publication_sequence": snapshot.publication_sequence,
        "open": snapshot.open,
        "high": snapshot.high,
        "low": snapshot.low,
        "close": snapshot.close,
        "volume": snapshot.volume,
        "trade_count": snapshot.trade_count,
        "vwap_numerator": snapshot.vwap_numerator,
        "vwap_denominator": snapshot.vwap_denominator,
        "rule_set_id": snapshot.rule_set_id,
    }


def build_trace_records(
    runner: ReplayRunner,
    router: SeriesRouter | None,
    *,
    observation_mode: str,
    quotes_enabled: bool,
) -> list[dict[str, Any]]:
    """One record per dispatched event: event/publication keys, source identity, quality and,
    when a trade-authority router is wired, the snapshot/closure directly RESULTING from that
    one event -- never a post-hoc "current state" lookup, which would leak later events into an
    earlier record (the exact C06/R3I-04 defect this module exists to avoid)."""
    records: list[dict[str, Any]] = []
    for event, result in zip(runner.processed, runner.results, strict=True):
        record: dict[str, Any] = {
            "input": event.model_dump(mode="json"),
            "event_id": event.event_id,
            "phase": int(event_phase(event, runner._profile)),
            "internal": isinstance(event, BarCloseEvent),
            "received_at_ns": event.received_at_ns,
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
        if isinstance(event, ProviderBarEvent):
            # R3I-03: the provider's own OHLCV/count/VWAP/revision must be captured verbatim --
            # without this, a changed provider price never shows up in the trace or its hash.
            record["provider_bar"] = {
                "bar_start_ns": event.bar_start_ns,
                "bar_end_ns": event.bar_end_ns,
                "interval": event.interval,
                "open": event.open,
                "high": event.high,
                "low": event.low,
                "close": event.close,
                "volume": event.volume,
                "trade_count": event.trade_count,
                "vwap": event.vwap,
                "adjustment_mode": event.adjustment_mode,
                "bar_revision": event.bar_revision,
            }
        elif isinstance(event, CorporateActionEvent):
            # R3I-07: a straddled bucket's invalidation must be visible in the trace, never
            # silently dropped as if the action had no effect.
            record["corporate_action"] = {
                "action_type": event.action_type,
                "ratio": event.ratio,
                "effective_at_ns": event.effective_at_ns,
                "known_at_ns": event.known_at_ns,
            }
            if isinstance(result, dict):
                invalidated = {
                    instrument_id: _bar_version_record(s)
                    for instrument_id, s in result.items()
                    if s is not None
                }
                if invalidated:
                    record["corporate_action"]["invalidated_buckets"] = invalidated
        elif isinstance(event, TimerEvent) and isinstance(result, dict):
            closures = {
                instrument_id: [_bar_version_record(s) for s in snapshots]
                for instrument_id, snapshots in result.items()
                if snapshots
            }
            if closures:
                record["closures"] = closures
        elif quotes_enabled and isinstance(event, QuoteEvent):
            # R3I-04: use the snapshot RETURNED by applying THIS quote, never a later re-query
            # of "the current quote state" -- that would leak a future quote into this row.
            if result is not None:
                record["quote"] = {
                    "condition": result.condition.value,
                    "source_event_id": result.source_event_id,
                    "bid_price": result.bid_price,
                    "ask_price": result.ask_price,
                    "bid_size": result.bid_size,
                    "ask_size": result.ask_size,
                    "size_unit": result.size_unit,
                }
        else:
            bar_reference = _bar_version_record(result)
            if bar_reference is not None:
                record["bar_version"] = bar_reference
        records.append(record)
    for index, evaluation in enumerate(runner.evaluations):
        records.append(
            {
                "event_id": f"observer:{evaluation.observer_name}:{evaluation.instrument_id}:{evaluation.decision_key_ns}:{index}",
                "kind": "observation",
                "available_at_ns": evaluation.decision_key_ns,
                "phase": evaluation.decision_phase,
                "source_sequence": None,
                **asdict(evaluation),
            }
        )
    # Stable sort preserves the runner's sequence/tie order within source-event phases.
    records.sort(key=lambda r: (r["available_at_ns"], r["phase"]))
    return records


class SyntheticObserver:
    """Named fixture-only diagnostics. No orders, account mutation or strategy claims."""

    def __init__(self, settings):
        self.name = settings.observer_id
        self.mode = settings.mode
        self.cadence = settings.cadence
        self.on_revisions = settings.on_revisions
        self.threshold = settings.threshold

    def decide(self, context, instrument_id):
        if self.name == "synthetic_closed_recorder:v1":
            bars = context.closed_bars(instrument_id)
            return {
                "closed_count": len(bars),
                "last_close": bars[-1].close if bars else None,
            }
        forming = context.forming_bar(instrument_id)
        if (
            forming is not None
            and forming.close is not None
            and forming.close >= self.threshold
        ):
            return {"crossed": True, "close": forming.close}
        return None


def run_replay_from_config(config: Any, out_dir: Path) -> ReplayRunResult:
    """One retained attempt, including unreadable input; source coverage is independent of timers."""
    from rsys.application.models import ReplayConfig

    config = ReplayConfig.model_validate(config.model_dump(mode="json"))
    input_bytes = None
    read_error = None
    try:
        input_bytes = Path(config.input).read_bytes()
    except OSError as exc:
        read_error = exc
    data_identity = (
        f"sha256:{hashlib.sha256(input_bytes).hexdigest()}"
        if input_bytes is not None
        else None
    )
    storage = RunStorage(out_dir)
    try:
        record = start_run(
            storage,
            hypothesis="Offline synthetic replay: causal observations and deterministic evidence.",
            phase="replay",
            resolved_config=config.model_dump(mode="json"),
            data_identity=data_identity,
            data_unavailable_reason="input could not be read"
            if read_error is not None
            else None,
            repo_root=Path(__file__).resolve().parents[3],
        )
        attempt_dir = out_dir / "attempts" / record.run_id
        attempt_dir.mkdir(parents=True, exist_ok=False)
        trace_path, manifest_path = (
            attempt_dir / "trace.jsonl",
            attempt_dir / "manifest.json",
        )
        complete, reason, semantic_hash, event_count = False, None, "", 0
        router, runner, document = None, None, {}
        try:
            with run_context(storage, record):
                try:
                    if read_error is not None:
                        raise ReplayInputError(
                            "cannot read replay input file"
                        ) from read_error
                    document, events = decode_input(input_bytes)
                    validate_input_events(config, events)
                    profile = ReplayProfile(config.replay.ordering_profile)
                    quote_policy = (
                        QuoteFreshnessPolicy(
                            freshness_limit_ms=config.quotes.freshness_limit_ms
                        )
                        if config.quotes.enabled
                        else None
                    )
                    if (
                        config.bars.authority == "trade_builder"
                        or config.quotes.enabled
                    ):
                        router = SeriesRouter(
                            config.coverage.feed,
                            config.interval_policy.interval,
                            _REGISTERED_RULE_SETS[config.bars.rule_set_id],
                            retention_limit=config.revision.retention_limit,
                            quote_freshness_policy=quote_policy,
                        )
                    runner = ReplayRunner(
                        events,
                        profile,
                        router,
                        authority=config.bars.authority,
                        quotes_enabled=config.quotes.enabled,
                        window_start_ns=parse_utc_timestamp_ns(
                            config.coverage.window_start_at
                        ),
                        window_end_ns=parse_utc_timestamp_ns(
                            config.coverage.required_boundary_at
                        ),
                    )
                    if config.observation.observer_id != "none":
                        runner.run_with_observers(
                            [SyntheticObserver(config.observation)],
                            config.coverage.instruments,
                        )
                    else:
                        runner.run_to_completion()
                    event_count = len(events)
                    problems = coverage_errors(config, document, events)
                    if config.bars.authority == "provider_publication" and any(
                        bar.bar_start_ns < action.effective_at_ns < bar.bar_end_ns
                        for bar in events
                        if isinstance(bar, ProviderBarEvent)
                        for action in events
                        if isinstance(action, CorporateActionEvent)
                        and action.instrument_id == bar.instrument_id
                    ):
                        problems.append("unsupported corporate-action unit transition")
                    if router and any(
                        v.status == "invalidated" for v in router.frozen_versions()
                    ):
                        problems.append("unsupported corporate-action unit transition")
                    if problems:
                        raise _IncompleteReplayMarker("; ".join(problems))
                    complete = True
                except BaseException as exc:
                    reason = (
                        str(exc)
                        if isinstance(exc, (ReplayInputError, _IncompleteReplayMarker))
                        else type(exc).__name__
                    )
                    error_path = attempt_dir / "error.json"
                    error_path.write_text(
                        _canonical_json(
                            {"error_type": type(exc).__name__, "message": reason}
                        ),
                        encoding="utf-8",
                    )
                    record.artifacts.append(str(error_path))
                    raise
                finally:
                    records = (
                        build_trace_records(
                            runner,
                            router,
                            observation_mode=config.observation.mode,
                            quotes_enabled=config.quotes.enabled,
                        )
                        if runner
                        else []
                    )
                    semantic_hash = compute_semantic_hash(records)
                    write_trace_jsonl(records, trace_path)
                    rule_id = config.bars.rule_set_id
                    manifest_path.write_text(
                        _canonical_json(
                            {
                                "schema_version": 2,
                                "mode": config.mode,
                                "run_id": record.run_id,
                                "complete": complete,
                                "incomplete_reason": reason,
                                "semantic_trace_hash": semantic_hash,
                                "event_count": event_count,
                                "bars_authority": config.bars.authority,
                                "ordering_profile": config.replay.ordering_profile,
                                "config_hash": record.config_hash,
                                "data_hash": record.data_hash,
                                "code_hash": record.code_hash,
                                "dependency_lock_hash": record.dependency_lock_hash,
                                "rule_set_id": rule_id,
                                "rule_implementation_hash": hashlib.sha256(
                                    (
                                        Path(__file__).parents[1]
                                        / "data"
                                        / "bar_rules.py"
                                    ).read_bytes()
                                ).hexdigest(),
                                "timing": config.replay.model_dump(mode="json"),
                                "coverage": config.coverage.model_dump(mode="json"),
                                "source_coverage": document.get("coverage"),
                                "observer": config.observation.model_dump(mode="json"),
                                "evaluation_count": len(runner.evaluations)
                                if runner
                                else 0,
                            }
                        ),
                        encoding="utf-8",
                    )
                    record.artifacts.extend([str(trace_path), str(manifest_path)])
                    record.metrics = {
                        "event_count": event_count,
                        "semantic_trace_hash": semantic_hash,
                        "complete": complete,
                    }
        except _IncompleteReplayMarker:
            pass
        return ReplayRunResult(
            record.run_id,
            trace_path,
            manifest_path,
            semantic_hash,
            complete,
            reason,
            event_count,
        )
    finally:
        storage.close()

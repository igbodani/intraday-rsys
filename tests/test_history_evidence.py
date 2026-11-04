"""Lossless compaction, old IDs/readers, bounded writes and failure controls."""

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from test_public_history_scanner import build_fixture, run

from rsys.data.history_evidence import (
    HistoryArchive,
    HistorySnapshots,
    history_archive,
    read_history_archive,
)
from rsys.data.models import (
    HistorySession,
    MinuteObservation,
    ObservationStatus,
    SelectedSession,
    UnitBasis,
)
from rsys.research import diagnostics
from rsys.research.errors import DiagnosticIntegrityError
from rsys.research.trace import verify_streamed_artifacts
from rsys.strategies.minute_scanner import MinuteHistoryScanner


def original(value):
    payload = json.loads(json.dumps(asdict(value), default=str))
    identity = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return identity, payload


def observations(count):
    return tuple(
        MinuteObservation(
            "AAA",
            "fixture",
            "<script>source</script>",
            date(2024, 11, 27),
            m,
            datetime(2024, 11, 28, tzinfo=UTC),
            0,
            ObservationStatus.TRADED,
            UnitBasis.RAW,
            Decimal(100),
            Decimal(1),
        )
        for m in range(count)
    )


def snapshots(count=60):
    rows = observations(count)
    return [
        SelectedSession(
            HistorySession(date(2024, 11, 27), 390, Decimal(m), True), rows[:m], None
        )
        for m in range(1, count + 1)
    ]


def test_every_snapshot_resolves_to_original_bytes_and_prefix_storage_is_linear(
    tmp_path,
):
    store, expected = HistorySnapshots(), {}
    for value in snapshots():
        identity, payload = original(value)
        assert store.retain(value) == identity
        expected[identity] = payload
    archive = history_archive([store, store])
    assert (
        len(archive["observations"])
        == len(archive["sequences"])
        == len(archive["snapshots"])
        == 60
    )
    assert sum(len(p["selected_revisions"]) for p in expected.values()) == 1830
    assert len(json.dumps(archive)) < len(json.dumps(expected)) // 3
    path = tmp_path / "history.json"
    path.write_text(json.dumps(archive))
    restored = read_history_archive(path, version=2)
    assert dict(restored) == expected
    for identity, payload in expected.items():
        assert store[identity] == payload
        restored[identity]["selected_revisions"].clear()
        store[identity]["selected_revisions"][0]["source"] = "mutated"
        assert restored[identity] == store[identity] == payload
    assert dict(HistoryArchive(expected, version=1)) == expected


def test_revision_missing_zero_and_numeric_spelling_are_retained_exactly():
    store = HistorySnapshots()
    row = observations(1)[0]
    cases = [
        replace(row, volume=Decimal("1.00")),
        row,
        replace(row, revision=1, volume=Decimal(9)),
        replace(row, status=ObservationStatus.NO_TRADE, close=None, volume=Decimal(0)),
    ]
    identities = []
    for observation in cases:
        value = SelectedSession(
            HistorySession(row.session_date, 390, observation.volume, True),
            (observation,),
            None,
        )
        identity, payload = original(value)
        assert store.retain(value) == identity
        assert store[identity] == payload
        identities.append(identity)
    missing = SelectedSession(
        HistorySession(row.session_date, 390, None, False), (), "MISSING_MINUTE"
    )
    identity, payload = original(missing)
    assert store.retain(missing) == identity and store[identity] == payload
    assert len(set(identities)) == 4
    assert len(store.observations) == 4


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_compacted_and_original_retain_algorithm_produce_identical_decisions_fills_and_traces(
    monkeypatch, mode
):
    config = build_fixture(mode)
    compact = run(config)
    expected = {}

    def retain_original(scanner, value):
        identity, payload = original(value)
        expected[identity] = payload
        return identity

    monkeypatch.setattr(MinuteHistoryScanner, "_retain", retain_original)
    legacy = run(config)
    assert compact.summary() == legacy.summary()
    assert compact.trace == legacy.trace
    assert len(compact.summary()["fills"]) == 4
    assert (
        dict(
            HistoryArchive(
                history_archive(r.history_scanner.snapshots for r in compact.runners)
            )
        )
        == expected
    )


@pytest.mark.parametrize(
    "damage",
    [
        "observation",
        "missing_observation",
        "sequence",
        "missing_sequence",
        "snapshot",
        "missing_snapshot",
        "count",
        "version",
        "boolean_version",
        "symbols_shape",
        "symbol_shape",
        "references_shape",
        "reference_shape",
    ],
)
def test_internal_integrity_and_decision_references_are_checked_even_after_outer_rehash(
    tmp_path, damage
):
    store = HistorySnapshots()
    identity = store.retain(snapshots(3)[-1])
    payload = history_archive([store])
    if damage == "observation":
        next(iter(payload["observations"].values()))["volume"] = "9"
    elif damage == "missing_observation":
        payload["observations"].pop(next(iter(payload["observations"])))
    elif damage == "sequence":
        next(iter(payload["sequences"].values()))["previous"] = "f" * 64
    elif damage == "missing_sequence":
        payload["sequences"].pop(next(iter(payload["sequences"])))
    elif damage == "snapshot":
        payload["snapshots"][identity]["header"]["exclusion_reason"] = "invented"
    elif damage == "missing_snapshot":
        payload["snapshots"].pop(identity)
    elif damage == "count":
        payload["snapshots"][identity]["count"] += 1
    elif damage in ("version", "boolean_version"):
        payload["schema_version"] = True if damage == "boolean_version" else 99
    (tmp_path / "scanner_history.json").write_text(json.dumps(payload))
    for name in ("input.json", "resolved_config.json"):
        (tmp_path / name).write_text("{}")
    row = {
        "kind": "decision",
        "event_id": "d",
        "available_at_ns": 1,
        "phase": 40,
        "account": {},
        "scanner_evidence": {
            "symbols": {"AAA": {"history_sessions": [{"snapshot_id": identity}]}}
        },
    }
    if damage == "symbols_shape":
        row["scanner_evidence"]["symbols"] = []
    elif damage == "symbol_shape":
        row["scanner_evidence"]["symbols"]["AAA"] = None
    elif damage == "references_shape":
        row["scanner_evidence"]["symbols"]["AAA"]["history_sessions"] = "bad"
    elif damage == "reference_shape":
        row["scanner_evidence"]["symbols"]["AAA"]["history_sessions"] = [None]
    (tmp_path / "trace.jsonl").write_text(json.dumps(row) + "\n")
    hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
    }
    manifest = {
        "trace_version": 1,
        "scanner_history_version": 2,
        "artifact_hashes": hashes,
        "semantic_trace_hash": hashes["trace.jsonl"],
        "trace_records": 1,
    }
    with pytest.raises(DiagnosticIntegrityError):
        verify_streamed_artifacts(tmp_path, manifest)


def test_atomic_writer_preserves_old_bytes_and_uses_bounded_chunks(
    tmp_path, monkeypatch
):
    value = {
        "rows": [
            {"i": i, "amount": Decimal("1.20"), "source": "x" * 200}
            for i in range(4000)
        ],
        "keys": {2: "two", 10: "ten", "2": "last", None: [True, date(2024, 1, 1)]},
    }
    expected = json.dumps(
        json.loads(json.dumps(value, default=str)), indent=2, sort_keys=True
    ).encode()
    writes = []
    real_open = Path.open

    class RecordingFile:
        def __init__(self, handle):
            self.handle = handle

        def write(self, data):
            writes.append(len(data))
            return self.handle.write(data)

        def __getattr__(self, name):
            return getattr(self.handle, name)

    def opened(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        return RecordingFile(handle) if mode == "wb" else handle

    monkeypatch.setattr(Path, "open", opened)
    real_dumps = json.dumps

    def no_whole_tree(obj, *args, **kwargs):
        assert obj is not value, "whole tree serialized again"
        return real_dumps(obj, *args, **kwargs)

    monkeypatch.setattr(json, "dumps", no_whole_tree)
    path = tmp_path / "state.json"
    diagnostics.atomic_write_json(path, value)
    assert path.read_bytes() == expected
    assert len(writes) > 10 and max(writes) < 66000


def test_late_stream_write_failure_preserves_previous_atomic_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "state.json"
    path.write_bytes(b'{"previous":true}')
    real_open = Path.open

    class BrokenFile:
        def __init__(self, handle):
            self.handle, self.count = handle, 0

        def write(self, data):
            self.count += 1
            if self.count == 3:
                raise OSError("disk full after prefix")
            return self.handle.write(data)

        def __getattr__(self, name):
            return getattr(self.handle, name)

    def opened(target, mode="r", *args, **kwargs):
        handle = real_open(target, mode, *args, **kwargs)
        return BrokenFile(handle) if mode == "wb" else handle

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(OSError, match="disk full"):
        diagnostics.atomic_write_json(path, ["x" * 1000 for _ in range(1000)])
    assert path.read_bytes() == b'{"previous":true}'
    assert list(tmp_path.iterdir()) == [path]

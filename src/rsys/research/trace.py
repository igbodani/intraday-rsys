"""Canonical streaming account traces, separate from operational diagnostics.

Each record is flushed. Fills, session boundaries and finalization are synced.
Only an acknowledged prefix is promised after process termination; a torn tail
is never repaired or replaced using the runner's in-memory list.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from rsys.data.history_evidence import read_history_archive
from rsys.research.diagnostics import protect_evidence_write
from rsys.research.errors import DiagnosticIntegrityError, TraceWriteError
from rsys.research.models import TraceRead


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_trace(
    path: Path, *, expected_sha256=None, collect_records=True, record_sink=None
) -> TraceRead:
    """Read only complete newline-terminated objects; disclose an incomplete tail.
    Complete malformed rows are corruption, not permission to skip evidence.
    """
    digest, rows, count, incomplete = hashlib.sha256(), [], 0, False
    with Path(path).open("rb") as handle:
        for line in handle:
            digest.update(line)
            if not line.endswith(b"\n"):
                incomplete = True
                break
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise DiagnosticIntegrityError("invalid complete trace record") from exc
            if (
                not isinstance(row, dict)
                or not {"kind", "event_id", "available_at_ns", "phase", "account"}
                <= row.keys()
            ):
                raise DiagnosticIntegrityError("invalid trace record shape")
            count += 1
            if record_sink is not None:
                record_sink(row)
            if collect_records:
                rows.append(row)
    actual = digest.hexdigest()
    if expected_sha256 is not None and (incomplete or expected_sha256 != actual):
        raise DiagnosticIntegrityError("trace integrity mismatch")
    return TraceRead(tuple(rows), count, actual, incomplete)


def verify_streamed_artifacts(attempt: Path, manifest: dict) -> None:
    """Additional requirements only for explicitly versioned streaming evidence."""
    if type(manifest.get("trace_version")) is not int or manifest["trace_version"] != 1:
        raise DiagnosticIntegrityError("unsupported trace version")
    hashes = manifest.get("artifact_hashes", {})
    if not {"input.json", "resolved_config.json", "trace.jsonl"} <= hashes.keys():
        raise DiagnosticIntegrityError("missing required streamed artifacts")
    for name, expected in hashes.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise DiagnosticIntegrityError("invalid artifact path")
        if file_sha256(attempt / name) != expected:
            raise DiagnosticIntegrityError("retained artifact integrity mismatch")
    history = None
    if "scanner_history_version" in manifest or "scanner_history.json" in hashes:
        if "scanner_history.json" not in hashes:
            raise DiagnosticIntegrityError("missing scanner history artifact")
        version = manifest.get("scanner_history_version", 1)
        if type(version) is not int or version not in (1, 2):
            raise DiagnosticIntegrityError("unsupported scanner history version")
        try:
            history = read_history_archive(
                attempt / "scanner_history.json", version=version
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise DiagnosticIntegrityError("invalid scanner history archive") from exc

    def verify_references(row):
        evidence = row.get("scanner_evidence")
        if evidence is None or evidence == {}:
            return
        if not isinstance(evidence, dict) or not isinstance(
            evidence.get("symbols"), dict
        ):
            raise DiagnosticIntegrityError("invalid scanner evidence shape")
        for detail in evidence["symbols"].values():
            if not isinstance(detail, dict):
                raise DiagnosticIntegrityError("invalid scanner symbol evidence")
            snapshots = detail.get("history_sessions", [])
            if not isinstance(snapshots, list):
                raise DiagnosticIntegrityError("invalid scanner history references")
            if "previous_session" in detail:
                snapshots = [*snapshots, detail["previous_session"]]
            for snapshot in snapshots:
                if not isinstance(snapshot, dict) or not isinstance(
                    snapshot.get("snapshot_id"), str
                ):
                    raise DiagnosticIntegrityError("invalid decision history reference")
                if history is None or snapshot["snapshot_id"] not in history:
                    raise DiagnosticIntegrityError("missing decision history snapshot")

    state = read_trace(
        attempt / "trace.jsonl",
        expected_sha256=manifest["semantic_trace_hash"],
        collect_records=False,
        record_sink=verify_references,
    )
    if state.record_count != manifest.get("trace_records"):
        raise DiagnosticIntegrityError("trace record count mismatch")


class TraceWriter:
    """Exclusive, single-use writer with constant buffering and fail-stop I/O."""

    def __init__(self, path: Path, *, open_file=None):
        self.path = Path(path)
        self._error = None
        self._closed = False
        try:
            self._handle = (open_file or (lambda p: p.open("xb")))(self.path)
        except OSError as exc:
            raise TraceWriteError("could not create semantic trace") from exc

    def _check(self):
        if self._error is not None:
            raise self._error
        if self._closed:
            raise TraceWriteError("cannot append to a closed semantic trace")

    def _failed(self, exc):
        self._error = TraceWriteError("semantic trace write failed")
        raise self._error from exc

    def write(self, row: dict) -> None:
        self._check()
        line = (json.dumps(row, sort_keys=True, default=str) + "\n").encode("utf-8")
        try:
            if self._handle.write(line) != len(line):
                raise OSError("short semantic trace write")
            self._handle.flush()
            if row["kind"] == "fill":
                os.fsync(self._handle.fileno())
        except OSError as exc:
            self._failed(exc)

    def checkpoint(self):
        self._check()
        try:
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except OSError as exc:
            self._failed(exc)

    def close(self):
        if not self._closed:
            try:
                # Sync even a failed append's prefix, without retrying its row.
                self._handle.flush()
                os.fsync(self._handle.fileno())
            except OSError as exc:
                self._failed(exc)
            finally:
                self._closed = True
                try:
                    protect_evidence_write(self._handle.close, catch=(OSError,))
                except OSError as exc:
                    self._failed(exc)
        if self._error is not None:
            raise self._error

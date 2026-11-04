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

from rsys.research.diagnostics import protect_evidence_write
from rsys.research.errors import DiagnosticIntegrityError, TraceWriteError
from rsys.research.models import TraceRead


def read_trace(path: Path, *, expected_sha256=None, collect_records=True) -> TraceRead:
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
        if hashlib.sha256((attempt / name).read_bytes()).hexdigest() != expected:
            raise DiagnosticIntegrityError("retained artifact integrity mismatch")
    state = read_trace(
        attempt / "trace.jsonl",
        expected_sha256=manifest["semantic_trace_hash"],
        collect_records=False,
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

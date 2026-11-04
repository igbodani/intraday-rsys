"""Append-only operational journal and early invocation evidence (BR-01).

Two independent evidence streams exist side by side, and neither is a second
source of truth for a terminal research outcome:

- `invocation.json`: a small, atomically-replaced state summary (temp file plus
  `os.replace`, mirroring `data/bar_cache.py`'s existing atomic-write precedent).
  It is overwritten whole, never appended.
- `diagnostics.jsonl`: an append-only journal, one complete UTF-8 JSON object per
  line. Flushed after every record; synced (`os.fsync`) at lifecycle, failure and
  declared checkpoint boundaries. Once a line is written, it is never rewritten.

`RunStorage`'s SQLite commit (via `research.experiments.run_context`) remains the
one authoritative terminal-status store. An invocation can exist, retain failure
evidence, and never resolve to a run record at all -- a crash during preparation
is an expected, inspectable state here, not corruption.

Wall-clock timestamps in this module are operational metadata only: they never
drive a strategy decision and never enter the deterministic semantic trace hash
(see `application/batch.py`/`application/run.py`'s separate `trace.jsonl`).
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rsys.research.errors import DiagnosticIntegrityError, DiagnosticWriteError
from rsys.research.experiments import SecretRedactor, redact_secrets
from rsys.research.models import (
    DiagnosticEventCode,
    DiagnosticJournalRead,
    DiagnosticRecord,
    InvocationRecord,
)
from rsys.research.storage import _validate_run_id, new_run_id

_STAGE_PREPARING = "preparing"
_STAGE_RESOLVED = "resolved"
_STAGE_FINALIZING = "finalizing"
_STAGE_FAILED = "failed"

# Operational metadata only. Never accept whole requests, bars, headers or
# arbitrary provider payloads as diagnostic details.
_DETAIL_KEYS = frozenset(
    {
        "message",
        "error_type",
        "note",
        "nested",
        "api_key",
        "symbol",
        "date",
        "coverage_complete",
        "checked_dates",
        "loaded_dates",
        "permitted_dates",
        "dataset_count",
        "session_count",
        "page_count",
        "retry_count",
        "count",
        "event_id",
        "kind",
        "code",
        "eligible",
        "proposed",
        "reference_id",
        "available_at_ms",
        "completed_sessions",
        "total_sessions",
        "processed_events",
        "entry_fills",
        "exits",
    }
)

# Event codes whose loss would be unacceptable for the "kill-survival" guarantee:
# always fsynced immediately, not just flushed.
_SYNC_ON_CODES = frozenset(
    {
        DiagnosticEventCode.INVOCATION_START,
        DiagnosticEventCode.FAILURE,
        DiagnosticEventCode.ARTIFACTS_READY,
        DiagnosticEventCode.SESSION_END,
    }
)


def _default_clock() -> str:
    return datetime.now(UTC).isoformat()


def _default_open_append(path: Path):
    return open(path, "ab")


def _atomic_write_json(path: Path, value: Any, *, default=str) -> None:
    """Whole-file replace: an open invocation.json is always either the previous
    complete state or the new complete state, never a partial write (C-style
    temp-file-plus-replace, mirroring `data/bar_cache.py:BarCache._write`).

    `invocation.json` is rewritten several times per command in quick succession
    (unlike a content-addressed cache object written once), which is more exposed
    to a transient Windows file-replace contention window (e.g. a momentary
    antivirus/indexer lock on a just-created file). A short bounded retry absorbs
    that known OS-level flakiness without masking a genuine persistent failure
    (permissions, missing disk, read-only mount), which still raises after the
    last attempt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    attempts = 5
    try:
        handle = temp.open("wb")
        try:
            buffer = bytearray()
            for chunk in json.JSONEncoder(
                indent=2, sort_keys=True, default=default
            ).iterencode(value):
                buffer.extend(chunk.encode("utf-8"))
                if len(buffer) >= 65536:
                    if handle.write(buffer) != len(buffer):
                        raise OSError("short atomic state write")
                    buffer.clear()
            if buffer and handle.write(buffer) != len(buffer):
                raise OSError("short atomic state write")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            protect_evidence_write(handle.close, catch=(OSError,))
        for attempt in range(attempts):
            try:
                os.replace(temp, path)
                break
            except OSError:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.01 * (2**attempt))
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort cleanup only; the atomic replace above already succeeded or failed


def atomic_write_json(path: Path, value: Any, *, default=str) -> None:
    """Publish a complete JSON summary without exposing truncated replacements."""
    _atomic_write_json(path, _json_keys(value, default))


def _json_keys(value, default=str):
    """Preserve prior JSON conversion without serializing/parsing the tree.

    JSON-native branches retain their original containers. Only branches needing
    key/scalar conversion are copied; JSON's key collision rules still apply.
    """
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            return {
                next(iter(json.loads(json.dumps({key: None})))): _json_keys(
                    child, default
                )
                for key, child in value.items()
            }
        result = None
        for key, child in value.items():
            converted = _json_keys(child, default)
            if converted is not child:
                if result is None:
                    result = value.copy()
                result[key] = converted
        return value if result is None else result
    if isinstance(value, (list, tuple)):
        result = None
        for index, child in enumerate(value):
            converted = _json_keys(child, default)
            if converted is not child:
                if result is None:
                    result = list(value)
                result[index] = converted
        return value if result is None else result
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return _json_keys(default(value), default)


def read_diagnostic_journal(
    path: Path,
    *,
    expected_sha256: str | None = None,
    collect_records: bool = True,
    expected_invocation_id: str | None = None,
    expected_run_id: str | None = None,
) -> DiagnosticJournalRead:
    """Read a complete prefix without repairing bytes. A torn tail is incomplete;
    malformed complete lines, changed identities and sequence gaps are corruption.
    A prefix alone never proves a successful research outcome.
    """
    digest, records, count = hashlib.sha256(), [], 0
    invocation_id, run_id, incomplete = None, None, False
    with Path(path).open("rb") as handle:
        for line in handle:
            digest.update(line)
            if not line.endswith(b"\n"):
                incomplete = True
                break
            try:
                record = DiagnosticRecord.model_validate_json(line)
            except ValueError as exc:
                raise DiagnosticIntegrityError(
                    "invalid complete diagnostic record"
                ) from exc
            if record.schema_version != 1 or record.sequence != count + 1:
                raise DiagnosticIntegrityError("diagnostic version/sequence mismatch")
            if invocation_id is not None and record.invocation_id != invocation_id:
                raise DiagnosticIntegrityError("diagnostic invocation mismatch")
            if (
                expected_invocation_id is not None
                and record.invocation_id != expected_invocation_id
            ):
                raise DiagnosticIntegrityError("diagnostic invocation mismatch")
            if expected_run_id is not None and record.run_id not in (
                None,
                expected_run_id,
            ):
                raise DiagnosticIntegrityError("diagnostic run mismatch")
            if run_id is not None and record.run_id != run_id:
                raise DiagnosticIntegrityError("diagnostic run mismatch")
            invocation_id, run_id = record.invocation_id, record.run_id
            count += 1
            if collect_records:
                records.append(record)
    actual = digest.hexdigest()
    if expected_sha256 is not None and (incomplete or actual != expected_sha256):
        raise DiagnosticIntegrityError("sealed diagnostic journal integrity mismatch")
    return DiagnosticJournalRead(tuple(records), count, actual, incomplete)


def verify_diagnostic_seal(
    root: Path, seal: dict, *, run_id: str | None = None
) -> None:
    """Verify a seal pinned by the authoritative run/download record."""
    try:
        if type(seal["schema_version"]) is not int or seal["schema_version"] != 1:
            raise ValueError("unsupported diagnostic seal")
        expected_hash = seal["journal_sha256"]
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(c not in "0123456789abcdef" for c in expected_hash)
        ):
            raise ValueError("invalid diagnostic hash")
        invocation_id = seal["invocation_id"]
        _validate_run_id(invocation_id)
        directory = Path(root) / "invocations" / invocation_id
        state = InvocationRecord.model_validate_json(
            (directory / "invocation.json").read_bytes()
        )
        result = read_diagnostic_journal(
            directory / "diagnostics.jsonl",
            expected_sha256=expected_hash,
            collect_records=False,
            expected_invocation_id=invocation_id,
            expected_run_id=run_id,
        )
        if (
            state.schema_version != 1
            or state.ended_at is None
            or result.record_count == 0
            or state.invocation_id != invocation_id
            or state.journal_sha256 != seal["journal_sha256"]
            or state.journal_records != result.record_count
            or (run_id is not None and state.linked_run_id != run_id)
        ):
            raise ValueError("diagnostic seal metadata mismatch")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DiagnosticIntegrityError(
            "diagnostic evidence integrity mismatch"
        ) from exc


class DiagnosticWriter:
    """One invocation's early evidence: its `invocation.json` summary and its
    append-only `diagnostics.jsonl` journal. Never shared across concurrent
    invocations -- each gets its own directory and writer instance."""

    def __init__(
        self,
        invocation_dir: Path,
        *,
        invocation_id: str,
        command: str,
        redactor: SecretRedactor | None = None,
        clock: Callable[[], str] | None = None,
        open_append: Callable[[Path], Any] | None = None,
        observer: Callable[[DiagnosticRecord], None] | None = None,
    ):
        self.invocation_id = invocation_id
        self.command = command
        self._dir = invocation_dir
        self.journal_path = invocation_dir / "diagnostics.jsonl"
        self.invocation_path = invocation_dir / "invocation.json"
        self._redactor = redactor or SecretRedactor.unknown()
        self._clock = clock or _default_clock
        self._open_append = open_append or _default_open_append
        self._observer = observer
        self._sequence = 0
        self._sealed = False
        self._write_error: DiagnosticWriteError | None = None
        self._failed_exceptions: list[BaseException] = []
        self._record = InvocationRecord(
            invocation_id=invocation_id,
            command=command,
            created_at=self._clock(),
            stage=_STAGE_PREPARING,
            owner_pid=os.getpid(),
            owner_host=socket.gethostname(),
        )
        self._write_invocation()
        self.event(DiagnosticEventCode.INVOCATION_START, _STAGE_PREPARING)

    # -- invocation.json (atomic whole-state replace) ------------------------

    def _write_invocation(self) -> None:
        try:
            _atomic_write_json(
                self.invocation_path, json.loads(self._record.model_dump_json())
            )
        except OSError as exc:
            raise DiagnosticWriteError(
                f"could not persist invocation state: {self.invocation_path}"
            ) from exc

    def record_identity(
        self, *, input_path: str | None = None, input_sha256: str | None = None
    ) -> None:
        """Sanitized request identity only: a path and a readable-byte hash, never
        arbitrary raw bytes or secret values."""
        self._record.input_path = input_path
        self._record.input_sha256 = input_sha256
        self._write_invocation()

    def use_config(self, config: dict[str, Any]) -> None:
        """Capture configured secret values in memory before validation can fail."""
        self._redactor = SecretRedactor.from_resolved_config(config)

    def error_message(self, exc: BaseException) -> str:
        if self._redactor.is_known:
            return self._redactor.scrub(str(exc))
        return "<details withheld; no secret redactor available>"

    def _safe_details(self, value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {
                k: self._safe_details(v, k)
                for k, v in value.items()
                if k in _DETAIL_KEYS
            }
        if isinstance(value, (list, tuple)):
            return [self._safe_details(v, key) for v in value]
        if isinstance(value, str):
            if value == "REDACTED":
                return value
            if self._redactor.is_known:
                return self._redactor.scrub(value)
            # Only bounded structural identifiers are useful without a known
            # redaction context. Arbitrary notes/provider messages are withheld.
            if (
                key == "symbol"
                and value.isascii()
                and value.isalnum()
                and len(value) <= 12
            ):
                return value
            if key == "error_type" and value.isidentifier() and len(value) <= 80:
                return value
            if key == "date" and len(value) == 10:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                    return value
                except ValueError:
                    pass
            return "<details withheld; no secret redactor available>"
        return (
            value
            if value is None or isinstance(value, (bool, int, float))
            else "<withheld>"
        )

    def set_stage(self, stage: str) -> None:
        self._record.stage = stage
        self._write_invocation()

    def link_run(self, run_id: str) -> None:
        """Resolution succeeded: this invocation and `run_id` are one attempt, never
        two counted trials."""
        self._record.linked_run_id = run_id
        self._record.stage = _STAGE_RESOLVED
        self._write_invocation()

    def link_download_attempt(self, manifest_path: str) -> None:
        self._record.linked_download_attempt = manifest_path
        self._record.stage = _STAGE_RESOLVED
        self._write_invocation()

    def link_preflight_attempt(self, manifest_path: str) -> None:
        self._record.linked_preflight_attempt = manifest_path
        self._record.stage = _STAGE_RESOLVED
        self._write_invocation()

    @property
    def linked_run_id(self) -> str | None:
        return self._record.linked_run_id

    @property
    def stage(self) -> str:
        return self._record.stage

    @property
    def sequence(self) -> int:
        return self._sequence

    # -- diagnostics.jsonl (append-only journal) ------------------------------

    def event(
        self,
        code: DiagnosticEventCode,
        stage: str,
        *,
        details: dict[str, Any] | None = None,
        level: str = "info",
        simulation_time: str | None = None,
    ) -> None:
        """Append one complete UTF-8 JSON line. Flushed immediately; fsynced too when
        `code` is a kill-survival-critical boundary or `level == "error"`."""
        if self._write_error is not None:
            raise self._write_error
        if self._sealed:
            raise DiagnosticWriteError("cannot append to a sealed diagnostic journal")
        self._sequence += 1
        payload = {
            "schema_version": 1,
            "invocation_id": self.invocation_id,
            "run_id": self._record.linked_run_id,
            "sequence": self._sequence,
            "wall_time": self._clock(),
            "level": level,
            "event": code.value if isinstance(code, DiagnosticEventCode) else code,
            "stage": stage,
            "details": self._safe_details(redact_secrets(details or {})),
        }
        if simulation_time is not None:
            payload["simulation_time"] = simulation_time
        line = (json.dumps(payload, sort_keys=True, default=str) + "\n").encode("utf-8")
        should_sync = level == "error" or code in _SYNC_ON_CODES
        try:
            handle = self._open_append(self.journal_path)
            try:
                if handle.write(line) != len(line):
                    raise OSError("short diagnostic journal write")
                handle.flush()
                if should_sync:
                    os.fsync(handle.fileno())
            finally:
                protect_evidence_write(handle.close, catch=(OSError,))
        except OSError as exc:
            self._handle_write_failure(exc)
        if self._observer is not None:
            # A fresh value prevents a presentation consumer from changing evidence.
            self._observer(DiagnosticRecord.model_validate_json(line))

    def _handle_write_failure(self, exc: OSError) -> None:
        # Preserve the primary failure (re-raised below) and attempt one sanitized
        # secondary note; that secondary write is itself best-effort and never
        # assumed to succeed.
        try:
            self._record.stage = _STAGE_FAILED
            self._record.write_failure = (
                f"{type(exc).__name__}: diagnostics journal append failed"
            )
            self._write_invocation()
        except DiagnosticWriteError:
            pass
        self._write_error = DiagnosticWriteError(
            f"could not append to diagnostics journal: {self.journal_path}"
        )
        raise self._write_error from exc

    # -- lifecycle convenience -------------------------------------------------

    def fail(self, exc: BaseException, *, stage: str | None = None) -> None:
        """Record a distinct, readable failure before the caller re-raises `exc`.

        A secondary diagnostics-write failure here must never replace or hide the
        primary application exception (BR-01 T04: "preserve the primary failure and
        identify the secondary write problem when possible"). It is attached to
        `exc` as `exc.diagnostic_write_error` for the caller to report, never
        raised in `exc`'s place -- the caller's own `raise` must still surface
        `exc` with its original type intact. Only the first secondary failure is
        kept (matching `protect_evidence_write`'s own "first failure wins" rule),
        so an earlier write problem (e.g. from a `finally`-block finalization step
        already attached before `fail()` runs) is never quietly replaced by a
        later one."""
        exc.invocation_id = self.invocation_id
        exc.invocation_path = self.invocation_path
        exc.journal_path = self.journal_path
        exc.diagnostic_message = self.error_message(exc)
        if self.linked_run_id is not None:
            exc.run_id = self.linked_run_id
            attempt = self._dir.parent.parent / "attempts" / self.linked_run_id
            exc.trace_path = attempt / "trace.jsonl"
            exc.manifest_path = attempt / "manifest.json"
        if any(previous is exc for previous in self._failed_exceptions):
            return
        self._failed_exceptions.append(exc)
        if self._redactor.is_known:
            message = self._redactor.scrub(f"{type(exc).__name__}: {exc}")
        else:
            message = f"{type(exc).__name__}: <details withheld; no secret redactor available>"
        current_stage = stage or self._record.stage
        try:
            if not self._sealed:
                self.event(
                    DiagnosticEventCode.FAILURE,
                    current_stage,
                    details={"error_type": type(exc).__name__, "message": message},
                    level="error",
                )
        except DiagnosticWriteError as diag_exc:
            if not hasattr(exc, "diagnostic_write_error"):
                exc.diagnostic_write_error = diag_exc
        try:
            self.set_stage(_STAGE_FAILED)
        except DiagnosticWriteError as diag_exc:
            if not hasattr(exc, "diagnostic_write_error"):
                exc.diagnostic_write_error = diag_exc

    def artifacts_ready(self, *, stage: str = _STAGE_FINALIZING) -> None:
        """Journal that artifacts are on disk -- strictly *before* the authoritative
        terminal commit, never after, so a later console/report problem cannot be
        mistaken for the committed financial result being wrong."""
        self.event(DiagnosticEventCode.ARTIFACTS_READY, stage)
        self.seal()

    def seal(self) -> dict[str, Any]:
        """Pin the actual bytes; no append is allowed after sealing. Terminal
        success remains the responsibility of SQLite or the download manifest.
        """
        if not self._sealed:
            if self._write_error is not None:
                raise self._write_error
            try:
                result = read_diagnostic_journal(
                    self.journal_path, collect_records=False
                )
                if result.incomplete_tail:
                    raise DiagnosticIntegrityError("cannot seal a torn journal")
                self._record.journal_sha256 = result.sha256
                self._record.journal_records = result.record_count
                self._record.ended_at = self._clock()
                self._write_invocation()
            except (OSError, DiagnosticIntegrityError) as exc:
                raise DiagnosticWriteError("could not seal diagnostic journal") from exc
            self._sealed = True
        return {
            "schema_version": 1,
            "invocation_id": self.invocation_id,
            "journal_sha256": self._record.journal_sha256,
        }

    def close(self) -> None:
        """Seal handled preflight failures as well as normal completed attempts."""
        self.seal()


@contextmanager
def invocation_scope(out_dir, *, command: str, invocation=None):
    """One boundary for metadata, preparation, execution and final-save errors.
    Nested application entry points share the writer; fail() records an exception
    once. Console delivery stays outside this scope and cannot rewrite a result.
    """
    owns = invocation is None
    writer = invocation or start_invocation(out_dir, command=command)
    try:
        yield writer
    except BaseException as exc:
        writer.fail(exc)
        protect_evidence_write(writer.close)
        raise
    finally:
        if owns:
            protect_evidence_write(writer.close)


def protect_evidence_write(
    write_call: Callable[[], None],
    *,
    catch: tuple[type[BaseException], ...] = (DiagnosticWriteError,),
) -> None:
    """Run an evidence write (diagnostics `set_stage`/`artifacts_ready`, or an
    application-level manifest/report/trace write) that may itself raise, from
    inside a `finally` block that could already be unwinding an in-flight
    application exception.

    `catch` defaults to `DiagnosticWriteError` (the journal's own writes); pass
    `catch=(OSError,)` (or `(DiagnosticWriteError, OSError)`) for plain-file
    manifest/report/trace writes, which raise ordinary `OSError`, not
    `DiagnosticWriteError`, on permission/disk-full failures.

    A secondary evidence-write failure here must never silently replace that
    in-flight exception (BR-01 T04: "preserve the primary failure and identify
    the secondary write problem when possible") -- it is attached to the
    in-flight exception as `.diagnostic_write_error` instead, never raised in
    its place. When nothing is in flight (the success path), this failure is
    the only failure and is raised normally, so it is never silently dropped."""
    active = sys.exc_info()[1]
    try:
        write_call()
    except catch as diag_exc:
        if active is None:
            raise
        if not hasattr(active, "diagnostic_write_error"):
            active.diagnostic_write_error = diag_exc


def start_invocation(
    out_dir: str | Path,
    *,
    command: str,
    redactor: SecretRedactor | None = None,
    clock: Callable[[], str] | None = None,
    open_append: Callable[[Path], Any] | None = None,
    invocation_id: str | None = None,
    observer: Callable[[DiagnosticRecord], None] | None = None,
) -> DiagnosticWriter:
    """Allocate an invocation ID and persist its evidence directory before any
    cache/network/expansion work. Concurrent commands always get distinct
    directories (`mkdir(..., exist_ok=False)` is the same collision guard already
    used for attempt directories)."""
    invocation_id = _validate_run_id(invocation_id or new_run_id())
    invocation_dir = Path(out_dir) / "invocations" / invocation_id
    try:
        invocation_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise DiagnosticWriteError(
            f"could not create invocation directory: {invocation_dir}"
        ) from exc
    return DiagnosticWriter(
        invocation_dir,
        invocation_id=invocation_id,
        command=command,
        redactor=redactor,
        clock=clock,
        open_append=open_append,
        observer=observer,
    )

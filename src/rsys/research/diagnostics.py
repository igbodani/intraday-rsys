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

import json
import os
import socket
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rsys.research.errors import DiagnosticWriteError
from rsys.research.experiments import SecretRedactor, redact_secrets
from rsys.research.models import DiagnosticEventCode, InvocationRecord
from rsys.research.storage import new_run_id

_STAGE_PREPARING = "preparing"
_STAGE_RESOLVED = "resolved"
_STAGE_FINALIZING = "finalizing"
_STAGE_FAILED = "failed"

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


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
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
        temp.write_bytes(
            json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8")
        )
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
    ):
        self.invocation_id = invocation_id
        self.command = command
        self._dir = invocation_dir
        self.journal_path = invocation_dir / "diagnostics.jsonl"
        self.invocation_path = invocation_dir / "invocation.json"
        self._redactor = redactor or SecretRedactor.unknown()
        self._clock = clock or _default_clock
        self._open_append = open_append or _default_open_append
        self._sequence = 0
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

    @property
    def linked_run_id(self) -> str | None:
        return self._record.linked_run_id

    @property
    def stage(self) -> str:
        return self._record.stage

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
            "details": redact_secrets(details or {}),
        }
        if simulation_time is not None:
            payload["simulation_time"] = simulation_time
        line = (
            json.dumps(payload, sort_keys=True, default=str) + "\n"
        ).encode("utf-8")
        should_sync = level == "error" or code in _SYNC_ON_CODES
        try:
            handle = self._open_append(self.journal_path)
            try:
                handle.write(line)
                handle.flush()
                if should_sync:
                    os.fsync(handle.fileno())
            finally:
                handle.close()
        except OSError as exc:
            self._handle_write_failure(exc)

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
        raise DiagnosticWriteError(
            f"could not append to diagnostics journal: {self.journal_path}"
        ) from exc

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
        if self._redactor.is_known:
            message = self._redactor.scrub(f"{type(exc).__name__}: {exc}")
        else:
            message = f"{type(exc).__name__}: <details withheld; no secret redactor available>"
        current_stage = stage or self._record.stage
        try:
            self.event(
                DiagnosticEventCode.FAILURE,
                current_stage,
                details={"message": message},
                level="error",
            )
        except DiagnosticWriteError as diag_exc:
            if not hasattr(exc, "diagnostic_write_error"):
                exc.diagnostic_write_error = diag_exc
            return
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

    def close(self) -> None:
        """No-op seam reserved for an explicit final sync/seal step; the journal is
        append-only and already flushed/synced at each declared boundary."""


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
) -> DiagnosticWriter:
    """Allocate an invocation ID and persist its evidence directory before any
    cache/network/expansion work. Concurrent commands always get distinct
    directories (`mkdir(..., exist_ok=False)` is the same collision guard already
    used for attempt directories)."""
    invocation_id = invocation_id or new_run_id()
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
    )

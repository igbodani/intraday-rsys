"""Transactional run evidence storage (H2).

Each storage root owns one SQLite database (`root_dir/runs.db`) with two
tables: `runs` (current state, one row per run) and `transitions` (append-only
status history with a payload-integrity hash per entry). A single SQLite
transaction commits a run's new state and its transition entry together --
record replacement across two separate files is not atomic; one transactional
commit against one file is. JSON remains available as a derived export via
`load_record`/`read_transitions`, never as the source of truth.

Run directories, once created, are never deleted or reused by this module
(failed/interrupted/crashed runs are retained; duplicate run IDs are
rejected; frozen input fields cannot change after creation; status can only
move along the declared lifecycle; a run's owning process cannot be
reassigned once set).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if sys.platform.startswith("win"):
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )

from rsys.research.errors import (
    FrozenFieldError,
    IllegalTransitionError,
    InvalidRunIdError,
    RecoveryInProgressError,
    RunAlreadyExistsError,
    RunNotFoundError,
    TerminalOutcomeConflictError,
)
from rsys.research.models import RunRecord

# Legal forward transitions. A status absent here (or mapped to an empty set)
# is terminal: no further status change is accepted for that run.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running"}),
    "running": frozenset({"success", "failed", "interrupted", "crashed"}),
}

# Frozen once a run is created: a later write that changes any of these is
# rejected, not silently accepted. Status/results/owner fields are
# deliberately excluded (owner is set once at the pending->running
# transition and frozen separately; see `_check_owner_not_reassigned`).
_IMMUTABLE_FIELDS = (
    "run_id",
    "hypothesis",
    "phase",
    "parent_run_id",
    "resolved_config",
    "config_hash",
    "data_hash",
    "code_hash",
    "code_manifest",
    "code_revision",
    "dependency_lock_hash",
    "seed",
    "account_cost_profile",
    "started_at",
)

# No outgoing transitions declared for these: once reached, a run is sealed (see
# `_check_terminal_sealed` -- an identical repeat write is an idempotent no-op; a
# write that changes any result field is rejected, not silently accepted, C02/C03).
_TERMINAL_STATUSES = frozenset({"success", "failed", "interrupted", "crashed"})

# Result/output fields a terminal run may still legitimately have had set by the same
# write that finalized it; compared verbatim to detect a conflicting second finalizer.
_RESULT_FIELDS = (
    "ended_at",
    "error",
    "artifacts",
    "exclusions",
    "metrics",
    "decision_gate_status",
)

_JSON_COLUMNS = (
    "resolved_config",
    "code_manifest",
    "artifacts",
    "exclusions",
    "metrics",
    "decision_gate_status",
)


def new_run_id() -> str:
    """Sortable-by-creation-order unique ID: UTC timestamp prefix + random suffix."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    suffix = uuid.uuid4().hex[:8]
    return f"{stamp}-{suffix}"


def canonical_hash(value: Any) -> str:
    """SHA-256 over canonical (sorted-key, compact) UTF-8 JSON."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_run_id(run_id: str) -> str:
    """A single safe path component: no separators, no '..', no empty/null-byte values.

    Restricting to one component (rather than allowing nested IDs) keeps directory
    listing/recovery exhaustive by construction -- there is no second traversal mode
    to keep in sync with the single-component one.
    """
    if not isinstance(run_id, str) or run_id in ("", ".", "..") or "\x00" in run_id:
        raise InvalidRunIdError(f"invalid run_id: {run_id!r}")
    if "/" in run_id or "\\" in run_id or run_id != Path(run_id).name:
        raise InvalidRunIdError(f"run_id must be a single path component: {run_id!r}")
    return run_id


def _pid_is_alive(pid: int) -> bool:
    """Best-effort liveness check so recovery never marks a live run crashed."""
    if pid is None or pid <= 0:
        return False
    if sys.platform.startswith("win"):
        process_query_limited_information = 0x1000
        still_active = 259
        handle = _kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            _kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    return True


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    hypothesis TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    parent_run_id TEXT,
    resolved_config TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    data_hash TEXT,
    code_hash TEXT,
    code_manifest TEXT NOT NULL,
    code_revision TEXT,
    dependency_lock_hash TEXT,
    seed INTEGER,
    account_cost_profile TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    artifacts TEXT NOT NULL,
    exclusions TEXT NOT NULL,
    metrics TEXT NOT NULL,
    decision_gate_status TEXT NOT NULL,
    error TEXT,
    owner_pid INTEGER,
    owner_host TEXT
);
CREATE TABLE IF NOT EXISTS transitions (
    commit_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    status TEXT NOT NULL,
    at TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
"""

_RUN_COLUMNS = (
    "run_id",
    "hypothesis",
    "phase",
    "status",
    "parent_run_id",
    "resolved_config",
    "config_hash",
    "data_hash",
    "code_hash",
    "code_manifest",
    "code_revision",
    "dependency_lock_hash",
    "seed",
    "account_cost_profile",
    "started_at",
    "ended_at",
    "artifacts",
    "exclusions",
    "metrics",
    "decision_gate_status",
    "error",
    "owner_pid",
    "owner_host",
)


class RunStorage:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root_dir / "runs.db"
        self._conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def _row_values(self, record: RunRecord) -> tuple:
        dumped = json.loads(record.model_dump_json())
        values = []
        for col in _RUN_COLUMNS:
            v = dumped[col]
            values.append(
                json.dumps(v, sort_keys=True, separators=(",", ":"))
                if col in _JSON_COLUMNS
                else v
            )
        return tuple(values)

    def _row_to_record(self, row: sqlite3.Row) -> RunRecord:
        data = dict(row)
        for col in _JSON_COLUMNS:
            data[col] = json.loads(data[col])
        return RunRecord.model_validate(data)

    def create_run(self, record: RunRecord) -> None:
        _validate_run_id(record.run_id)
        existing = self._conn.execute(
            "SELECT 1 FROM runs WHERE run_id = ?", (record.run_id,)
        ).fetchone()
        if existing is not None:
            raise RunAlreadyExistsError(f"run_id already exists: {record.run_id}")
        if record.config_hash != canonical_hash(record.resolved_config):
            raise ValueError(
                "config_hash does not match the canonical hash of resolved_config"
            )
        values = self._row_values(record)
        payload_hash = canonical_hash(dict(zip(_RUN_COLUMNS, values, strict=True)))
        with self._transaction():
            try:
                self._conn.execute(
                    f"INSERT INTO runs ({', '.join(_RUN_COLUMNS)}) VALUES ({', '.join('?' * len(_RUN_COLUMNS))})",
                    values,
                )
            except sqlite3.IntegrityError as exc:
                raise RunAlreadyExistsError(
                    f"run_id already exists: {record.run_id}"
                ) from exc
            self._insert_transition(record.run_id, record.status, payload_hash)

    def write_record(self, record: RunRecord) -> None:
        """C02 fix: the read-current/validate/write sequence all happens *inside* one
        `BEGIN IMMEDIATE` transaction. Previously the read and validation ran before the
        transaction started, so two concurrent writers could both validate against the same
        stale "running" state and both commit -- e.g. `running -> success` then, moments
        later, `running -> failed` from a writer that never saw the first commit. Validating
        after acquiring the write lock means the second writer re-reads the *already-committed*
        first outcome and is correctly rejected.
        """
        _validate_run_id(record.run_id)
        with self._transaction():
            current = self._load_record_for_update(record.run_id)
            self._check_immutable_fields(current, record)
            self._check_owner_not_reassigned(current, record)
            if current.status in _TERMINAL_STATUSES and record.status == current.status:
                if any(
                    getattr(current, f) != getattr(record, f) for f in _RESULT_FIELDS
                ):
                    raise TerminalOutcomeConflictError(
                        f"run {record.run_id!r} is already sealed as {current.status!r}; "
                        "a different result cannot overwrite it"
                    )
                return  # identical repeat of an already-sealed terminal outcome: idempotent no-op
            self._check_legal_transition(current.status, record.status)
            values = self._row_values(record)
            payload_hash = canonical_hash(dict(zip(_RUN_COLUMNS, values, strict=True)))
            assignments = ", ".join(
                f"{col} = ?" for col in _RUN_COLUMNS if col != "run_id"
            )
            update_values = tuple(
                v
                for col, v in zip(_RUN_COLUMNS, values, strict=True)
                if col != "run_id"
            )
            self._conn.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ?",
                (*update_values, record.run_id),
            )
            if record.status != current.status:
                self._insert_transition(record.run_id, record.status, payload_hash)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """`isolation_level=None` puts sqlite3 in autocommit mode, where the module's own
        `with conn:` shortcut is a no-op (each statement commits individually). Manage the
        transaction boundary explicitly instead, so multi-statement writes are truly atomic.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def _insert_transition(self, run_id: str, status: str, payload_hash: str) -> None:
        """Isolated seam so tests can inject a mid-transaction failure (see test_run_record.py)."""
        self._conn.execute(
            "INSERT INTO transitions (run_id, status, at, payload_sha256) VALUES (?, ?, ?, ?)",
            (run_id, status, datetime.now(UTC).isoformat(), payload_hash),
        )

    def load_record(self, run_id: str) -> RunRecord:
        _validate_run_id(run_id)
        return self._load_record_for_update(run_id)

    def _load_record_for_update(self, run_id: str) -> RunRecord:
        """Caller-validated `run_id`; safe to call again inside a transaction already holding
        the write lock (see `write_record`'s C02 fix) without re-validating the identifier."""
        row = self._conn.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunNotFoundError(f"no run record for run_id: {run_id}")
        return self._row_to_record(row)

    def read_transitions(self, run_id: str) -> list[dict[str, Any]]:
        _validate_run_id(run_id)
        rows = self._conn.execute(
            "SELECT status, at, payload_sha256 FROM transitions WHERE run_id = ? ORDER BY commit_seq",
            (run_id,),
        ).fetchall()
        return [{"status": s, "at": a, "payload_sha256": h} for s, a, h in rows]

    def list_run_ids(self) -> list[str]:
        rows = self._conn.execute("SELECT run_id FROM runs ORDER BY run_id").fetchall()
        return [r[0] for r in rows]

    def recover_crashed_runs(
        self, reason: str = "process ended without a clean status transition"
    ) -> list[str]:
        """Mark every 'running' run whose owning process is actually dead as 'crashed'.
        A run with a live owner is left untouched -- recovery must never mark a live run crashed.

        Ownership/liveness checks are local-host only (`os.kill`/`OpenProcess` by PID): this
        cannot and does not infer whether a *remote* host's owning process has died. The lock
        file below carries its holder's PID so a stale lock left by a sweep that crashed before
        releasing it can be detected and reclaimed, rather than blocking all future recovery
        forever -- but that staleness check is equally local-host-only.
        """
        lock_path = self.root_dir / ".recovery.lock"
        if lock_path.exists():
            holder_pid = None
            try:
                holder_pid = int(lock_path.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pass
            if holder_pid is None or _pid_is_alive(holder_pid):
                # Unreadable/unknown holder is treated conservatively as still held -- we have
                # no positive evidence it is safe to reclaim.
                raise RecoveryInProgressError(
                    f"a recovery sweep is already in progress: {lock_path}"
                )
            lock_path.unlink(
                missing_ok=True
            )  # stale lock: holder PID is confirmed dead; reclaim it
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RecoveryInProgressError(
                f"a recovery sweep is already in progress: {lock_path}"
            ) from exc
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        try:
            recovered: list[str] = []
            for run_id in self.list_run_ids():
                record = self.load_record(run_id)
                if record.status != "running":
                    continue
                if record.owner_pid is not None and _pid_is_alive(record.owner_pid):
                    continue
                record.status = "crashed"
                record.ended_at = datetime.now(UTC).isoformat()
                record.error = f"recovered: {reason}"
                self.write_record(record)
                recovered.append(run_id)
            return recovered
        finally:
            lock_path.unlink(missing_ok=True)

    @staticmethod
    def _check_immutable_fields(current: RunRecord, candidate: RunRecord) -> None:
        for field in _IMMUTABLE_FIELDS:
            if getattr(current, field) != getattr(candidate, field):
                raise FrozenFieldError(
                    f"field {field!r} is frozen after run creation and cannot change"
                )

    @staticmethod
    def _check_owner_not_reassigned(current: RunRecord, candidate: RunRecord) -> None:
        if current.owner_pid is not None and candidate.owner_pid != current.owner_pid:
            raise FrozenFieldError("owner_pid is frozen once a run has started running")
        if (
            current.owner_host is not None
            and candidate.owner_host != current.owner_host
        ):
            raise FrozenFieldError(
                "owner_host is frozen once a run has started running"
            )

    @staticmethod
    def _check_legal_transition(current_status: str, new_status: str) -> None:
        if new_status == current_status:
            return
        allowed = _ALLOWED_TRANSITIONS.get(current_status, frozenset())
        if new_status not in allowed:
            raise IllegalTransitionError(
                f"illegal status transition: {current_status!r} -> {new_status!r}"
            )

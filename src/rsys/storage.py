"""Atomic, append-only run evidence storage.

Each run owns one directory `root_dir/<run_id>/` containing `record.json`
(current state) and `transitions.jsonl` (append-only status history). Writes
use write-to-temp-then-rename so a crash mid-write never leaves a corrupt
`record.json`; a run directory, once created, is never deleted or reused by
this module (failed/interrupted/crashed runs are retained; duplicate run IDs
are rejected; frozen input fields cannot change after creation; status can
only move along the declared lifecycle).

Write ordering is deliberate: the append-only transition log is written
*before* the atomic `record.json` commit. If the atomic write then fails,
`record.json` (the single source of truth read by `load_record`) still
reflects the last durably-committed state -- it never claims a status that
was not actually persisted. The log may contain one extra, uncommitted
entry in that case; that is a detectable, recoverable inconsistency, not a
silently wrong authoritative record.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

RunStatus = Literal["pending", "running", "success", "failed", "interrupted", "crashed"]

# Legal forward transitions. A status absent here (or mapped to an empty set)
# is terminal: no further status change is accepted for that run.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running"}),
    "running": frozenset({"success", "failed", "interrupted", "crashed"}),
}

# Frozen once a run is created: a later write that changes any of these is
# rejected, not silently accepted. Status/results fields are deliberately
# excluded (they are expected to change as the run progresses).
_IMMUTABLE_FIELDS = (
    "run_id", "hypothesis", "phase", "parent_run_id", "resolved_config", "config_hash",
    "data_hash", "code_hash", "dependency_lock_hash", "seed", "account_cost_profile", "started_at",
)


class RunRecord(BaseModel):
    """Minimum run record per docs/contracts.md's CLI target section."""

    run_id: str
    hypothesis: str
    phase: str
    status: RunStatus
    parent_run_id: str | None = None
    resolved_config: dict[str, Any]
    config_hash: str
    data_hash: str | None = None
    code_hash: str | None = None
    dependency_lock_hash: str | None = None
    seed: int | None = None
    account_cost_profile: str | None = None
    started_at: str
    ended_at: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    decision_gate_status: dict[str, str] = Field(default_factory=dict)
    error: str | None = None

    # In-memory only (never serialized): raw secret-field values found in the
    # original resolved_config, used to scrub them out of later error text.
    _secret_values: list[str] = PrivateAttr(default_factory=list)


class RunAlreadyExistsError(Exception):
    """A run directory for this run_id already exists; it is never overwritten."""


class RunNotFoundError(Exception):
    pass


class InvalidRunIdError(ValueError):
    """run_id would escape the storage root (absolute path or '..' traversal)."""


class IllegalTransitionError(Exception):
    """A status change outside the declared lifecycle (e.g. resurrecting a finalized attempt)."""


class FrozenFieldError(Exception):
    """A write attempted to change a field that is frozen once a run is created."""


def new_run_id() -> str:
    """Sortable-by-creation-order unique ID: UTC timestamp prefix + random suffix."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    suffix = uuid.uuid4().hex[:8]
    return f"{stamp}-{suffix}"


def canonical_hash(value: Any) -> str:
    """SHA-256 over canonical (sorted-key, compact) UTF-8 JSON."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


class RunStorage:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)

    def _run_dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or run_id == "" or "\x00" in run_id:
            raise InvalidRunIdError(f"invalid run_id: {run_id!r}")
        candidate_path = Path(run_id)
        if candidate_path.is_absolute() or any(part in ("..", "") for part in candidate_path.parts):
            raise InvalidRunIdError(f"run_id must be a relative path with no '..' segments: {run_id!r}")
        root_resolved = self.root_dir.resolve()
        run_dir = (self.root_dir / run_id).resolve()
        try:
            run_dir.relative_to(root_resolved)
        except ValueError as exc:
            raise InvalidRunIdError(f"run_id escapes storage root: {run_id!r}") from exc
        return run_dir

    def create_run(self, record: RunRecord) -> Path:
        run_dir = self._run_dir(record.run_id)
        if run_dir.exists():
            raise RunAlreadyExistsError(f"run_id already exists: {record.run_id}")
        if record.config_hash != canonical_hash(record.resolved_config):
            raise ValueError("config_hash does not match the canonical hash of resolved_config")
        run_dir.mkdir(parents=True, exist_ok=False)
        self._append_transition(run_dir, record.status)
        self._write_record(run_dir, record)
        return run_dir

    def write_record(self, record: RunRecord) -> None:
        run_dir = self._run_dir(record.run_id)
        if not run_dir.exists():
            raise RunNotFoundError(f"no run directory for run_id: {record.run_id}")
        current = self.load_record(record.run_id)
        self._check_immutable_fields(current, record)
        self._check_legal_transition(current.status, record.status)
        if record.status != current.status:
            self._append_transition(run_dir, record.status)
        self._write_record(run_dir, record)

    def load_record(self, run_id: str) -> RunRecord:
        run_dir = self._run_dir(run_id)
        record_path = run_dir / "record.json"
        if not record_path.exists():
            raise RunNotFoundError(f"no record.json for run_id: {run_id}")
        return RunRecord.model_validate_json(record_path.read_text(encoding="utf-8"))

    def read_transitions(self, run_id: str) -> list[dict[str, Any]]:
        run_dir = self._run_dir(run_id)
        path = run_dir / "transitions.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def list_run_ids(self) -> list[str]:
        if not self.root_dir.exists():
            return []
        return sorted(p.name for p in self.root_dir.iterdir() if (p / "record.json").exists())

    def recover_crashed_runs(self, reason: str = "process ended without a clean status transition") -> list[str]:
        """Mark every run still 'running' as 'crashed'. Never pretends a killed process cleaned up."""
        recovered: list[str] = []
        for run_id in self.list_run_ids():
            record = self.load_record(run_id)
            if record.status == "running":
                record.status = "crashed"
                record.ended_at = datetime.now(UTC).isoformat()
                record.error = f"recovered: {reason}"
                self.write_record(record)
                recovered.append(run_id)
        return recovered

    @staticmethod
    def _check_immutable_fields(current: RunRecord, candidate: RunRecord) -> None:
        for field in _IMMUTABLE_FIELDS:
            if getattr(current, field) != getattr(candidate, field):
                raise FrozenFieldError(f"field {field!r} is frozen after run creation and cannot change")

    @staticmethod
    def _check_legal_transition(current_status: str, new_status: str) -> None:
        if new_status == current_status:
            return
        allowed = _ALLOWED_TRANSITIONS.get(current_status, frozenset())
        if new_status not in allowed:
            raise IllegalTransitionError(f"illegal status transition: {current_status!r} -> {new_status!r}")

    def _write_record(self, run_dir: Path, record: RunRecord) -> None:
        _atomic_write_json(run_dir / "record.json", json.loads(record.model_dump_json()))

    def _append_transition(self, run_dir: Path, status: str) -> None:
        line = json.dumps({"status": status, "at": datetime.now(UTC).isoformat()}, sort_keys=True)
        with open(run_dir / "transitions.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

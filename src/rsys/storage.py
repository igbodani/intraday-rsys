"""Atomic, append-only run evidence storage.

Each run owns one directory `root_dir/<run_id>/` containing `record.json`
(current state) and `transitions.jsonl` (append-only status history). Writes
use write-to-temp-then-rename so a crash mid-write never leaves a corrupt
`record.json`; a run directory, once created, is never deleted or reused by
this module (failed runs are retained; duplicate run IDs are rejected).
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RunRecord(BaseModel):
    """Minimum run record per docs/contracts.md's CLI target section."""

    run_id: str
    hypothesis: str
    phase: str
    status: str  # pending | running | success | failed
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


class RunAlreadyExistsError(Exception):
    """A run directory for this run_id already exists; it is never overwritten."""


class RunNotFoundError(Exception):
    pass


def new_run_id() -> str:
    """Sortable-by-creation-order unique ID: UTC timestamp prefix + random suffix."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    suffix = uuid.uuid4().hex[:8]
    return f"{stamp}-{suffix}"


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
        return self.root_dir / run_id

    def create_run(self, record: RunRecord) -> Path:
        run_dir = self._run_dir(record.run_id)
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RunAlreadyExistsError(f"run_id already exists: {record.run_id}") from exc
        self._write_record(run_dir, record)
        self._append_transition(run_dir, record.status)
        return run_dir

    def write_record(self, record: RunRecord) -> None:
        run_dir = self._run_dir(record.run_id)
        if not run_dir.exists():
            raise RunNotFoundError(f"no run directory for run_id: {record.run_id}")
        self._write_record(run_dir, record)
        self._append_transition(run_dir, record.status)

    def load_record(self, run_id: str) -> RunRecord:
        run_dir = self._run_dir(run_id)
        record_path = run_dir / "record.json"
        if not record_path.exists():
            raise RunNotFoundError(f"no record.json for run_id: {run_id}")
        return RunRecord.model_validate_json(record_path.read_text(encoding="utf-8"))

    def _write_record(self, run_dir: Path, record: RunRecord) -> None:
        _atomic_write_json(run_dir / "record.json", json.loads(record.model_dump_json()))

    def _append_transition(self, run_dir: Path, status: str) -> None:
        line = json.dumps({"status": status, "at": datetime.now(UTC).isoformat()}, sort_keys=True)
        with open(run_dir / "transitions.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

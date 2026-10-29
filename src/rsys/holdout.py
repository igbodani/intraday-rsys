"""Holdout-boundary access guard (docs/M2_IMPLEMENTATION_HANDOFF.md step 9 "Holdout
boundary"). Enforces denial BEFORE any content is read, including a warmup/alias request.

Honesty note: this is an access-control contract over declared synthetic partitions, not a
real final-evaluation runner. No frozen real holdout manifest exists; `final_evaluate` is
permanently blocked pending the owner-approved D16/D17 decisions in docs/decisions.md. A
block must never look like a successful evaluation -- see docs/M2_IMPLEMENTATION_STATUS.md.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FINAL_EVALUATE_EXIT_CODE = 5


class HoldoutAccessDenied(Exception):
    """Raised before any read is attempted against a forbidden or undeclared path."""


@dataclass(frozen=True)
class PartitionManifest:
    """One declared, frozen segment of data. `sealed=True` marks the final holdout."""

    partition_id: str
    paths: tuple[str, ...]
    content_hash: str
    sealed: bool


@dataclass
class AccessLogEntry:
    partition_id: str
    path: str
    purpose: str
    allowed: bool


class HoldoutGuard:
    """Resolves a requested path to its declared partition and denies access to a sealed
    partition before any read, including a request labeled "warmup" or using an alias path
    that resolves to the same declared sealed location."""

    def __init__(self, manifests: list[PartitionManifest]):
        self._by_path: dict[str, PartitionManifest] = {}
        for manifest in manifests:
            for path in manifest.paths:
                normalized = self._normalize(path)
                if normalized in self._by_path:
                    raise ValueError(f"path {path!r} declared in more than one partition")
                self._by_path[normalized] = manifest
        self.access_log: list[AccessLogEntry] = []

    @staticmethod
    def _normalize(path: str) -> str:
        return str(Path(path).resolve())

    def check_access(self, path: str, *, purpose: str = "development") -> PartitionManifest:
        """Raises `HoldoutAccessDenied` for a sealed partition or an undeclared path --
        before any read is attempted, regardless of the stated `purpose`."""
        normalized = self._normalize(path)
        manifest = self._by_path.get(normalized)
        if manifest is None:
            self.access_log.append(AccessLogEntry("UNKNOWN", path, purpose, allowed=False))
            raise HoldoutAccessDenied(f"path {path!r} is not a declared partition; access denied")
        if manifest.sealed:
            self.access_log.append(AccessLogEntry(manifest.partition_id, path, purpose, allowed=False))
            raise HoldoutAccessDenied(f"partition {manifest.partition_id!r} is sealed final-holdout data")
        self.access_log.append(AccessLogEntry(manifest.partition_id, path, purpose, allowed=True))
        return manifest

    def guarded_read(self, path: str, reader: Callable[[str], Any], *, purpose: str = "development") -> Any:
        """Checks access, THEN calls `reader(path)`; `reader` is never invoked for a denied
        path, so a monkeypatched reader that fails the test if called proves denial."""
        self.check_access(path, purpose=purpose)
        return reader(path)


def compute_file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_partition_integrity(manifest: PartitionManifest, base_dir: Path) -> list[str]:
    """Returns a list of mismatch descriptions (empty if every declared path's actual
    content hash matches); a tampered or missing frozen file is never silently accepted."""
    problems = []
    for relative_path in manifest.paths:
        full_path = base_dir / relative_path
        if not full_path.exists():
            problems.append(f"{relative_path}: missing")
            continue
        actual = compute_file_hash(full_path)
        if actual != manifest.content_hash:
            problems.append(f"{relative_path}: content hash mismatch (expected {manifest.content_hash}, got {actual})")
    return problems


@dataclass(frozen=True)
class FinalEvaluateBlockedResult:
    blocked: bool
    missing_requirements: tuple[str, ...]


def final_evaluate() -> FinalEvaluateBlockedResult:
    """Always blocked in this codebase: D16 (splits/trial budget) and D17 (promotion gates)
    remain Open in docs/decisions.md, and no owner-approved frozen holdout manifest exists.
    Returns a truthful blocked result; never produces output that looks like a completed
    evaluation. Callers should exit `FINAL_EVALUATE_EXIT_CODE`."""
    return FinalEvaluateBlockedResult(
        blocked=True,
        missing_requirements=(
            "D16: chronological dates/trial budget/seeds (docs/decisions.md)",
            "D17: promotion gates (docs/decisions.md)",
            "owner-approved frozen final-holdout manifest",
        ),
    )

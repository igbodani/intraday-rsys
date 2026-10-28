"""Run lifecycle: starts a run record, redacts secrets, hashes resolved
config, captures code/dependency provenance, and guarantees a failed or
interrupted run is retained (not silently dropped) with truthful status."""
from __future__ import annotations

import re
import subprocess
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rsys.storage import RunRecord, RunStorage, canonical_hash, new_run_id

_REDACTED = "REDACTED"

# Matched as whole underscore/case-split tokens, never substrings, so a
# legitimate research setting like "ranking_sort_key" is never confused with
# an actual credential field like "api_key" (the false-positive this fixes).
_SECRET_SUFFIXES = frozenset({"key", "token", "secret", "password", "passwd", "credential", "credentials"})
_SECRET_PREFIXES = frozenset({"api", "auth", "access", "client", "private", "refresh", "secret"})


def _tokenize(field_name: str) -> list[str]:
    return [t for t in re.split(r"[^a-zA-Z0-9]+", field_name.lower()) if t]


def _is_secret_field(field_name: str) -> bool:
    tokens = _tokenize(field_name)
    if not tokens or tokens[-1] not in _SECRET_SUFFIXES:
        return False
    if len(tokens) == 1:
        return True  # the field is literally named e.g. "token"/"secret"/"password"
    return any(t in _SECRET_PREFIXES for t in tokens[:-1])


def redact_secrets(value: Any) -> Any:
    """Recursively replace any dict value whose key looks secret-like. Never reads os.environ."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if isinstance(k, str) and _is_secret_field(k) else redact_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    return value


def extract_secret_values(value: Any) -> list[str]:
    """Raw secret-field string values, kept only in-memory to scrub later error text."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and _is_secret_field(k):
                found.append(v if isinstance(v, str) else str(v))
            else:
                found.extend(extract_secret_values(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(extract_secret_values(v))
    return found


def _scrub(text: str, secret_values: list[str]) -> str:
    for secret in secret_values:
        if secret:
            text = text.replace(secret, _REDACTED)
    return text


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return here.parent


def compute_code_hash(repo_root: Path | None = None) -> str:
    """Git commit plus a dirty-tree flag; an explicit 'unavailable' sentinel, never a silent guess."""
    repo_root = repo_root or _find_repo_root()
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, check=True, timeout=5,
        ).stdout
        return f"{sha}+dirty" if status.strip() else sha
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"unavailable:{type(exc).__name__}"


def compute_dependency_lock_hash(repo_root: Path | None = None) -> str:
    """Hash of the pinned dependency lockfile; an explicit sentinel if none exists yet."""
    repo_root = repo_root or _find_repo_root()
    lock_path = repo_root / "uv.lock"
    if not lock_path.exists():
        return "unavailable:no_lockfile"
    return canonical_hash(lock_path.read_text(encoding="utf-8"))


def start_run(
    storage: RunStorage,
    *,
    hypothesis: str,
    phase: str,
    resolved_config: dict[str, Any],
    parent_run_id: str | None = None,
    seed: int | None = None,
    account_cost_profile: str | None = None,
    data_identity: str | None = None,
    data_not_applicable_reason: str | None = None,
    repo_root: str | Path | None = None,
) -> RunRecord:
    """Create a new run record. Input-data provenance is mandatory: either a
    concrete `data_identity`, or an explicit `data_not_applicable_reason` --
    missing provenance must never masquerade as a complete research record."""
    if data_identity is None and data_not_applicable_reason is None:
        raise ValueError(
            "start_run requires either data_identity or an explicit data_not_applicable_reason"
        )
    root = Path(repo_root) if repo_root is not None else None
    redacted_config = redact_secrets(resolved_config)
    record = RunRecord(
        run_id=new_run_id(),
        hypothesis=hypothesis,
        phase=phase,
        status="pending",
        parent_run_id=parent_run_id,
        resolved_config=redacted_config,
        config_hash=canonical_hash(redacted_config),
        data_hash=data_identity if data_identity is not None else f"not_applicable:{data_not_applicable_reason}",
        code_hash=compute_code_hash(root),
        dependency_lock_hash=compute_dependency_lock_hash(root),
        seed=seed,
        account_cost_profile=account_cost_profile,
        started_at=datetime.now(UTC).isoformat(),
    )
    record._secret_values = extract_secret_values(resolved_config)
    storage.create_run(record)
    return record


@contextmanager
def run_context(storage: RunStorage, record: RunRecord) -> Iterator[RunRecord]:
    """Transition pending -> running -> success/failed; failures are retained with scrubbed errors."""
    record.status = "running"
    storage.write_record(record)
    try:
        yield record
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        record.status = "failed"
        record.error = _scrub(error_text, record._secret_values)
        record.ended_at = datetime.now(UTC).isoformat()
        storage.write_record(record)
        raise
    else:
        record.status = "success"
        record.ended_at = datetime.now(UTC).isoformat()
        storage.write_record(record)


def mark_interrupted(storage: RunStorage, record: RunRecord, reason: str) -> RunRecord:
    """Record a handled interruption (e.g. SIGTERM) with a truthful end time, not a fake success."""
    record.status = "interrupted"
    record.error = _scrub(f"handled interruption: {reason}", record._secret_values)
    record.ended_at = datetime.now(UTC).isoformat()
    storage.write_record(record)
    return record


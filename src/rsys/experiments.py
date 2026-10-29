"""Run lifecycle: starts a run record, redacts secrets, hashes resolved
config, captures code/dependency provenance, and guarantees a failed or
interrupted run is retained (not silently dropped) with truthful status."""
from __future__ import annotations

import hashlib
import os
import re
import socket
import subprocess
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rsys.storage import (
    FrozenFieldError,
    IllegalTransitionError,
    RunRecord,
    RunStorage,
    canonical_hash,
    new_run_id,
)

_REDACTED = "REDACTED"

# Matched as whole tokens split on separators *and* camelCase boundaries, never
# substrings, so a legitimate research setting like "ranking_sort_key" is never
# confused with a credential field like "apiKey"/"api_key"/"Authorization".
_SECRET_SUFFIXES = frozenset(
    {"key", "token", "secret", "password", "passwd", "credential", "credentials", "authorization", "bearer"}
)
_SECRET_PREFIXES = frozenset({"api", "auth", "access", "client", "private", "refresh", "secret", "x"})


def _tokenize(field_name: str) -> list[str]:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", field_name)
    return [t for t in re.split(r"[^a-zA-Z0-9]+", camel_split.lower()) if t]


def _is_secret_field(field_name: str) -> bool:
    tokens = _tokenize(field_name)
    if not tokens or tokens[-1] not in _SECRET_SUFFIXES:
        return False
    if len(tokens) == 1:
        return True  # the field is literally named e.g. "token"/"secret"/"password"/"Authorization"
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
    """Raw secret-field string values, kept only in-memory to scrub later error text.

    Never persisted: once a record is reloaded from disk, its resolved_config
    already holds "REDACTED" in place of these values, so there is nothing left
    to leak even though this in-memory list itself comes back empty.
    """
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


# Directories/files never contributing to the content fingerprint: caches, the
# virtual environment, build output, data/run artifacts, and anything credential-shaped.
_EXCLUDED_DIR_NAMES = frozenset(
    {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "data", "runs", ".vscode"}
)
_SOURCE_GLOBS = ("src/**/*.py", "pyproject.toml", "uv.lock", "fixtures/**/*.json", "configs/**/*.yaml")


def _is_excluded(relative_path: Path) -> bool:
    if any(part in _EXCLUDED_DIR_NAMES for part in relative_path.parts):
        return True
    return relative_path.name == ".env" or relative_path.suffix == ".key"


def compute_source_fingerprint(repo_root: Path | None = None) -> tuple[str, list[str]]:
    """Canonical hash over (relative path, content sha256) for every matched source file,
    including uncommitted/untracked changes -- two differently-dirty trees always differ.
    Returns (fingerprint, sorted relative paths included) so the manifest is inspectable.
    """
    repo_root = repo_root or _find_repo_root()
    entries: list[dict[str, str]] = []
    for pattern in _SOURCE_GLOBS:
        for path in repo_root.glob(pattern):
            if not path.is_file():
                continue
            relative = path.relative_to(repo_root)
            if _is_excluded(relative):
                continue
            entries.append({"path": relative.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    entries.sort(key=lambda e: e["path"])
    return canonical_hash(entries), [e["path"] for e in entries]


def compute_code_revision(repo_root: Path | None = None) -> str:
    """Git commit plus a dirty-tree flag: informational metadata, not the content fingerprint."""
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
    nonblank `data_identity`, or a nonblank explicit `data_not_applicable_reason`,
    never both -- a nonempty sentinel alone must not stand in for real provenance."""
    identity_given = data_identity is not None and data_identity.strip() != ""
    reason_given = data_not_applicable_reason is not None and data_not_applicable_reason.strip() != ""
    if identity_given and reason_given:
        raise ValueError("start_run: data_identity and data_not_applicable_reason are contradictory; provide one")
    if not identity_given and not reason_given:
        raise ValueError("start_run requires a nonblank data_identity or a nonblank data_not_applicable_reason")

    root = Path(repo_root) if repo_root is not None else None
    redacted_config = redact_secrets(resolved_config)
    code_hash, code_manifest = compute_source_fingerprint(root)
    record = RunRecord(
        run_id=new_run_id(),
        hypothesis=hypothesis,
        phase=phase,
        status="pending",
        parent_run_id=parent_run_id,
        resolved_config=redacted_config,
        config_hash=canonical_hash(redacted_config),
        data_hash=data_identity if identity_given else f"not_applicable:{data_not_applicable_reason}",
        code_hash=code_hash,
        code_manifest=code_manifest,
        code_revision=compute_code_revision(root),
        dependency_lock_hash=compute_dependency_lock_hash(root),
        seed=seed,
        account_cost_profile=account_cost_profile,
        started_at=datetime.now(UTC).isoformat(),
    )
    record._secret_values = extract_secret_values(resolved_config)
    storage.create_run(record)
    return record


def _finalize(storage: RunStorage, record: RunRecord, *, status: str, error_text: str | None) -> None:
    record.status = status
    record.error = _scrub(error_text, record._secret_values) if error_text is not None else None
    record.ended_at = datetime.now(UTC).isoformat()
    try:
        storage.write_record(record)
    except (FrozenFieldError, IllegalTransitionError):
        # Already finalized (e.g. a concurrent recovery sweep got there first): the existing
        # outcome stands. Never overwrite an already-finalized record during context-manager exit.
        pass


@contextmanager
def run_context(storage: RunStorage, record: RunRecord) -> Iterator[RunRecord]:
    """Transition pending -> running -> success/failed/interrupted; failures and handled
    interruptions are retained with scrubbed errors. A force-killed process is never
    claimed to have cleaned up; that is `RunStorage.recover_crashed_runs`'s job instead."""
    record.status = "running"
    record.owner_pid = os.getpid()
    record.owner_host = socket.gethostname()
    storage.write_record(record)
    try:
        yield record
    except KeyboardInterrupt:
        _finalize(storage, record, status="interrupted", error_text="handled interruption: KeyboardInterrupt")
        raise
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        _finalize(storage, record, status="failed", error_text=error_text)
        raise
    else:
        _finalize(storage, record, status="success", error_text=None)


def mark_interrupted(storage: RunStorage, record: RunRecord, reason: str) -> RunRecord:
    """Record a handled interruption (e.g. a caught signal) with a truthful end time."""
    _finalize(storage, record, status="interrupted", error_text=f"handled interruption: {reason}")
    return record



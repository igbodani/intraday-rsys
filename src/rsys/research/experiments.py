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

from rsys.research.errors import (
    FinalizationError,
    FrozenFieldError,
    IllegalTransitionError,
    TerminalOutcomeConflictError,
)
from rsys.research.models import RunRecord
from rsys.research.storage import RunStorage, canonical_hash, new_run_id

_REDACTED = "REDACTED"

# Matched as whole tokens split on separators *and* camelCase boundaries, never
# substrings, so a legitimate research setting like "ranking_sort_key" is never
# confused with a credential field like "apiKey"/"api_key"/"Authorization". A
# secret-suffix token may appear anywhere in the name (not only last) so provider
# env-var forms like "APCA_API_KEY_ID" (ends in "_ID", not "_KEY") are still caught.
_SECRET_SUFFIXES = frozenset(
    {
        "key",
        "token",
        "secret",
        "password",
        "passwd",
        "credential",
        "credentials",
        "authorization",
        "bearer",
    }
)
_SECRET_PREFIXES = frozenset(
    {"api", "auth", "access", "client", "private", "refresh", "secret", "x", "apca"}
)


def _tokenize(field_name: str) -> list[str]:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", field_name)
    return [t for t in re.split(r"[^a-zA-Z0-9]+", camel_split.lower()) if t]


def _is_secret_field(field_name: str) -> bool:
    tokens = _tokenize(field_name)
    if not any(t in _SECRET_SUFFIXES for t in tokens):
        return False
    if len(tokens) == 1:
        return True  # the field is literally named e.g. "token"/"secret"/"password"/"Authorization"
    return any(t in _SECRET_PREFIXES for t in tokens)


def redact_secrets(value: Any) -> Any:
    """Recursively replace any dict value whose key looks secret-like. Never reads os.environ."""
    if isinstance(value, dict):
        return {
            k: (
                _REDACTED
                if isinstance(k, str) and _is_secret_field(k)
                else redact_secrets(v)
            )
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


class SecretRedactor:
    """Runtime-only secret-value store, independent of any `RunRecord`'s in-memory
    `_secret_values` (which comes back empty *and unverified* after a reload from disk).
    Inject this explicitly whenever a record may have been reloaded, so scrubbing never
    silently depends on whether the record happens to be the original in-process object (C04).
    `is_known` distinguishes "verified zero secrets" from "secret content is simply unknown".
    """

    def __init__(self, secret_values: list[str] | None = None, *, known: bool = True):
        self._secret_values = [v for v in (secret_values or []) if v]
        self._known = known

    @classmethod
    def from_resolved_config(cls, resolved_config: dict[str, Any]) -> SecretRedactor:
        return cls(extract_secret_values(resolved_config), known=True)

    @classmethod
    def unknown(cls) -> SecretRedactor:
        return cls([], known=False)

    @classmethod
    def from_record(cls, record: RunRecord) -> SecretRedactor:
        """Honest default for the common same-process path; genuinely unknown after reload
        unless an explicit redactor is supplied by the caller."""
        if record._secret_values_known:
            return cls(record._secret_values, known=True)
        return cls.unknown()

    @property
    def is_known(self) -> bool:
        return self._known

    @property
    def has_known_secrets(self) -> bool:
        return bool(self._secret_values)

    def scrub(self, text: str) -> str:
        return _scrub(text, self._secret_values)


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return here.parent


# Directories/files never contributing to the content fingerprint. `_ROOT_EXCLUDED_DIRS` only
# excludes these names when they are the *first* path component (output/cache roots); a nested
# package directory that happens to share a name (e.g. `src/rsys/data/`) is never excluded by
# name alone -- C01's real bug was excluding every directory named "data" anywhere in the tree,
# which silently dropped all of `src/rsys/data/*.py` from the fingerprint.
_ROOT_EXCLUDED_DIRS = frozenset({"data", "runs", "dist"})
_ANYWHERE_EXCLUDED_DIRS = frozenset(
    {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".vscode"}
)
_SOURCE_GLOBS = (
    "src/**/*.py",
    "pyproject.toml",
    "uv.lock",
    "fixtures/**/*.json",
    "configs/**/*.yaml",
)


def _is_excluded(relative_path: Path) -> bool:
    parts = relative_path.parts
    if parts and parts[0] in _ROOT_EXCLUDED_DIRS:
        return True
    if any(part in _ANYWHERE_EXCLUDED_DIRS for part in parts):
        return True
    return relative_path.name == ".env" or relative_path.suffix == ".key"


_FINGERPRINT_ALGORITHM_VERSION = "source-fingerprint-v2"


def compute_source_fingerprint(repo_root: Path | None = None) -> tuple[str, list[str]]:
    """Canonical hash over (relative path, content sha256) for every matched source file,
    including uncommitted/untracked changes -- two differently-dirty trees always differ.
    Returns (fingerprint, sorted relative paths included) so the manifest is inspectable.
    Changing `_FINGERPRINT_ALGORITHM_VERSION` is a declared algorithm change for new runs;
    older persisted fingerprints are not retroactively recomputed and are not comparable to it.
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
            entries.append(
                {
                    "path": relative.as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    entries.sort(key=lambda e: e["path"])
    manifest = {"algorithm_version": _FINGERPRINT_ALGORITHM_VERSION, "entries": entries}
    return canonical_hash(manifest), [e["path"] for e in entries]


def compute_code_revision(repo_root: Path | None = None) -> str:
    """Git commit plus a dirty-tree flag: informational metadata, not the content fingerprint."""
    repo_root = repo_root or _find_repo_root()
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
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
    data_unavailable_reason: str | None = None,
    repo_root: str | Path | None = None,
) -> RunRecord:
    """Create a new run record. Input-data provenance is mandatory: either a
    nonblank `data_identity`, or a nonblank explicit `data_not_applicable_reason`,
    never both -- a nonempty sentinel alone must not stand in for real provenance."""
    identity_given = data_identity is not None and data_identity.strip() != ""
    reason_given = (
        data_not_applicable_reason is not None
        and data_not_applicable_reason.strip() != ""
    )
    unavailable_given = data_unavailable_reason is not None and bool(
        data_unavailable_reason.strip()
    )
    if sum((identity_given, reason_given, unavailable_given)) > 1:
        raise ValueError(
            "start_run: data_identity and data_not_applicable_reason are contradictory; provide one"
        )
    if not identity_given and not reason_given and not unavailable_given:
        raise ValueError(
            "start_run requires a nonblank data_identity or a nonblank data_not_applicable_reason"
        )

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
        data_hash=(
            data_identity
            if identity_given
            else f"unavailable:{data_unavailable_reason}"
            if unavailable_given
            else f"not_applicable:{data_not_applicable_reason}"
        ),
        code_hash=code_hash,
        code_manifest=code_manifest,
        code_revision=compute_code_revision(root),
        dependency_lock_hash=compute_dependency_lock_hash(root),
        seed=seed,
        account_cost_profile=account_cost_profile,
        started_at=datetime.now(UTC).isoformat(),
    )
    record._secret_values = extract_secret_values(resolved_config)
    record._secret_values_known = True
    storage.create_run(record)
    return record


_TERMINAL_STATUSES = frozenset({"success", "failed", "interrupted", "crashed"})


def _finalize(
    storage: RunStorage,
    record: RunRecord,
    *,
    status: str,
    exc: BaseException | None,
    note: str | None,
    redactor: SecretRedactor,
) -> None:
    record.status = status
    record.ended_at = datetime.now(UTC).isoformat()
    if exc is not None:
        if redactor.is_known:
            record.error = redactor.scrub(
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            )
        else:
            # Secret content is genuinely unknown (e.g. a reloaded record with no explicit
            # redactor supplied): never persist arbitrary exception text/traceback verbatim
            # when a reliable sanitizer is unavailable (C04) -- a field-name regex over the
            # config cannot guarantee an arbitrary free-text message is credential-free.
            record.error = f"{type(exc).__name__}: <details withheld; no secret redactor available>"
    elif note is not None:
        record.error = redactor.scrub(note)
    else:
        record.error = None

    try:
        storage.write_record(record)
    except TerminalOutcomeConflictError as conflict_exc:
        raise FinalizationError(
            f"run {record.run_id!r}: a conflicting terminal outcome was already committed"
        ) from (exc if exc is not None else conflict_exc)
    except IllegalTransitionError:
        current = storage.load_record(record.run_id)
        if current.status in _TERMINAL_STATUSES:
            return  # a concurrent finalizer (e.g. recovery) legitimately got there first
        raise FinalizationError(
            f"run {record.run_id!r}: could not record status {status!r}"
        ) from exc
    except FrozenFieldError as frozen_exc:
        raise FinalizationError(
            f"run {record.run_id!r}: immutable input field mismatch while finalizing"
        ) from (exc if exc is not None else frozen_exc)


@contextmanager
def run_context(
    storage: RunStorage,
    record: RunRecord,
    *,
    redactor: SecretRedactor | None = None,
) -> Iterator[RunRecord]:
    """Transition pending -> running -> success/failed/interrupted; failures and handled
    interruptions are retained with scrubbed errors. A force-killed process is never
    claimed to have cleaned up; that is `RunStorage.recover_crashed_runs`'s job instead.
    Pass `redactor` explicitly when `record` may have been reloaded from disk (its own
    `_secret_values` would otherwise be empty); it defaults to the record's in-memory values
    for the common same-process path.
    """
    record.status = "running"
    record.owner_pid = os.getpid()
    record.owner_host = socket.gethostname()
    storage.write_record(record)
    effective_redactor = redactor or SecretRedactor.from_record(record)
    try:
        yield record
    except KeyboardInterrupt:
        _finalize(
            storage,
            record,
            status="interrupted",
            exc=None,
            note="handled interruption: KeyboardInterrupt",
            redactor=effective_redactor,
        )
        raise
    except Exception as exc:
        _finalize(
            storage,
            record,
            status="failed",
            exc=exc,
            note=None,
            redactor=effective_redactor,
        )
        raise
    else:
        _finalize(
            storage,
            record,
            status="success",
            exc=None,
            note=None,
            redactor=effective_redactor,
        )


def mark_interrupted(
    storage: RunStorage,
    record: RunRecord,
    reason: str,
    *,
    redactor: SecretRedactor | None = None,
) -> RunRecord:
    """Record a handled interruption (e.g. a caught signal) with a truthful end time."""
    effective_redactor = redactor or SecretRedactor.from_record(record)
    _finalize(
        storage,
        record,
        status="interrupted",
        exc=None,
        note=f"handled interruption: {reason}",
        redactor=effective_redactor,
    )
    return record

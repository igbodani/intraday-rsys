"""Run lifecycle: starts a run record, redacts secrets, hashes resolved
config, and guarantees a failed run is retained (not silently dropped)."""
from __future__ import annotations

import hashlib
import json
import re
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from rsys.storage import RunRecord, RunStorage, new_run_id

_SECRET_KEY_RE = re.compile(r"(key|token|secret|password|credential)", re.IGNORECASE)
_REDACTED = "REDACTED"


def redact_secrets(value: Any) -> Any:
    """Recursively replace any dict value whose key looks secret-like. Never reads os.environ."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if isinstance(k, str) and _SECRET_KEY_RE.search(k) else redact_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    return value


def canonical_hash(value: Any) -> str:
    """SHA-256 over canonical (sorted-key, compact) UTF-8 JSON."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def start_run(
    storage: RunStorage,
    *,
    hypothesis: str,
    phase: str,
    resolved_config: dict[str, Any],
    parent_run_id: str | None = None,
    seed: int | None = None,
    account_cost_profile: str | None = None,
) -> RunRecord:
    redacted_config = redact_secrets(resolved_config)
    record = RunRecord(
        run_id=new_run_id(),
        hypothesis=hypothesis,
        phase=phase,
        status="pending",
        parent_run_id=parent_run_id,
        resolved_config=redacted_config,
        config_hash=canonical_hash(redacted_config),
        seed=seed,
        account_cost_profile=account_cost_profile,
        started_at=datetime.now(UTC).isoformat(),
    )
    storage.create_run(record)
    return record


@contextmanager
def run_context(storage: RunStorage, record: RunRecord) -> Iterator[RunRecord]:
    """Transition pending -> running -> success/failed; failures are retained, not raised away silently."""
    record.status = "running"
    storage.write_record(record)
    try:
        yield record
    except Exception as exc:
        record.status = "failed"
        record.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        record.ended_at = datetime.now(UTC).isoformat()
        storage.write_record(record)
        raise
    else:
        record.status = "success"
        record.ended_at = datetime.now(UTC).isoformat()
        storage.write_record(record)

"""Read-only inspection and conservative recovery of one logical attempt.

SQLite is authoritative for linked runs. A final download manifest is authoritative
for acquisition. Recovery of an early orphan records a separate receipt: it never
repairs a journal, fabricates a manifest, or makes an attempt comparable.
"""

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from rsys.research.diagnostics import (
    atomic_write_json,
    read_diagnostic_journal,
    verify_diagnostic_seal,
)
from rsys.research.errors import InspectionError, RunNotFoundError
from rsys.research.models import AttemptStatus, InvocationRecord, RecoveryAction
from rsys.research.storage import (
    RunStorage,
    _validate_run_id,
    owner_state,
    recovery_lock,
)
from rsys.research.trace import read_trace, verify_streamed_artifacts

TERMINAL = frozenset({"success", "failed", "interrupted", "crashed"})


def _path(root, *parts):
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise InspectionError("evidence path escapes storage root")
    return path


def _json(path):
    try:
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict):
            raise TypeError("expected an object")
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise InspectionError(f"cannot read valid object: {path}") from exc


def _invocation(root, invocation_id):
    _validate_run_id(invocation_id)
    path = _path(root, "invocations", invocation_id, "invocation.json")
    try:
        state = InvocationRecord.model_validate(_json(path))
    except ValueError as exc:
        raise InspectionError(f"invalid invocation state: {path}") from exc
    if state.schema_version != 1 or state.invocation_id != invocation_id:
        raise InspectionError("invocation identity/version mismatch")
    if state.linked_run_id:
        _validate_run_id(state.linked_run_id)
    return state


def _invocations(root):
    directory = _path(root, "invocations")
    if not directory.exists():
        return []
    return [
        _invocation(root, p.name) for p in sorted(directory.iterdir()) if p.is_dir()
    ]


def _run(root, run_id):
    database = _path(root, "runs.db")
    if not database.exists():
        return None
    storage = RunStorage(root, read_only=True)
    try:
        try:
            return storage.load_record(run_id)
        except RunNotFoundError:
            return None
    finally:
        storage.close()


def _find(root, identity):
    _validate_run_id(identity)
    if _path(root, "invocations", identity).exists():
        state = _invocation(root, identity)
        record = _run(root, state.linked_run_id) if state.linked_run_id else None
        if state.linked_run_id and record is None:
            raise InspectionError("linked run has no authoritative record")
        return state, record
    record = _run(root, identity)
    if record is None:
        raise InspectionError(f"attempt not found: {identity}")
    states = [s for s in _invocations(root) if s.linked_run_id == identity]
    if len(states) > 1:
        raise InspectionError("multiple invocations claim the same run")
    return states[0] if states else None, record


def _read_status(root, identity):
    state, record = _find(root, identity)
    run_id = record.run_id if record else None
    paths, progress = {}, {}
    last, error, torn = None, None, False
    stage = state.stage if state else "unknown"
    if state:
        directory = _path(root, "invocations", state.invocation_id)
        paths.update(
            invocation=str(directory / "invocation.json"),
            journal=str(
                _path(root, "invocations", state.invocation_id, "diagnostics.jsonl")
            ),
        )
        journal = Path(paths["journal"])
        last = state.created_at
        if journal.exists():
            result = read_diagnostic_journal(
                journal,
                expected_sha256=state.journal_sha256,
                expected_invocation_id=state.invocation_id,
                expected_run_id=run_id,
            )
            if state.journal_sha256 and (
                result.record_count != state.journal_records or not state.ended_at
            ):
                raise InspectionError("sealed invocation metadata mismatch")
            torn = result.incomplete_tail
            for row in result.records:
                last = row.wall_time
                updates = {
                    k: v
                    for k, v in row.details.items()
                    if k
                    in {
                        "date",
                        "completed_sessions",
                        "total_sessions",
                        "processed_events",
                        "entry_fills",
                        "exits",
                    }
                }
                if updates:
                    progress.update(updates, observed_at=row.wall_time)
                if row.event == "failure":
                    error = row.details.get("message")
                    progress.update(
                        failure_type=row.details.get("error_type"),
                        failure_stage=row.stage,
                    )
        elif state.journal_sha256:
            raise InspectionError("sealed invocation journal is missing")
        error = state.write_failure or error
        if not error and state.stage == "failed":
            error = (
                "Invocation reported failure; details were not retained in the journal."
            )

    if record:
        owner = owner_state(record.owner_host, record.owner_pid)
        outcome = record.status
        error = record.error or error
        attempt = _path(root, "attempts", run_id)
        paths.update(
            manifest=str(_path(root, "attempts", run_id, "manifest.json")),
            trace=str(_path(root, "attempts", run_id, "trace.jsonl")),
        )
        completeness = "partial"
        manifest_path = Path(paths["manifest"])
        manifest = _json(manifest_path) if manifest_path.exists() else None
        pinned = record.decision_gate_status.get("manifest_sha256")
        if pinned:
            if (
                manifest is None
                or hashlib.sha256(manifest_path.read_bytes()).hexdigest() != pinned
            ):
                raise InspectionError("manifest integrity mismatch")
            if manifest.get("run_id") != run_id:
                raise InspectionError("manifest run identity mismatch")
        if manifest and "diagnostics_version" in manifest and pinned:
            if manifest["diagnostics_version"] != 1:
                raise InspectionError("unsupported diagnostic evidence")
            seal = json.loads(record.decision_gate_status.get("diagnostics", "{}"))
            # A failed evidence write can prevent sealing; don't invent a seal.
            if seal:
                verify_diagnostic_seal(root, seal, run_id=run_id)
            elif outcome == "success":
                raise InspectionError("successful run has no diagnostic seal")
        trace = Path(paths["trace"])
        if trace.exists():
            trace_state = read_trace(trace, collect_records=False)
            torn = torn or trace_state.incomplete_tail
            progress["retained_trace_rows"] = trace_state.record_count
        if outcome == "success":
            if not manifest or not pinned:
                completeness = "unverified"
            elif manifest.get("status") != "success":
                raise InspectionError("success record disagrees with manifest")
            elif "trace_version" in manifest:
                verify_streamed_artifacts(attempt, manifest)
                if manifest.get("trace_complete") is not True or torn:
                    raise InspectionError("successful run has incomplete evidence")
                completeness = "complete"
            else:
                completeness = "legacy_unverified"
        explanation = _explanation(outcome, owner, error, manifest)
        return AttemptStatus(
            run_id,
            state.invocation_id if state else None,
            run_id,
            state.command if state else record.phase,
            outcome,
            completeness,
            owner,
            stage,
            last,
            {**progress, "incomplete_tail": torn},
            error,
            paths,
            explanation,
        )

    owner = owner_state(state.owner_host, state.owner_pid)
    outcome = "failed" if state.stage == "failed" else "preparing"
    if outcome == "failed" and progress.get("failure_type") == "KeyboardInterrupt":
        outcome = "interrupted"
    completeness = "partial"
    if state.linked_download_attempt:
        # Download directories may be moved as a whole: use the retained ID,
        # never trust an arbitrary absolute path from an invocation file.
        saved = Path(state.linked_download_attempt)
        if saved.name != "manifest.json" or saved.parent.parent.name != "downloads":
            raise InspectionError("invalid download manifest path")
        download_id = _validate_run_id(saved.parent.name)
        manifest_path = _path(root, "downloads", download_id, "manifest.json")
        paths["manifest"] = str(manifest_path)
        if manifest_path.exists():
            manifest = _json(manifest_path)
            if manifest.get("status") not in {
                "success",
                "failed",
                "interrupted",
                "finalizing",
            }:
                raise InspectionError("invalid download status")
            if manifest["status"] != "finalizing" or outcome not in (
                "failed",
                "interrupted",
            ):
                outcome = manifest["status"]
            download_error = manifest.get("error", {})
            if not isinstance(download_error, dict):
                raise InspectionError("invalid download error object")
            error = download_error.get("message") or error
            if outcome == "success":
                seal = manifest.get("diagnostics", {})
                if not isinstance(seal, dict):
                    raise InspectionError("invalid download diagnostic seal")
                if seal.get("invocation_id") != state.invocation_id:
                    raise InspectionError("download invocation mismatch")
                verify_diagnostic_seal(root, seal)
                completeness = "complete"
        elif outcome not in ("failed", "interrupted"):
            outcome = "running"
    recovery = _path(root, "invocations", state.invocation_id, "recovery.json")
    if recovery.exists():
        receipt = _json(recovery)
        if (
            receipt.get("schema_version") != 1
            or receipt.get("invocation_id") != state.invocation_id
            or receipt.get("outcome") != "crashed"
            or receipt.get("invocation_sha256")
            != hashlib.sha256(Path(paths["invocation"]).read_bytes()).hexdigest()
            or receipt.get("journal_sha256") != _optional_hash(Path(paths["journal"]))
        ):
            raise InspectionError("recovery receipt integrity mismatch")
        if outcome in TERMINAL:
            raise InspectionError("recovery receipt conflicts with terminal invocation")
        outcome, error = "crashed", receipt["reason"]
        paths["recovery"] = str(recovery)
    return AttemptStatus(
        state.invocation_id,
        state.invocation_id,
        None,
        state.command,
        outcome,
        completeness,
        owner,
        stage,
        last,
        {**progress, "incomplete_tail": torn},
        error,
        paths,
        _explanation(outcome, owner, error, None),
    )


def _explanation(outcome, owner, error, manifest):
    if outcome == "success":
        sessions = (manifest or {}).get("funnel", {}).get("sessions", [])
        if sessions and not any(s["entry_fills"] for s in sessions):
            return "Completed with zero entry fills. Inspect the recorded session reasons; zero trades alone is not a software error."
        return "Authoritative outcome is success; completeness separately describes the retained evidence."
    if outcome in ("failed", "interrupted", "crashed"):
        return "Attempt did not succeed. Retained results are partial; inspect the error and evidence paths."
    if error:
        return "Invocation reported failure but no terminal outcome was committed. Results are incomplete; inspect evidence and preview recovery once the local owner is dead."
    if owner == "dead":
        return "Owner is confirmed dead on this host. Preview recovery with recover --dry-run."
    if owner == "unknown":
        return "Owner cannot be checked on this host. Investigate manually; age alone does not prove a crash."
    return "Owner process is alive. This does not prove it is making progress; inspect last_observed_at."


def inspect_attempt(root, identity):
    root = Path(root)
    try:
        return _read_status(root, identity)
    except InspectionError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        sqlite3.Error,
    ) as exc:
        raise InspectionError(
            f"invalid or unreadable attempt evidence ({type(exc).__name__})"
        ) from exc


def _optional_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _identities(root, identity):
    if identity is not None:
        return [identity]
    ids = []
    if _path(root, "runs.db").exists():
        storage = RunStorage(root, read_only=True)
        try:
            ids = storage.list_run_ids()
        finally:
            storage.close()
    # A linked invocation and its run represent exactly one attempted trial.
    for state in _invocations(root):
        if state.linked_run_id:
            if state.linked_run_id not in ids:
                raise InspectionError("linked run has no authoritative record")
        else:
            ids.append(state.invocation_id)
    return sorted(ids)


def _action(status):
    if status.outcome in TERMINAL:
        return RecoveryAction(
            status.attempt_id, "unchanged", "terminal outcome is sealed"
        )
    if status.owner_state != "dead":
        return RecoveryAction(
            status.attempt_id,
            "unchanged",
            "owner is alive"
            if status.owner_state == "alive"
            else "unknown ownership; manual investigation required",
        )
    if status.run_id and status.outcome != "running":
        return RecoveryAction(
            status.attempt_id,
            "unchanged",
            "unstarted record; manual investigation required",
        )
    return RecoveryAction(
        status.attempt_id, "mark_crashed", "local owner is confirmed dead"
    )


def recover_attempts(root, identity=None, *, apply=False):
    root = Path(root)
    if not root.is_dir():
        raise InspectionError("storage root does not exist")
    ids = _identities(root, identity)
    preview = [_action(inspect_attempt(root, item)) for item in ids]
    if not apply or not any(action.action == "mark_crashed" for action in preview):
        return preview
    with recovery_lock(root):
        # Re-read under the lock. Nothing is changed based on a stale preview.
        statuses = [inspect_attempt(root, item) for item in ids]
        results = []
        for status in statuses:
            action = _action(status)
            if action.action == "mark_crashed":
                if status.run_id:
                    storage = RunStorage(root)
                    try:
                        changed = storage._recover_dead_runs(
                            [status.run_id],
                            reason="operator recovery confirmed local owner dead",
                        )
                    finally:
                        storage.close()
                    action = replace(action, applied=bool(changed))
                else:
                    state = _invocation(root, status.invocation_id)
                    if (
                        owner_state(state.owner_host, state.owner_pid) == "dead"
                        and state.stage != "failed"
                    ):
                        receipt = {
                            "schema_version": 1,
                            "invocation_id": state.invocation_id,
                            "outcome": "crashed",
                            "recovered_at": datetime.now(UTC).isoformat(),
                            "reason": "operator recovery confirmed local owner dead",
                            "invocation_sha256": _optional_hash(
                                Path(status.paths["invocation"])
                            ),
                            "journal_sha256": _optional_hash(
                                Path(status.paths["journal"])
                            ),
                        }
                        target = _path(
                            root, "invocations", state.invocation_id, "recovery.json"
                        )
                        if target.exists():
                            raise InspectionError("recovery receipt already exists")
                        atomic_write_json(target, receipt)
                        action = replace(action, applied=True)
            results.append(action)
        return results

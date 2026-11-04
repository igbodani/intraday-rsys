"""BR-01 append-only operational journal and early invocation evidence.

Covers T01-T04 (docs/agent-workflow/backtest-readiness-tests.md); existing
run-record/redaction tests (`tests/test_run_record.py`) exercise the SQLite
terminal-outcome store this journal never replaces.
"""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from rsys.application.errors import RunConfigError
from rsys.cli.commands import EXIT_INVALID_CONFIG, EXIT_OTHER_FAILURE
from rsys.cli.commands import main as cli_main
from rsys.research.diagnostics import DiagnosticWriter, start_invocation
from rsys.research.errors import DiagnosticWriteError
from rsys.research.experiments import SecretRedactor
from rsys.research.models import DiagnosticEventCode

REPO_ROOT = Path(__file__).resolve().parents[1]
STANDALONE_FIXTURE = REPO_ROOT / "fixtures" / "standalone_run_fixture.json"


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


# ---- T01: Early visibility and correlation ----------------------------------


def test_invocation_evidence_is_readable_immediately_after_creation(tmp_path):
    writer = start_invocation(tmp_path, command="run")

    on_disk = json.loads(writer.invocation_path.read_text(encoding="utf-8"))
    assert on_disk["invocation_id"] == writer.invocation_id
    assert on_disk["stage"] == "preparing"
    assert on_disk["linked_run_id"] is None

    lines = _read_jsonl(writer.journal_path)
    assert len(lines) == 1
    assert lines[0]["event"] == "invocation_start"
    assert lines[0]["sequence"] == 1


def test_sanitized_request_identity_is_recorded_before_resolution(tmp_path):
    writer = start_invocation(tmp_path, command="download")
    writer.record_identity(input_path="configs/example.json", input_sha256="ab" * 32)

    on_disk = json.loads(writer.invocation_path.read_text(encoding="utf-8"))
    assert on_disk["input_path"] == "configs/example.json"
    assert on_disk["input_sha256"] == "ab" * 32
    assert on_disk["linked_run_id"] is None  # not yet resolved


def test_concurrent_invocations_get_distinct_directories_and_independent_sequences(
    tmp_path,
):
    first = start_invocation(tmp_path, command="run")
    second = start_invocation(tmp_path, command="run")

    assert first.invocation_id != second.invocation_id
    assert first.journal_path.parent != second.journal_path.parent

    first.event(DiagnosticEventCode.CONFIG_LOADED, "preparing")
    second.event(DiagnosticEventCode.CONFIG_LOADED, "preparing")
    second.event(DiagnosticEventCode.PREFLIGHT_CHECK, "preparing")

    assert [r["sequence"] for r in _read_jsonl(first.journal_path)] == [1, 2]
    assert [r["sequence"] for r in _read_jsonl(second.journal_path)] == [1, 2, 3]


def test_linking_a_run_makes_invocation_and_run_agree_once(tmp_path):
    writer = start_invocation(tmp_path, command="run")
    writer.link_run("20990101T000000000000-aaaaaaaa")

    on_disk = json.loads(writer.invocation_path.read_text(encoding="utf-8"))
    assert on_disk["linked_run_id"] == "20990101T000000000000-aaaaaaaa"
    assert on_disk["stage"] == "resolved"
    assert writer.linked_run_id == "20990101T000000000000-aaaaaaaa"


def _mp_child_pauses_mid_preparation(out_dir, ready, release) -> None:
    writer = start_invocation(out_dir, command="run")
    writer.event(DiagnosticEventCode.CONFIG_LOADED, "preparing")
    ready.put(writer.invocation_id)
    release.wait(timeout=30)
    writer.link_run("linked-after-release")


def test_a_child_paused_mid_preparation_is_inspectable_from_outside(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    release = ctx.Event()
    proc = ctx.Process(
        target=_mp_child_pauses_mid_preparation, args=(str(tmp_path), ready, release)
    )
    proc.start()
    try:
        invocation_id = ready.get(timeout=30)
        invocation_dir = tmp_path / "invocations" / invocation_id
        on_disk = json.loads(
            (invocation_dir / "invocation.json").read_text(encoding="utf-8")
        )
        assert on_disk["stage"] == "preparing"
        assert on_disk["linked_run_id"] is None

        lines = _read_jsonl(invocation_dir / "diagnostics.jsonl")
        assert [r["event"] for r in lines] == ["invocation_start", "config_loaded"]
    finally:
        release.set()
        proc.join(timeout=30)


def _mp_child_crashes_mid_preparation(out_dir) -> None:
    writer = start_invocation(out_dir, command="run")
    writer.event(DiagnosticEventCode.CONFIG_LOADED, "preparing")
    os._exit(1)  # simulate a hard crash: never link_run(), fail(), or close()


def test_a_killed_child_leaves_an_inspectable_never_resolved_invocation(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=_mp_child_crashes_mid_preparation, args=(str(tmp_path),))
    proc.start()
    proc.join(timeout=30)
    assert not proc.is_alive()

    invocation_dirs = list((tmp_path / "invocations").iterdir())
    assert len(invocation_dirs) == 1
    on_disk = json.loads(
        (invocation_dirs[0] / "invocation.json").read_text(encoding="utf-8")
    )
    # Never resolved, never marked failed, and never silently "succeeded" either.
    assert on_disk["stage"] == "preparing"
    assert on_disk["linked_run_id"] is None


def test_run_cli_entry_links_the_invocation_to_its_resolved_run_exactly_once(tmp_path):
    from rsys.application.run import run_cli_entry

    out_dir = tmp_path / "out"
    _summary, run_id, _trace_path, manifest_path = run_cli_entry(
        STANDALONE_FIXTURE, out_dir
    )

    invocation_dirs = list((out_dir / "invocations").iterdir())
    assert len(invocation_dirs) == 1
    on_disk = json.loads(
        (invocation_dirs[0] / "invocation.json").read_text(encoding="utf-8")
    )
    assert on_disk["linked_run_id"] == run_id
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == run_id


# ---- T02: Secret handling ----------------------------------------------------

_SECRET_VALUE = 'sk-FAKE-1234567890\nwith-newline"and-quote'


def test_fail_redacts_known_secret_values_from_exception_text(tmp_path):
    writer = start_invocation(
        tmp_path, command="run", redactor=SecretRedactor([_SECRET_VALUE], known=True)
    )
    writer.fail(ValueError(f"upstream rejected credential {_SECRET_VALUE}"))

    assert _SECRET_VALUE not in writer.journal_path.read_text(encoding="utf-8")
    assert _SECRET_VALUE not in writer.invocation_path.read_text(encoding="utf-8")


def test_fail_withholds_free_text_when_redaction_context_is_unknown(tmp_path):
    writer = start_invocation(
        tmp_path, command="run", redactor=SecretRedactor.unknown()
    )
    writer.fail(ValueError(f"leaked {_SECRET_VALUE}"))

    failure = next(
        r for r in _read_jsonl(writer.journal_path) if r["event"] == "failure"
    )
    assert _SECRET_VALUE not in failure["details"]["message"]
    assert "withheld" in failure["details"]["message"]


def test_event_details_are_redacted_before_serialization(tmp_path):
    writer = start_invocation(tmp_path, command="run")
    writer.event(
        DiagnosticEventCode.CONFIG_LOADED,
        "preparing",
        details={"nested": {"api_key": _SECRET_VALUE, "symbol": "AAPL"}},
    )

    last = _read_jsonl(writer.journal_path)[-1]
    assert last["details"]["nested"]["api_key"] == "REDACTED"
    assert last["details"]["nested"]["symbol"] == "AAPL"


def test_no_injected_secret_appears_in_a_real_backtest_failure_attempt(tmp_path):
    from rsys.application.errors import RunConfigError
    from rsys.application.historical import backtest

    config = {
        "schema_version": 1,
        "cache": str(tmp_path / "cache"),
        "dataset": {"symbol": "AAPL", "feed": "iex", "asof": "-"},
        "objects": {},
        "partitions": {"api_key": _SECRET_VALUE},
        "purpose": "research",
        "sessions": ["2024-11-27"],
        "account": {},
        "strategy": {},
        "scanner": {
            "top_n": 1,
            "lookback_sessions": 1,
            "max_lookback_calendar_sessions": 1,
            "min_dollar_volume": "0",
            "max_current_bar_age_ms": 1000,
        },
        "action_review": {
            "status": "verified_none",
            "source": "manual",
            "start": "2024-11-27",
            "end": "2024-11-27",
        },
        "timing_basis": "final_bars_modeled_at_end",
    }
    raw = json.dumps(config).encode()
    with pytest.raises(RunConfigError):
        backtest(config, raw, tmp_path / "out")

    for path in (tmp_path / "out").rglob("*"):
        if path.is_file():
            assert _SECRET_VALUE not in path.read_text(
                encoding="utf-8", errors="ignore"
            )


# ---- T03: Append, flush and encoding boundaries ------------------------------


def test_event_schema_has_required_fields_and_monotonic_sequence(tmp_path):
    writer = start_invocation(tmp_path, command="run")
    writer.event(DiagnosticEventCode.CONFIG_LOADED, "preparing")
    writer.event(DiagnosticEventCode.PREFLIGHT_CHECK, "preparing")

    lines = _read_jsonl(writer.journal_path)
    assert [r["sequence"] for r in lines] == [1, 2, 3]
    for record in lines:
        assert record["schema_version"] == 1
        assert record["invocation_id"] == writer.invocation_id
        assert isinstance(record["wall_time"], str)
        assert record["level"] in ("info", "warning", "error")
        assert {"event", "stage", "details"} <= record.keys()


def test_embedded_newlines_and_html_stay_single_line_encoded_data(tmp_path):
    writer = start_invocation(
        tmp_path, command="run", redactor=SecretRedactor([], known=True)
    )
    writer.event(
        DiagnosticEventCode.FAILURE,
        "preparing",
        details={"message": "line1\nline2\t<script>alert(1)</script>"},
        level="error",
    )

    raw_lines = writer.journal_path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 2  # invocation_start + this event
    decoded = json.loads(raw_lines[-1])
    assert decoded["details"]["message"] == "line1\nline2\t<script>alert(1)</script>"


def test_flush_and_sync_boundaries_are_honored(tmp_path, monkeypatch):
    synced = []
    real_fsync = os.fsync

    def _fsync(fd):
        synced.append(os.fstat(fd).st_ino)
        return real_fsync(fd)

    monkeypatch.setattr("rsys.research.diagnostics.os.fsync", _fsync)

    writer = start_invocation(tmp_path, command="run")  # INVOCATION_START syncs
    journal_inode = writer.journal_path.stat().st_ino
    assert synced.count(journal_inode) == 1
    writer.event(DiagnosticEventCode.CONFIG_LOADED, "preparing")  # ordinary info
    assert synced.count(journal_inode) == 1
    writer.event(DiagnosticEventCode.ARTIFACTS_READY, "finalizing")  # sync-mandated
    assert synced.count(journal_inode) == 2
    writer.event(DiagnosticEventCode.CONFIG_LOADED, "preparing", level="error")
    assert synced.count(journal_inode) == 3


def test_append_failure_preserves_prior_lines_and_raises_diagnostic_write_error(
    tmp_path,
):
    writer = start_invocation(tmp_path, command="run")
    writer.event(DiagnosticEventCode.CONFIG_LOADED, "preparing")
    before = writer.journal_path.read_bytes()

    def _boom(_path):
        raise OSError("simulated disk-full on append")

    writer._open_append = _boom
    with pytest.raises(DiagnosticWriteError):
        writer.event(DiagnosticEventCode.PREFLIGHT_CHECK, "preparing")

    assert writer.journal_path.read_bytes() == before  # no partial record appended
    on_disk = json.loads(writer.invocation_path.read_text(encoding="utf-8"))
    assert on_disk["stage"] == "failed"
    assert on_disk["write_failure"] is not None


def test_a_torn_trailing_line_is_distinguishable_from_the_valid_prefix(tmp_path):
    writer = start_invocation(tmp_path, command="run")
    writer.event(DiagnosticEventCode.CONFIG_LOADED, "preparing")
    writer.event(DiagnosticEventCode.PREFLIGHT_CHECK, "preparing")
    complete_bytes = writer.journal_path.read_bytes()

    # Simulate an abrupt kill mid-append: a flushed-but-incomplete trailing record.
    with open(writer.journal_path, "ab") as handle:
        handle.write(b'{"schema_version": 1, "sequence": 4, "event": "preflight')
        handle.flush()

    on_disk = writer.journal_path.read_bytes()
    assert on_disk.startswith(complete_bytes)
    raw_lines = on_disk.split(b"\n")
    for line in raw_lines[:-1]:
        json.loads(
            line
        )  # every earlier record parses as one complete, independent line
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw_lines[-1])  # the torn trailing record is identifiably incomplete


# ---- T04: Error on error ------------------------------------------------------


def test_creation_failure_raises_diagnostic_write_error(tmp_path, monkeypatch):
    def _boom(self, parents=False, exist_ok=False):
        raise PermissionError("simulated permission denial")

    monkeypatch.setattr(Path, "mkdir", _boom)
    with pytest.raises(DiagnosticWriteError):
        start_invocation(tmp_path, command="run")


def test_append_failure_is_a_distinct_error_never_a_silent_success(tmp_path):
    writer = start_invocation(tmp_path, command="run")

    def _boom(_path):
        raise OSError("simulated disk-full on append")

    writer._open_append = _boom
    with pytest.raises(DiagnosticWriteError):
        writer.artifacts_ready()


def test_invocation_manifest_write_failure_is_a_distinct_error_never_a_silent_success(
    tmp_path, monkeypatch
):
    """T04: "final manifest" (invocation.json) writing is a distinct code path from
    journal append (`_atomic_write_json`, not `_open_append`); a failure there must
    raise `DiagnosticWriteError` just as reliably, never silently succeed."""
    import rsys.research.diagnostics as diagnostics_module

    writer = start_invocation(tmp_path, command="run")

    def _boom(_path, _value):
        raise OSError("simulated disk-full on invocation.json replace")

    monkeypatch.setattr(diagnostics_module, "_atomic_write_json", _boom)
    with pytest.raises(DiagnosticWriteError):
        writer.set_stage("resolved")


def test_a_preparation_error_followed_by_a_journal_write_failure_keeps_both_causes(
    tmp_path,
):
    """T04: a normal preparation error followed by a journal-write failure must
    preserve the primary failure (never replace/mask it) while still identifying
    the secondary write problem."""
    writer = start_invocation(tmp_path, command="run")

    def _boom(_path):
        raise OSError("simulated disk-full on append")

    writer._open_append = _boom
    primary = ValueError("malformed historical request")
    writer.fail(primary)  # must not raise; the primary exception stays primary.
    diagnostic_write_error = primary.diagnostic_write_error
    assert isinstance(diagnostic_write_error, DiagnosticWriteError)
    # The secondary journal-append OSError is preserved as the explicit cause, not swallowed.
    assert isinstance(diagnostic_write_error.__cause__, OSError)


def test_run_cli_entry_never_reports_success_when_invocation_cannot_be_created(
    tmp_path, monkeypatch
):
    import rsys.application.run as run_module

    def _boom(*_args, **_kwargs):
        raise DiagnosticWriteError("simulated unwritable disk")

    monkeypatch.setattr("rsys.research.diagnostics.start_invocation", _boom)
    with pytest.raises(DiagnosticWriteError):
        run_module.run_cli_entry(STANDALONE_FIXTURE, tmp_path / "out")


def test_run_cli_entry_saves_the_manifest_but_never_journals_readiness_when_the_readiness_write_itself_fails(
    tmp_path, monkeypatch
):
    """ARTIFACTS_READY is now recorded only after the manifest (and the rest of
    the attempt's evidence) is already fully saved, so the journal's own
    ARTIFACTS_READY append can fail independently of that already-successful
    data write: the manifest is not retroactively erased, but the journal must
    still never show readiness for an attempt whose readiness event itself did
    not succeed."""
    import rsys.application.run as run_module

    def _boom(self, *, stage="finalizing"):
        raise DiagnosticWriteError("simulated unwritable disk at artifacts_ready")

    monkeypatch.setattr(DiagnosticWriter, "artifacts_ready", _boom)
    out_dir = tmp_path / "out"
    invocation = start_invocation(out_dir, command="run")
    with pytest.raises(DiagnosticWriteError):
        run_module.run_cli_entry(STANDALONE_FIXTURE, out_dir, invocation=invocation)

    manifests = list(out_dir.glob("attempts/*/manifest.json"))
    assert len(manifests) == 1  # the manifest itself was already fully saved

    events = [r["event"] for r in _read_jsonl(invocation.journal_path)]
    assert events[-1] == "failure"
    assert "artifacts_ready" not in events


def test_run_cli_entry_marks_the_invocation_failed_on_an_attempt_directory_collision(
    tmp_path, monkeypatch
):
    """A collision/permission failure creating the attempt directory must still
    leave a FAILURE event and a failed stage, never an invocation stuck
    "resolved" with no record of what happened (regression for a reviewed gap)."""
    import rsys.application.run as run_module
    import rsys.research.experiments as experiments_mod

    fixed_run_id = "FIXED-RUN-ID"
    monkeypatch.setattr(experiments_mod, "new_run_id", lambda: fixed_run_id)
    out_dir = tmp_path / "out"
    (out_dir / "attempts" / fixed_run_id).mkdir(parents=True)

    with pytest.raises(FileExistsError):
        run_module.run_cli_entry(STANDALONE_FIXTURE, out_dir)

    invocation_dirs = list((out_dir / "invocations").iterdir())
    assert len(invocation_dirs) == 1
    on_disk = json.loads(
        (invocation_dirs[0] / "invocation.json").read_text(encoding="utf-8")
    )
    assert on_disk["stage"] == "failed"
    assert on_disk["linked_run_id"] == fixed_run_id
    failure_events = [
        e
        for e in _read_jsonl(invocation_dirs[0] / "diagnostics.jsonl")
        if e["event"] == DiagnosticEventCode.FAILURE.value
    ]
    assert len(failure_events) == 1
    # link_run() already moved the invocation to "resolved" before the collision;
    # the FAILURE event must reflect that true lifecycle position, not "preparing".
    assert failure_events[0]["stage"] == "resolved"


def test_run_batch_cli_marks_the_invocation_failed_on_an_attempt_directory_collision(
    tmp_path, monkeypatch
):
    """Same guarantee as above, exercised through the schema-2 path shared by
    `rsys run`, `backtest()` and `retain_input_failure()`."""
    import json as json_mod

    import rsys.application.run as run_module
    import rsys.research.experiments as experiments_mod

    fixed_run_id = "FIXED-BATCH-ID"
    monkeypatch.setattr(experiments_mod, "new_run_id", lambda: fixed_run_id)
    out_dir = tmp_path / "out"
    (out_dir / "attempts" / fixed_run_id).mkdir(parents=True)

    sessions_fixture = REPO_ROOT / "fixtures" / "sessions_bar_fixture.json"
    config_path = tmp_path / "sessions.json"
    config_path.write_text(
        sessions_fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert json_mod.loads(config_path.read_text())["schema_version"] == 2

    with pytest.raises(FileExistsError):
        run_module.run_cli_entry(config_path, out_dir)

    invocation_dirs = list((out_dir / "invocations").iterdir())
    assert len(invocation_dirs) == 1
    on_disk = json.loads(
        (invocation_dirs[0] / "invocation.json").read_text(encoding="utf-8")
    )
    assert on_disk["stage"] == "failed"
    assert on_disk["linked_run_id"] == fixed_run_id
    failure_events = [
        e
        for e in _read_jsonl(invocation_dirs[0] / "diagnostics.jsonl")
        if e["event"] == DiagnosticEventCode.FAILURE.value
    ]
    assert len(failure_events) == 1
    # link_run() already moved the invocation to "resolved" before the collision;
    # the FAILURE event must reflect that true lifecycle position, not "preparing".
    assert failure_events[0]["stage"] == "resolved"


def test_download_marks_the_invocation_failed_without_writing_into_a_colliding_attempt(
    tmp_path, monkeypatch
):
    """`download()`'s attempt directory used to be created before any exception
    handling existed at all; a collision there must still fail loudly, mark the
    invocation failed, and preserve the existing attempt's files. The new
    invocation owns its journal but does not own the colliding directory."""
    import rsys.application.historical as historical_mod

    class _FixedUUID4:
        hex = "fixed-download-id"

    monkeypatch.setattr(historical_mod, "uuid4", lambda: _FixedUUID4())
    out_dir = tmp_path / "out"
    (out_dir / "downloads" / "fixed-download-id").mkdir(parents=True)

    config = {
        "schema_version": 1,
        "cache": str(out_dir / "cache"),
        "dataset": {"symbol": "AAPL", "feed": "iex", "asof": "-"},
        "start": "2024-11-27",
        "end": "2024-11-27",
        "partitions": {},
        "purpose": "research",
    }

    with pytest.raises(FileExistsError):
        historical_mod.download(config, out_dir)

    invocation_dirs = list((out_dir / "invocations").iterdir())
    assert len(invocation_dirs) == 1
    on_disk = json.loads(
        (invocation_dirs[0] / "invocation.json").read_text(encoding="utf-8")
    )
    assert on_disk["stage"] == "failed"
    events = [e["event"] for e in _read_jsonl(invocation_dirs[0] / "diagnostics.jsonl")]
    assert DiagnosticEventCode.FAILURE.value in events

    manifest_path = out_dir / "downloads" / "fixed-download-id" / "manifest.json"
    assert not manifest_path.exists()


def test_download_failure_after_resolution_reports_resolved_not_preparing(tmp_path):
    """Once `link_download_attempt()` has resolved the invocation, every later
    failure event must reflect
    that true stage, never the stale "preparing" label from before resolution
    (regression for a reviewed gap)."""
    import rsys.application.historical as historical_mod

    out_dir = tmp_path / "out"
    config = {
        "schema_version": 1,
        "cache": str(out_dir / "cache"),
        "dataset": {"symbol": "AAPL", "feed": "iex", "asof": "-"},
        "start": "2024-11-27",
        "end": "2020-01-01",  # start > end: fails validation AFTER the attempt
        "partitions": [
            {"name": "development", "start": "2020-01-01", "end": "2030-01-01"}
        ],  # directory/link_download_attempt() already succeeded by this point.
        "purpose": "development",
    }

    with pytest.raises(ValueError, match="Invalid download date range"):
        historical_mod.download(config, out_dir)

    invocation_dirs = list((out_dir / "invocations").iterdir())
    assert len(invocation_dirs) == 1
    on_disk = json.loads(
        (invocation_dirs[0] / "invocation.json").read_text(encoding="utf-8")
    )
    assert on_disk["stage"] == "failed"
    assert on_disk["linked_download_attempt"] is not None

    records = _read_jsonl(invocation_dirs[0] / "diagnostics.jsonl")
    failure = next(
        r for r in records if r["event"] == DiagnosticEventCode.FAILURE.value
    )
    assert failure["stage"] == "resolved"
    assert records[-1] == failure
    assert (
        json.loads(Path(on_disk["linked_download_attempt"]).read_text())["status"]
        == "failed"
    )


def test_cli_run_prints_a_distinct_nonzero_failure_when_evidence_cannot_be_saved(
    tmp_path, monkeypatch, capsys
):
    def _boom(*_args, **_kwargs):
        raise DiagnosticWriteError("simulated unwritable disk")

    monkeypatch.setattr("rsys.research.diagnostics.start_invocation", _boom)

    exit_code = cli_main(
        ["run", "--config", str(STANDALONE_FIXTURE), "--out", str(tmp_path / "out")]
    )
    captured = capsys.readouterr()
    assert exit_code == EXIT_OTHER_FAILURE
    assert "invocation evidence could not be saved" in captured.err
    # No retained attempt directory implies no manifest falsely claims completion.
    assert not (tmp_path / "out" / "attempts").exists()


def test_cli_data_download_prints_a_distinct_nonzero_failure_when_evidence_cannot_be_saved(
    tmp_path, monkeypatch, capsys
):
    def _boom(*_args, **_kwargs):
        raise DiagnosticWriteError("simulated unwritable disk")

    monkeypatch.setattr("rsys.research.diagnostics.start_invocation", _boom)

    config_path = tmp_path / "download.json"
    config_path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    exit_code = cli_main(
        ["download", "--config", str(config_path), "--out", str(tmp_path / "out")]
    )
    captured = capsys.readouterr()
    assert exit_code == EXIT_OTHER_FAILURE
    assert "invocation evidence could not be saved" in captured.err


def test_cli_run_reports_the_primary_failure_and_notes_the_journal_write_problem(
    tmp_path, monkeypatch, capsys
):
    """T04: a real application failure (an unreadable config, here) occurring while
    the journal append also fails must surface BOTH -- the primary failure's own
    message/exit code, never replaced by the secondary write problem -- plus a
    distinct note identifying that the evidence write itself also failed."""
    import rsys.research.diagnostics as diagnostics_module

    real_start_invocation = diagnostics_module.start_invocation
    calls = {"n": 0}

    def flaky_open_append(path):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("simulated disk full")
        return open(path, "ab")

    def patched_start_invocation(*args, **kwargs):
        kwargs["open_append"] = flaky_open_append
        return real_start_invocation(*args, **kwargs)

    monkeypatch.setattr(
        "rsys.research.diagnostics.start_invocation", patched_start_invocation
    )

    missing_config = tmp_path / "does_not_exist.json"
    exit_code = cli_main(
        ["run", "--config", str(missing_config), "--out", str(tmp_path / "out")]
    )
    captured = capsys.readouterr()

    # The primary failure (RunConfigError) keeps its own exit code and message --
    # it is never masked by the secondary DiagnosticWriteError.
    assert exit_code == EXIT_INVALID_CONFIG
    assert "cannot read run config" in captured.err
    # The secondary write problem is still identified, distinctly, alongside it.
    assert "invocation evidence could not be fully saved" in captured.err


def test_cli_run_reports_the_primary_failure_and_notes_an_invocation_manifest_write_problem(
    tmp_path, monkeypatch, capsys
):
    """T04 "final manifest... writing": `invocation.json` (`_atomic_write_json`) is
    a distinct code path from journal append (`_open_append`, already covered
    above). A failure there -- here, from the `finally`-block finalization step in
    `run_cli_entry`, not from `fail()` itself -- must likewise never mask the
    primary application failure (`protect_evidence_write`'s own code path)."""
    import rsys.research.diagnostics as diagnostics_module

    real_atomic_write_json = diagnostics_module._atomic_write_json
    calls = {"n": 0}

    def flaky_atomic_write_json(path, value):
        calls["n"] += 1
        if calls["n"] >= 4:
            raise OSError("simulated disk full on invocation.json replace")
        return real_atomic_write_json(path, value)

    monkeypatch.setattr(
        diagnostics_module, "_atomic_write_json", flaky_atomic_write_json
    )

    missing_config = tmp_path / "does_not_exist.json"
    exit_code = cli_main(
        ["run", "--config", str(missing_config), "--out", str(tmp_path / "out")]
    )
    captured = capsys.readouterr()

    # The primary failure (RunConfigError) keeps its own exit code and message --
    # it is never masked by the secondary invocation.json write problem.
    assert exit_code == EXIT_INVALID_CONFIG
    assert "cannot read run config" in captured.err
    # The secondary write problem is still identified, distinctly, alongside it.
    assert "invocation evidence could not be fully saved" in captured.err


def test_run_cli_entry_preserves_the_primary_failure_when_its_own_manifest_json_write_fails(
    tmp_path, monkeypatch
):
    """T04 "final manifest... writing": `run_cli_entry`'s own attempt manifest.json
    (`_write_json`, plain `OSError`, not `DiagnosticWriteError`) sits inside the
    same `finally` block as the trace/diagnostics finalization. A failure there
    must likewise never mask whatever primary application exception is already
    propagating through that `finally` block."""
    import rsys.application.run as run_module

    real_write_json = run_module._write_json

    def flaky_write_json(path, value):
        if path.name == "manifest.json":
            raise OSError("simulated disk full on manifest.json write")
        return real_write_json(path, value)

    monkeypatch.setattr(run_module, "_write_json", flaky_write_json)

    missing_config = tmp_path / "does_not_exist.json"
    with pytest.raises(RunConfigError) as excinfo:
        run_module.run_cli_entry(missing_config, tmp_path / "out")

    assert "cannot read run config" in str(excinfo.value)
    diagnostic_write_error = excinfo.value.diagnostic_write_error
    assert isinstance(diagnostic_write_error, OSError)
    assert "manifest.json" in str(diagnostic_write_error)


def test_download_preserves_the_primary_failure_when_its_own_manifest_json_write_fails(
    tmp_path, monkeypatch
):
    """Same guarantee as above, for `historical.download()`'s own attempt
    manifest.json write (also plain `OSError`, in the same `finally` block as the
    diagnostics finalization)."""
    import rsys.application.historical as historical_mod

    real_write_json = historical_mod.write_json

    def flaky_write_json(path, value):
        if path.name == "manifest.json":
            raise OSError("simulated disk full on manifest.json write")
        return real_write_json(path, value)

    monkeypatch.setattr(historical_mod, "write_json", flaky_write_json)

    out_dir = tmp_path / "out"
    config = {
        "schema_version": 2,  # unsupported: only schema_version 1 is accepted
        "cache": str(out_dir / "cache"),
        "dataset": {"symbol": "AAPL", "feed": "iex", "asof": "-"},
        "start": "2024-01-02",
        "end": "2024-01-02",
        "partitions": [],
        "purpose": "development",
    }
    with pytest.raises(ValueError) as excinfo:
        historical_mod.download(config, out_dir)

    assert "Unsupported download schema" in str(excinfo.value)
    diagnostic_write_error = excinfo.value.diagnostic_write_error
    assert isinstance(diagnostic_write_error, OSError)
    assert "manifest.json" in str(diagnostic_write_error)


def test_run_batch_cli_preserves_the_primary_failure_when_an_artifact_write_fails(
    tmp_path, monkeypatch
):
    """Same guarantee as above, for `run_batch_cli()`'s long `finally` block, which
    writes many artifacts (trace/input/resolved_config/fills/metrics/sessions/
    manifest) before the authoritative terminal commit. A failure in *any* of
    them -- not just the last one -- must never mask the primary failure already
    propagating through that block."""
    import rsys.application.batch as batch_module

    real_write_json = batch_module.write_json

    def flaky_write_json(path, value):
        if path.name == "input.json":
            raise OSError("simulated disk full on input.json write")
        return real_write_json(path, value)

    monkeypatch.setattr(batch_module, "write_json", flaky_write_json)

    config = {"schema_version": 2, "input_mode": "bad_mode"}  # missing required fields
    with pytest.raises(RunConfigError) as excinfo:
        batch_module.run_batch_cli(config, b"{}", tmp_path / "out", report=False)

    assert "missing required config fields" in str(excinfo.value)
    diagnostic_write_error = excinfo.value.diagnostic_write_error
    assert isinstance(diagnostic_write_error, OSError)
    assert "input.json" in str(diagnostic_write_error)


# ---- Sole (non-masking) manifest-write failure must still be journaled -----
#
# Distinct from the masking tests above: here the *manifest write itself* is
# the only failure -- nothing else is already in flight. `protect_evidence_write`
# correctly re-raises a bare failure in this case (there is nothing to attach it
# to), but that bare failure must still be journaled as a FAILURE event before
# it propagates, never silently dropped with a journal ending at ARTIFACTS_READY
# that is indistinguishable from a true success (BR-01 ticket step 8: "Avoid a
# false committed-success event"; T04: "No case prints successful completion or
# leaves a comparable successful record"). Since ARTIFACTS_READY is now recorded
# only after the manifest write itself succeeds, a manifest-write failure also
# means ARTIFACTS_READY is never reached at all for that attempt -- the journal
# must never show readiness for artifacts that were not actually all saved.


def test_run_cli_entry_journals_a_failure_when_its_sole_manifest_write_fails(
    tmp_path, monkeypatch
):
    import rsys.application.run as run_module

    real_write_json = run_module._write_json

    def flaky_write_json(path, value):
        if path.name == "manifest.json":
            raise OSError("simulated disk full on manifest.json write")
        return real_write_json(path, value)

    monkeypatch.setattr(run_module, "_write_json", flaky_write_json)

    invocation = start_invocation(tmp_path / "out", command="run")
    with pytest.raises(OSError, match="simulated disk full on manifest.json write"):
        run_module.run_cli_entry(
            STANDALONE_FIXTURE, tmp_path / "out", invocation=invocation
        )

    events = [r["event"] for r in _read_jsonl(invocation.journal_path)]
    assert events[-1] == "failure"
    assert "artifacts_ready" not in events


def test_download_journals_a_failure_when_its_sole_manifest_write_fails(
    tmp_path, monkeypatch
):
    from test_alpaca_pipeline import cached

    import rsys.application.historical as historical_mod

    acquisition, _ = cached(tmp_path)

    real_write_json = historical_mod.write_json

    def flaky_write_json(path, value):
        if path.name == "manifest.json":
            raise OSError("simulated disk full on manifest.json write")
        return real_write_json(path, value)

    monkeypatch.setattr(historical_mod, "write_json", flaky_write_json)

    # `cached()` already populated the cache for this acquisition's requests, so
    # this re-download is served entirely from cache (no client/network needed).
    out_dir = tmp_path / "out"
    invocation = start_invocation(out_dir, command="download")
    with pytest.raises(
        OSError, match="simulated disk full on manifest.json write"
    ) as excinfo:
        historical_mod.download(acquisition, out_dir, invocation=invocation)

    events = [r["event"] for r in _read_jsonl(invocation.journal_path)]
    assert events[-1] == "failure"
    assert "artifacts_ready" not in events
    # Usability: the attempt's manifest location must still be surfaced for this
    # failure shape, mirroring the main exception handler.
    assert excinfo.value.manifest_path is not None
    assert excinfo.value.manifest_path.name == "manifest.json"
    assert excinfo.value.manifest_path.parent.parent == out_dir / "downloads"


def test_run_batch_cli_journals_a_failure_when_its_sole_manifest_write_fails(
    tmp_path, monkeypatch
):
    from test_public_sessions import config as session_config

    import rsys.application.batch as batch_module

    real_write_json = batch_module.write_json

    def flaky_write_json(path, value):
        if path.name == "manifest.json":
            raise OSError("simulated disk full on manifest.json write")
        return real_write_json(path, value)

    monkeypatch.setattr(batch_module, "write_json", flaky_write_json)

    cfg = session_config("trade")
    out_dir = tmp_path / "out"
    invocation = start_invocation(out_dir, command="run")
    with pytest.raises(
        OSError, match="simulated disk full on manifest.json write"
    ) as excinfo:
        batch_module.run_batch_cli(
            cfg,
            json.dumps(cfg).encode("utf-8"),
            out_dir,
            report=False,
            invocation=invocation,
        )

    events = [r["event"] for r in _read_jsonl(invocation.journal_path)]
    assert events[-1] == "failure"
    assert "artifacts_ready" not in events
    # Usability: run_id/trace/manifest locations must still be surfaced for
    # this failure shape, mirroring the main exception handler.
    assert excinfo.value.run_id
    assert excinfo.value.trace_path is not None
    assert excinfo.value.manifest_path is not None
    assert excinfo.value.trace_path.exists()


def test_run_batch_cli_journals_a_failure_when_its_sole_finalizing_stage_write_fails(
    tmp_path, monkeypatch
):
    """Distinct from the manifest-write test above: here the *earlier*,
    separately-protected `set_stage("finalizing")` write is the only failure --
    a sibling write in the same `finally` block to the manifest write, but
    without its own dedicated failure-journaling wrapper until this regression
    test. A bare failure here (nothing already in flight) must be journaled
    just as reliably, never left to silently escape with no failure event
    recorded at all (confirmed reproducible before this fix: the journal ended
    at `invocation_start` with no trailing `failure`)."""
    from test_public_sessions import config as session_config

    import rsys.application.batch as batch_module
    from rsys.research.diagnostics import DiagnosticWriter

    real_set_stage = DiagnosticWriter.set_stage

    def flaky_set_stage(self, stage):
        if stage == "finalizing":
            raise DiagnosticWriteError("simulated unwritable disk at finalizing stage")
        return real_set_stage(self, stage)

    monkeypatch.setattr(DiagnosticWriter, "set_stage", flaky_set_stage)

    cfg = session_config("trade")
    out_dir = tmp_path / "out"
    invocation = start_invocation(out_dir, command="run")
    with pytest.raises(
        DiagnosticWriteError, match="simulated unwritable disk at finalizing stage"
    ) as excinfo:
        batch_module.run_batch_cli(
            cfg,
            json.dumps(cfg).encode("utf-8"),
            out_dir,
            report=False,
            invocation=invocation,
        )

    events = [r["event"] for r in _read_jsonl(invocation.journal_path)]
    assert events[-1] == "failure"
    assert "artifacts_ready" not in events
    assert excinfo.value.run_id
    assert excinfo.value.trace_path is not None
    assert excinfo.value.manifest_path is not None


# ---- ARTIFACTS_READY only after every required file is already on disk -----
#
# Distinct from the failure tests above: on the success path, ARTIFACTS_READY
# must be recorded strictly *after* the attempt's manifest (the last required
# artifact) has actually been written, never before -- so the event can never
# claim readiness for an attempt whose evidence is still incomplete.


def test_run_cli_entry_records_artifacts_ready_only_after_the_manifest_is_saved(
    tmp_path, monkeypatch
):
    import rsys.application.run as run_module

    order: list[str] = []
    real_write_json = run_module._write_json
    real_artifacts_ready = DiagnosticWriter.artifacts_ready

    def spying_write_json(path, value):
        if path.name == "manifest.json":
            order.append("manifest_written")
        return real_write_json(path, value)

    def spying_artifacts_ready(self, *, stage="finalizing"):
        order.append("artifacts_ready")
        return real_artifacts_ready(self, stage=stage)

    monkeypatch.setattr(run_module, "_write_json", spying_write_json)
    monkeypatch.setattr(DiagnosticWriter, "artifacts_ready", spying_artifacts_ready)

    _summary, _run_id, _trace_path, manifest_path = run_module.run_cli_entry(
        STANDALONE_FIXTURE, tmp_path / "out"
    )

    assert order == ["manifest_written", "artifacts_ready"]
    assert manifest_path.exists()


def test_download_records_artifacts_ready_only_after_the_manifest_is_saved(
    tmp_path, monkeypatch
):
    from test_alpaca_pipeline import cached

    import rsys.application.historical as historical_mod

    acquisition, _ = cached(tmp_path)

    order: list[str] = []
    real_write_json = historical_mod.write_json
    real_artifacts_ready = DiagnosticWriter.artifacts_ready

    def spying_write_json(path, value):
        if path.name == "manifest.json":
            order.append("manifest_" + value["status"])
        return real_write_json(path, value)

    def spying_artifacts_ready(self, *, stage="finalizing"):
        assert (
            json.loads(Path(self._record.linked_download_attempt).read_text())["status"]
            == "finalizing"
        )
        order.append("artifacts_ready")
        return real_artifacts_ready(self, stage=stage)

    monkeypatch.setattr(historical_mod, "write_json", spying_write_json)
    monkeypatch.setattr(DiagnosticWriter, "artifacts_ready", spying_artifacts_ready)

    out_dir = tmp_path / "out"
    report, manifest_path = historical_mod.download(acquisition, out_dir)

    assert order == ["manifest_finalizing", "artifacts_ready", "manifest_success"]
    assert manifest_path.exists()
    assert report["status"] == "success"


def test_run_batch_cli_records_artifacts_ready_only_after_the_manifest_is_saved(
    tmp_path, monkeypatch
):
    from test_public_sessions import config as session_config

    import rsys.application.batch as batch_module

    order: list[str] = []
    real_write_json = batch_module.write_json
    real_artifacts_ready = DiagnosticWriter.artifacts_ready

    def spying_write_json(path, value):
        if path.name == "manifest.json":
            order.append("manifest_written")
        return real_write_json(path, value)

    def spying_artifacts_ready(self, *, stage="finalizing"):
        order.append("artifacts_ready")
        return real_artifacts_ready(self, stage=stage)

    monkeypatch.setattr(batch_module, "write_json", spying_write_json)
    monkeypatch.setattr(DiagnosticWriter, "artifacts_ready", spying_artifacts_ready)

    cfg = session_config("trade")
    out_dir = tmp_path / "out"
    _summary, _run_id, _trace_path, manifest_path = batch_module.run_batch_cli(
        cfg, json.dumps(cfg).encode("utf-8"), out_dir, report=False
    )

    assert order == ["manifest_written", "artifacts_ready"]
    assert manifest_path.exists()


# ---- Usability: evidence locations must be printed on a final-save failure --
#
# The CLI's generic failure path (`_print_attempt_paths`) only has something to
# print when the propagating exception actually carries `run_id`/`trace_path`/
# `manifest_path`. Prove this end to end through the public CLI entry point for
# the sole-manifest-write-failure shape added above.


def test_cli_run_prints_evidence_locations_when_its_sole_manifest_write_fails(
    tmp_path, monkeypatch, capsys
):
    from test_public_sessions import config as session_config

    import rsys.application.batch as batch_module

    real_write_json = batch_module.write_json

    def flaky_write_json(path, value):
        if path.name == "manifest.json":
            raise OSError("simulated disk full on manifest.json write")
        return real_write_json(path, value)

    monkeypatch.setattr(batch_module, "write_json", flaky_write_json)

    cfg = session_config("trade")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    out_dir = tmp_path / "out"

    exit_code = cli_main(["run", "--config", str(config_path), "--out", str(out_dir)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OTHER_FAILURE
    assert "run_id:" in captured.err
    assert "trace:" in captured.err
    assert "manifest:" in captured.err

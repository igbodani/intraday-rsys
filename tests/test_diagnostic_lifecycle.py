"""Public failure-matrix checks for BR-01; provider requests are always fake."""

import json
import multiprocessing
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from rsys.application import batch, historical, run
from rsys.cli import main
from rsys.research import diagnostics, storage
from rsys.research.diagnostics import (
    DiagnosticWriter,
    read_diagnostic_journal,
    start_invocation,
)
from rsys.research.errors import DiagnosticWriteError
from rsys.research.experiments import SecretRedactor
from rsys.research.models import DiagnosticEventCode as Code

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'test-only-secret:"quoted"\nsecond-line'


def events(root):
    path = next((root / "invocations").glob("*/diagnostics.jsonl"))
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def failure(message="injected disk error"):
    def fail(*args, **kwargs):
        raise OSError(message)

    return fail


@pytest.mark.parametrize("mode", ["legacy", "batch"])
@pytest.mark.parametrize("point", ["record_identity", "link_run", "storage"])
def test_early_public_failure_is_recorded_once(
    tmp_path, monkeypatch, capsys, mode, point
):
    if point == "storage":
        monkeypatch.setattr(
            storage if mode == "legacy" else batch, "RunStorage", failure()
        )
    else:
        monkeypatch.setattr(DiagnosticWriter, point, failure())
    fixture = (
        "standalone_run_fixture.json"
        if mode == "legacy"
        else "sessions_bar_fixture.json"
    )
    assert (
        main(
            [
                "run",
                "--config",
                str(ROOT / "fixtures" / fixture),
                "--out",
                str(tmp_path),
            ]
        )
        != 0
    )
    records = events(tmp_path)
    assert sum(r["event"] == "failure" for r in records) == 1
    assert records[-1]["event"] == "failure"
    assert "invocation:" in capsys.readouterr().err
    if point == "link_run":
        db = storage.RunStorage(tmp_path)
        try:
            assert [db.load_record(i).status for i in db.list_run_ids()] == ["failed"]
        finally:
            db.close()


def test_legacy_report_exists_before_readiness(tmp_path, monkeypatch):
    original = DiagnosticWriter.artifacts_ready

    def check(writer, **kwargs):
        assert (tmp_path / "attempts" / writer.linked_run_id / "report.html").is_file()
        return original(writer, **kwargs)

    monkeypatch.setattr(DiagnosticWriter, "artifacts_ready", check)
    run.run_cli_entry(
        ROOT / "fixtures/standalone_run_fixture.json", tmp_path, report=True
    )


@pytest.mark.parametrize("point", [Code.DATASET_COMPLETE, Code.ARTIFACTS_READY])
def test_download_never_commits_success_after_journal_failure(
    tmp_path, monkeypatch, point
):
    from test_alpaca_pipeline import cached

    config, _ = cached(tmp_path / "cache_setup")
    original = DiagnosticWriter.event

    def fail_event(writer, code, *args, **kwargs):
        if code == point:
            raise DiagnosticWriteError("injected journal failure")
        return original(writer, code, *args, **kwargs)

    monkeypatch.setattr(DiagnosticWriter, "event", fail_event)
    out = tmp_path / "out"
    with pytest.raises(DiagnosticWriteError):
        historical.download(config, out)
    manifest = json.loads(next((out / "downloads").glob("*/manifest.json")).read_text())
    assert manifest["status"] != "success"
    assert events(out)[-1]["event"] == "failure"


def test_download_collision_preserves_existing_evidence(tmp_path, monkeypatch):
    attempt = tmp_path / "downloads/existing"
    attempt.mkdir(parents=True)
    manifest = attempt / "manifest.json"
    previous = b'{"status":"success","owner":"previous-attempt"}'
    manifest.write_bytes(previous)
    monkeypatch.setattr(historical, "uuid4", lambda: SimpleNamespace(hex="existing"))
    with pytest.raises(FileExistsError):
        historical.download({}, tmp_path)
    assert manifest.read_bytes() == previous
    assert events(tmp_path)[-1]["event"] == "failure"


def test_short_write_stops_writer_without_corrupting_the_next_record(tmp_path):
    writer = start_invocation(tmp_path, command="run")
    prefix = writer.journal_path.read_bytes()

    class ShortFile:
        def __init__(self, path):
            self.file = path.open("ab")

        def write(self, data):
            return self.file.write(data[: len(data) // 2])

        def __getattr__(self, name):
            return getattr(self.file, name)

    writer._open_append = ShortFile
    with pytest.raises(DiagnosticWriteError):
        writer.event(Code.CONFIG_LOADED, "preparing")
    torn = writer.journal_path.read_bytes()
    assert torn.startswith(prefix)
    writer._open_append = lambda path: path.open("ab")
    writer.fail(ValueError("original error"))
    assert writer.journal_path.read_bytes() == torn
    assert json.loads(writer.invocation_path.read_text())["stage"] == "failed"


def test_known_secret_is_scrubbed_from_arbitrary_event_values(tmp_path):
    writer = start_invocation(
        tmp_path, command="run", redactor=SecretRedactor([SECRET])
    )
    writer.event(
        Code.PREFLIGHT_CHECK, "preparing", details={"note": ["error: " + SECRET]}
    )
    assert events(tmp_path)[-1]["details"]["note"] == ["error: REDACTED"]


def test_unknown_free_text_is_withheld(tmp_path):
    writer = start_invocation(tmp_path, command="run")
    writer.event(Code.PREFLIGHT_CHECK, "preparing", details={"message": SECRET})
    assert "withheld" in events(tmp_path)[-1]["details"]["message"]


def test_invalid_legacy_config_records_failure_before_sealing(tmp_path, capsys):
    request = tmp_path / "invalid.json"
    request.write_text('{"schema_version":1}')
    out = tmp_path / "out"
    assert main(["run", "--config", str(request), "--out", str(out)]) != 0
    assert "ok:" not in capsys.readouterr().out
    records = events(out)
    assert sum(r["event"] == "failure" for r in records) == 1
    assert records[-1]["event"] == "failure"
    assert records[-1]["details"]["error_type"] == "RunConfigError"
    state = json.loads(
        next((out / "invocations").glob("*/invocation.json")).read_text()
    )
    assert state["stage"] == "failed" and state["journal_sha256"]


def test_provider_exception_never_leaks_into_cli_or_download_manifest(
    tmp_path, monkeypatch, capsys
):
    from test_alpaca_pipeline import inputs

    config, _ = inputs(tmp_path)
    config_path = tmp_path / "request.json"
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(historical.AlpacaBarsClient, "pages", failure(SECRET))
    out = tmp_path / "out"
    assert main(["download", "--config", str(config_path), "--out", str(out)]) != 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    manifest = json.loads(next((out / "downloads").glob("*/manifest.json")).read_text())
    assert SECRET not in manifest["error"]["message"]
    assert "OSError" in captured.err


@pytest.mark.parametrize("point", ["write", "flush", "fsync", "close"])
def test_writer_io_failure_preserves_prefix_and_stops_future_appends(
    tmp_path, monkeypatch, point
):
    writer = start_invocation(tmp_path, command="run")
    prefix = writer.journal_path.read_bytes()

    class FailingFile:
        def __init__(self, path):
            self.file = path.open("ab")

        def write(self, data):
            if point == "write":
                raise OSError("write broke")
            return self.file.write(data)

        def flush(self):
            self.file.flush()
            if point == "flush":
                raise OSError("flush broke")

        def close(self):
            self.file.close()
            if point == "close":
                raise OSError("close broke")

        def fileno(self):
            return self.file.fileno()

    writer._open_append = FailingFile
    if point == "fsync":
        monkeypatch.setattr(diagnostics.os, "fsync", failure("sync broke"))
    with pytest.raises(DiagnosticWriteError):
        writer.event(Code.SESSION_END, "resolved")
    retained = writer.journal_path.read_bytes()
    assert retained.startswith(prefix)
    with pytest.raises(DiagnosticWriteError):
        writer.event(Code.CONFIG_LOADED, "preparing")
    assert writer.journal_path.read_bytes() == retained
    # A complete prefix is inspectable, but there is no terminal seal.
    assert read_diagnostic_journal(writer.journal_path).record_count >= 1
    assert json.loads(writer.invocation_path.read_text())["journal_sha256"] is None


def test_backtest_preserves_preparation_error_when_diagnostics_also_fail(
    tmp_path, monkeypatch
):
    writer = start_invocation(tmp_path, command="backtest")
    writer._open_append = failure("secondary write error")
    primary = RuntimeError("primary preparation error")

    def fail_prepare(*args):
        raise primary

    monkeypatch.setattr(historical, "prepare_backtest", fail_prepare)
    # Exercise ownership/cleanup as well as the nested batch path.
    monkeypatch.setattr(diagnostics, "start_invocation", lambda *a, **k: writer)
    with pytest.raises(RuntimeError) as caught:
        historical.backtest({}, b"{}", tmp_path)
    assert caught.value is primary
    assert isinstance(primary.diagnostic_write_error, DiagnosticWriteError)


def test_failed_final_download_commit_never_publishes_success(tmp_path, monkeypatch):
    from test_alpaca_pipeline import cached

    config, _ = cached(tmp_path / "cached")
    original = historical.write_json

    def fail_success(path, value):
        if value.get("status") == "success":
            raise OSError("final commit failed")
        return original(path, value)

    monkeypatch.setattr(historical, "write_json", fail_success)
    out = tmp_path / "out"
    with pytest.raises(OSError, match="final commit failed"):
        historical.download(config, out)
    manifest = json.loads(next((out / "downloads").glob("*/manifest.json")).read_text())
    assert manifest["status"] == "failed"
    assert events(out)[-1]["event"] == "artifacts_ready"
    # Readiness precedes the authoritative commit; no post-seal journal append.
    diagnostics.verify_diagnostic_seal(out, manifest["diagnostics"])
    state = json.loads(
        next((out / "invocations").glob("*/invocation.json")).read_text()
    )
    assert state["stage"] == "failed"


def test_broken_console_does_not_rewrite_successful_download(tmp_path, monkeypatch):
    from test_alpaca_pipeline import cached

    config, _ = cached(tmp_path / "cached")
    request = tmp_path / "request.json"
    request.write_text(json.dumps(config))
    out = tmp_path / "out"

    class BrokenConsole:
        def write(self, text):
            raise BrokenPipeError("console closed")

    monkeypatch.setattr(sys, "stdout", BrokenConsole())
    with pytest.raises(BrokenPipeError):
        main(["download", "--config", str(request), "--out", str(out)])
    manifest = json.loads(next((out / "downloads").glob("*/manifest.json")).read_text())
    assert manifest["status"] == "success"
    diagnostics.verify_diagnostic_seal(out, manifest["diagnostics"])
    state = json.loads(
        next((out / "invocations").glob("*/invocation.json")).read_text()
    )
    assert state["stage"] == "finalizing"
    assert events(out)[-1]["event"] == "artifacts_ready"


@pytest.mark.parametrize("mode", ["legacy", "batch"])
def test_required_report_failure_is_not_comparable_success(
    tmp_path, monkeypatch, capsys, mode
):
    from rsys.reporting import html

    if mode == "legacy":
        monkeypatch.setattr(html, "render_report_for_attempt", failure("report failed"))
    else:
        monkeypatch.setattr(html, "render_report", failure("report failed"))
    fixture = (
        "standalone_run_fixture.json"
        if mode == "legacy"
        else "sessions_bar_fixture.json"
    )
    assert (
        main(
            [
                "run",
                "--config",
                str(ROOT / "fixtures" / fixture),
                "--out",
                str(tmp_path),
                "--report",
            ]
        )
        != 0
    )
    assert "ok:" not in capsys.readouterr().out
    assert events(tmp_path)[-1]["event"] == "failure"
    db = storage.RunStorage(tmp_path)
    try:
        assert [db.load_record(i).status for i in db.list_run_ids()] == ["failed"]
    finally:
        db.close()


def _paused_public_backtest(request, out, ready, release):
    original = historical.BarCache.load
    paused = False

    def blocked_load(cache, *args, **kwargs):
        nonlocal paused
        if not paused:
            paused = True
            ready.put(True)
            if not release.wait(30):
                raise RuntimeError("test barrier timed out")
        return original(cache, *args, **kwargs)

    historical.BarCache.load = blocked_load
    raise SystemExit(main(["backtest", "--config", request, "--out", out]))


@pytest.mark.parametrize("terminate", [False, True])
def test_public_preflight_is_visible_before_release_or_process_kill(
    tmp_path, terminate
):
    from test_alpaca_pipeline import cached

    _, config = cached(tmp_path / "cached")
    request = tmp_path / "request.json"
    request.write_text(json.dumps(config))
    out = tmp_path / "out"
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Queue(), ctx.Event()
    child = ctx.Process(
        target=_paused_public_backtest, args=(str(request), str(out), ready, release)
    )
    child.start()
    try:
        assert ready.get(timeout=30)
        assert child.is_alive()
        state_path = next((out / "invocations").glob("*/invocation.json"))
        state = json.loads(state_path.read_text())
        assert state["input_sha256"] and state["linked_run_id"] is None
        journal = state_path.with_name("diagnostics.jsonl")
        prefix = journal.read_bytes()
        assert read_diagnostic_journal(journal).record_count >= 1
        if terminate:
            child.kill()
        else:
            release.set()
        child.join(30)
        assert not child.is_alive()
        if terminate:
            assert child.exitcode != 0
            assert journal.read_bytes() == prefix
            assert json.loads(state_path.read_text())["ended_at"] is None
            assert not (out / "runs.db").exists()
        else:
            assert child.exitcode == 0
            assert json.loads(state_path.read_text())["linked_run_id"]
    finally:
        if child.is_alive():
            child.kill()
        child.join(5)
        ready.close()

"""BR-02 public streaming, interruption, integrity and observer-isolation contracts."""

import hashlib
import json
import multiprocessing
import socket
from copy import deepcopy
from pathlib import Path

import pytest

from rsys.application.batch import SessionBatch
from rsys.application.builders import build_runner
from rsys.application.historical import backtest, prepare_backtest
from rsys.application.run import run_cli_entry
from rsys.cli import main
from rsys.reporting.errors import ReportError
from rsys.reporting.html import load_attempt
from rsys.research import storage as storage_module
from rsys.research import trace as trace_module
from rsys.research.comparison import load_run_manifest
from rsys.research.diagnostics import DiagnosticWriter
from rsys.research.errors import (
    CompareCliError,
    DiagnosticIntegrityError,
    TraceWriteError,
)
from rsys.research.experiments import start_run
from rsys.research.models import DiagnosticEventCode
from rsys.research.storage import RunStorage
from rsys.research.trace import TraceWriter, read_trace

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = {
    "legacy": "standalone_run_fixture.json",
    "bar": "sessions_bar_fixture.json",
    "trade": "sessions_trade_fixture.json",
}
BASELINE = {
    "legacy": (
        "9880",
        "d67086832d008a2a69e9a9dafcb50ae0322e699c6526e9f3144adfba85b31cbc",
    ),
    "bar": (
        "1022.34800",
        "c029c04459d1bf9fbd6219525f482b3b14577d327bf0dfc3a2e121ec09245529",
    ),
    "trade": (
        "1034.14420",
        "767ca26db5c156389e98c264312eb2c1b6282cde29ebb3d7039b89a5bb117fb8",
    ),
}


def request_for(mode, root):
    root.mkdir(parents=True, exist_ok=True)
    if mode == "historical":
        from test_alpaca_pipeline import cached

        _, config = cached(root / "cache_setup")
    else:
        config = json.loads((ROOT / "fixtures" / FIXTURES[mode]).read_text())
    path = root / "request.json"
    path.write_text(json.dumps(config))
    return config, path


def execute(mode, config, request, out):
    return (
        backtest(config, request.read_bytes(), out, report=True)
        if mode == "historical"
        else run_cli_entry(request, out, report=True)
    )


def read_status(out, run_id):
    db = RunStorage(out)
    try:
        return db.load_record(run_id).status
    finally:
        db.close()


@pytest.mark.parametrize("mode", ["legacy", "bar", "trade", "historical"])
def test_public_stream_matches_unobserved_execution_and_original_money(
    tmp_path, monkeypatch, mode
):
    config, request = request_for(mode, tmp_path / "input")
    if mode == "legacy":
        runner, bars, scanner = build_runner(config)
        runner.run_session(bars, scanner)
        expected = runner.trace
    else:
        standalone = SessionBatch(
            prepare_backtest(config, []) if mode == "historical" else config,
            allow_historical=mode == "historical",
        )
        standalone.run()
        expected = standalone.trace
    original, observed = TraceWriter.write, []

    def observe(writer, row):
        original(writer, row)
        # Evidence must exist during execution, not be reconstructed afterward.
        assert (writer.path.parent / "input.json").is_file()
        assert (writer.path.parent / "resolved_config.json").is_file()
        assert not writer.path.with_name("manifest.json").exists()
        observed.append(deepcopy(row))

    monkeypatch.setattr(TraceWriter, "write", observe)
    out = tmp_path / "out"
    summary, run_id, trace, manifest_path = execute(mode, config, request, out)
    raw = trace.read_bytes()
    assert raw == b"".join(
        (json.dumps(row, sort_keys=True, default=str) + "\n").encode()
        for row in expected
    )
    assert observed == expected
    assert sum(row["kind"] == "fill" for row in observed) == len(summary["fills"])
    assert json.loads((trace.parent / "input.json").read_text()) == config
    manifest = load_run_manifest(out, run_id)
    assert manifest["trace_complete"] and manifest["trace_records"] == len(observed)
    assert manifest == json.loads(manifest_path.read_text())
    assert read_status(out, run_id) == "success"
    assert load_attempt(trace.parent)[1] == json.loads(
        json.dumps(observed, default=str)
    )
    if mode in BASELINE:
        equity, digest = BASELINE[mode]
        assert str(summary["ending_equity"]) == equity
        assert hashlib.sha256(raw).hexdigest() == digest


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_callback_mutation_and_operational_clock_do_not_change_decisions(
    tmp_path, monkeypatch, mode
):
    config, request = request_for(mode, tmp_path / "input")
    baseline = SessionBatch(config)
    baseline.run()
    observed = SessionBatch(config)
    delivered = []

    def hostile(row):
        delivered.append(deepcopy(row))
        row["account"]["positions"].clear()
        row["account"]["total_cash"] = "99999999"
        row.setdefault("accepted", []).append("INVENTED")
        row.clear()

    observed.run(event_sink=hostile)
    assert observed.summary() == baseline.summary()
    assert observed.trace == delivered == baseline.trace

    # Change only operational time and slow each observation via deterministic
    # extra work. No sleep or simulation-clock substitution is involved.
    monkeypatch.setattr(
        "rsys.research.diagnostics._default_clock", lambda: "2099-01-01T00:00:00+00:00"
    )
    original = TraceWriter.write

    def slow(writer, row):
        hashlib.pbkdf2_hmac("sha256", b"observer", b"test", 100)
        original(writer, row)

    monkeypatch.setattr(TraceWriter, "write", slow)
    _, _, trace, _ = execute(mode, config, request, tmp_path / "out")
    assert hashlib.sha256(trace.read_bytes()).hexdigest() == BASELINE[mode][1]


@pytest.mark.parametrize("mode", ["legacy", "bar", "trade", "historical"])
def test_torn_fill_write_retains_prefix_and_known_account_effects(
    tmp_path, monkeypatch, mode
):
    config, request = request_for(mode, tmp_path / "input")
    original, torn = TraceWriter.write, []

    def fail_first_fill(writer, row):
        if row["kind"] == "fill" and row["status"] == "FILLED":
            writer._handle.write(b'{"kind": "fill"')
            writer._handle.flush()
            torn.append(writer.path.read_bytes())
            writer._failed(OSError("simulated partial fill write"))
        original(writer, row)

    monkeypatch.setattr(TraceWriter, "write", fail_first_fill)
    out = tmp_path / "out"
    with pytest.raises(TraceWriteError) as error:
        execute(mode, config, request, out)
    trace = error.value.trace_path
    assert torn and trace.read_bytes() == torn[0]  # no finalizer repair/rewrite
    state = read_trace(trace)
    assert state.incomplete_tail and state.record_count > 0
    assert state.records[-1]["account"]["positions"]  # entry was already applied
    fills = json.loads((trace.parent / "fills.json").read_text())
    assert any(fill["status"] == "FILLED" for fill in fills)
    manifest = json.loads(error.value.manifest_path.read_text())
    assert manifest["status"] == "failed" and not manifest["trace_complete"]
    assert read_status(out, error.value.run_id) == "failed"
    with pytest.raises(CompareCliError):
        load_run_manifest(out, error.value.run_id)
    with pytest.raises(ReportError):
        load_attempt(trace.parent)


def _paused_public_run(mode, request, out, connection, boundary):
    original_write, original_event = TraceWriter.write, DiagnosticWriter.event
    paused = False

    def pause(path):
        nonlocal paused
        if not paused:
            paused = True
            connection.send(str(path))
            if not connection.poll(30):
                raise RuntimeError("test barrier timed out")
            connection.recv()

    def write(writer, row):
        original_write(writer, row)
        if boundary == "fill" and row["kind"] == "fill" and row["status"] == "FILLED":
            pause(writer.path)

    def event(writer, code, *args, **kwargs):
        original_event(writer, code, *args, **kwargs)
        if boundary == "session" and code == DiagnosticEventCode.SESSION_END:
            pause(Path(out) / "attempts" / writer.linked_run_id / "trace.jsonl")

    TraceWriter.write, DiagnosticWriter.event = write, event
    raise SystemExit(
        main(
            [
                "backtest" if mode == "historical" else "run",
                "--config",
                request,
                "--out",
                out,
                "--report",
            ]
        )
    )


def start_paused(ctx, mode, request, out, boundary="fill"):
    parent, child_pipe = ctx.Pipe()
    child = ctx.Process(
        target=_paused_public_run,
        args=(mode, str(request), str(out), child_pipe, boundary),
    )
    child.start()
    child_pipe.close()
    try:
        assert parent.poll(30), "public run did not reach the barrier"
        return child, parent, Path(parent.recv())
    except BaseException:
        if child.is_alive():
            child.kill()
        child.join(5)
        parent.close()
        raise


@pytest.mark.parametrize("mode", ["legacy", "bar", "trade", "historical"])
def test_real_process_kill_preserves_acknowledged_fill_and_only_recovers_dead_owner(
    tmp_path, mode
):
    _, request = request_for(mode, tmp_path / "input")
    ctx, out = multiprocessing.get_context("spawn"), tmp_path / "out"
    dead, dead_pipe, dead_trace = start_paused(ctx, mode, request, out)
    live = live_pipe = None
    try:
        live, live_pipe, live_trace = start_paused(ctx, mode, request, out)
        prefix = dead_trace.read_bytes()
        assert read_trace(dead_trace).records[-1]["kind"] == "fill"
        assert not dead_trace.with_name("manifest.json").exists()
        assert (dead_trace.parent / "input.json").is_file()
        assert (dead_trace.parent / "resolved_config.json").is_file()
        assert dead.is_alive() and live.is_alive()
        dead.kill()
        dead.join(10)
        assert not dead.is_alive() and dead.exitcode != 0
        db = RunStorage(out)
        try:
            assert db.recover_crashed_runs() == [dead_trace.parent.name]
            assert db.load_record(dead_trace.parent.name).status == "crashed"
            assert db.load_record(live_trace.parent.name).status == "running"
            assert db.recover_crashed_runs() == []
        finally:
            db.close()
        assert dead_trace.read_bytes() == prefix
        with pytest.raises(CompareCliError):
            load_run_manifest(out, dead_trace.parent.name)
        with pytest.raises(ReportError):
            load_attempt(dead_trace.parent)
        assert dead_trace.read_bytes() == prefix
        live_pipe.send("continue")
        live.join(30)
        assert not live.is_alive() and live.exitcode == 0
        assert read_status(out, live_trace.parent.name) == "success"
    finally:
        for process, connection in ((dead, dead_pipe), (live, live_pipe)):
            if process is not None:
                if process.is_alive():
                    process.kill()
                process.join(5)
            if connection is not None:
                connection.close()


def test_session_checkpoint_exists_before_next_session_starts(tmp_path):
    _, request = request_for("bar", tmp_path / "input")
    ctx, out = multiprocessing.get_context("spawn"), tmp_path / "out"
    child, pipe, trace = start_paused(ctx, "bar", request, out, "session")
    try:
        session = json.loads((trace.parent / "sessions/2024-11-29.json").read_text())
        assert session["status"] == "success" and len(session["fills"]) == 2
        assert not (trace.parent / "sessions/2024-12-02.json").exists()
        assert {r["session_date"] for r in read_trace(trace).records} == {"2024-11-29"}
    finally:
        child.kill()
        child.join(5)
        pipe.close()


@pytest.mark.parametrize(
    "host,pid",
    [
        (None, None),
        (None, 999999999),
        ("a-remote-host", 999999999),
        (socket.gethostname(), None),
    ],
)
def test_unknown_or_remote_owner_is_not_recovered(tmp_path, monkeypatch, host, pid):
    db = RunStorage(tmp_path)
    try:
        record = start_run(
            db,
            hypothesis="fixture",
            phase="run",
            resolved_config={},
            data_not_applicable_reason="owner test",
        )
        record.status, record.owner_host, record.owner_pid = "running", host, pid
        db.write_record(record)

        def forbidden(_):
            pytest.fail("unproven local ownership must not use local PID liveness")

        monkeypatch.setattr(storage_module, "_pid_is_alive", forbidden)
        assert db.recover_crashed_runs() == []
        assert db.load_record(record.run_id).status == "running"
    finally:
        db.close()


@pytest.mark.parametrize(
    "artifact",
    ["trace.jsonl", "input.json", "resolved_config.json", "diagnostics.jsonl"],
)
def test_public_readers_reject_tampering_without_repair(tmp_path, artifact):
    config, request = request_for("bar", tmp_path / "input")
    out = tmp_path / "out"
    _, run_id, trace, _ = execute("bar", config, request, out)
    path = (
        next((out / "invocations").glob("*/diagnostics.jsonl"))
        if artifact == "diagnostics.jsonl"
        else trace.with_name(artifact)
    )
    changed = path.read_bytes() + b" "
    path.write_bytes(changed)
    with pytest.raises(CompareCliError):
        load_run_manifest(out, run_id)
    with pytest.raises(ReportError):
        load_attempt(trace.parent)
    assert path.read_bytes() == changed


def test_reader_distinguishes_unterminated_tail_from_corrupt_complete_middle(tmp_path):
    config, request = request_for("legacy", tmp_path / "input")
    _, _, trace, _ = execute("legacy", config, request, tmp_path / "out")
    complete = trace.read_bytes()
    trace.write_bytes(complete[:-1])
    assert read_trace(trace).incomplete_tail  # even a parseable last JSON object
    assert read_trace(trace).record_count == len(complete.splitlines()) - 1
    trace.write_bytes(complete.splitlines(keepends=True)[0] + b"bad-row\n" + complete)
    before = trace.read_bytes()
    with pytest.raises(DiagnosticIntegrityError):
        read_trace(trace)
    assert trace.read_bytes() == before


@pytest.mark.parametrize("mode", ["legacy", "bar"])
def test_success_manifest_before_failed_terminal_commit_is_not_reportable(
    tmp_path, monkeypatch, mode
):
    config, request = request_for(mode, tmp_path / "input")
    original = RunStorage.write_record

    def fail_success(db, record):
        if record.status == "success":
            raise OSError("terminal commit failed")
        return original(db, record)

    monkeypatch.setattr(RunStorage, "write_record", fail_success)
    out = tmp_path / "out"
    with pytest.raises(OSError, match="terminal commit failed"):
        execute(mode, config, request, out)
    manifest = next((out / "attempts").glob("*/manifest.json"))
    assert json.loads(manifest.read_text())["status"] == "success"
    assert read_status(out, manifest.parent.name) == "running"
    with pytest.raises(ReportError):
        load_attempt(manifest.parent)
    with pytest.raises(CompareCliError):
        load_run_manifest(out, manifest.parent.name)


@pytest.mark.parametrize("mode", ["legacy", "bar", "trade"])
def test_failed_stream_close_is_not_committed_as_success(tmp_path, monkeypatch, mode):
    config, request = request_for(mode, tmp_path / "input")
    original = TraceWriter.close

    def fail_close(writer):
        original(writer)
        raise TraceWriteError("closing trace failed")

    monkeypatch.setattr(TraceWriter, "close", fail_close)
    out = tmp_path / "out"
    with pytest.raises(TraceWriteError) as error:
        execute(mode, config, request, out)
    assert read_status(out, error.value.run_id) == "failed"
    assert read_trace(error.value.trace_path).record_count > 0
    manifest = json.loads(error.value.manifest_path.read_text())
    assert manifest["status"] == "failed" and not manifest["trace_complete"]


@pytest.mark.parametrize("mode", ["legacy", "bar", "trade"])
@pytest.mark.parametrize("mutation", ["missing", "tampered"])
def test_failed_report_also_requires_sealed_diagnostics(
    tmp_path, monkeypatch, mode, mutation
):
    config, request = request_for(mode, tmp_path / "input")
    original = TraceWriter.write

    def fail_after_complete_row(writer, row):
        original(writer, row)
        raise TraceWriteError("failed after acknowledged row")

    monkeypatch.setattr(TraceWriter, "write", fail_after_complete_row)
    out = tmp_path / "out"
    with pytest.raises(TraceWriteError) as error:
        execute(mode, config, request, out)
    attempt = error.value.trace_path.parent
    assert load_attempt(attempt)[0]["status"] == "failed"
    journal = next((out / "invocations").glob("*/diagnostics.jsonl"))
    if mutation == "missing":
        journal.unlink()
    else:
        journal.write_bytes(journal.read_bytes() + b"{}\n")
    with pytest.raises(ReportError, match="integrity"):
        load_attempt(attempt)


@pytest.mark.parametrize("point", ["write", "short", "flush", "fsync", "close"])
def test_writer_errors_stop_writes_and_preserve_old_prefix(
    tmp_path, monkeypatch, point
):
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    row = {
        "kind": "event",
        "event_id": "fixture",
        "available_at_ns": 1,
        "phase": 1,
        "account": {},
    }
    writer.write(row)
    before = path.read_bytes()
    real = writer._handle

    class Broken:
        def write(self, value):
            if point == "write":
                raise OSError("write failed")
            return real.write(value[:4] if point == "short" else value)

        def flush(self):
            real.flush()
            if point == "flush":
                raise OSError("flush failed")

        def close(self):
            real.close()
            if point == "close":
                raise OSError("close failed")

        def fileno(self):
            return real.fileno()

    writer._handle = Broken()
    if point == "fsync":
        monkeypatch.setattr(
            trace_module.os,
            "fsync",
            lambda _: (_ for _ in ()).throw(OSError("sync failed")),
        )
    with pytest.raises(TraceWriteError):
        writer.close() if point == "close" else writer.write({**row, "kind": "fill"})
    with pytest.raises(TraceWriteError):
        writer.close()
    retained = path.read_bytes()
    assert retained.startswith(before)
    with pytest.raises(TraceWriteError):
        writer.write(row)
    assert path.read_bytes() == retained


@pytest.mark.parametrize("mode", ["bar", "trade"])
def test_streamed_history_future_prefix_and_effective_negative_control(tmp_path, mode):
    from test_public_history_scanner import build_fixture

    config = build_fixture(mode)
    cutoff = 9 * 3_600_000 + 47 * 60_000 + 30_000

    def prefix(value, label):
        request = tmp_path / f"{label}.json"
        request.write_text(json.dumps(value))
        _, _, trace, _ = run_cli_entry(request, tmp_path / label)
        return [
            r
            for r in read_trace(trace).records
            if r["session_date"] == "2024-11-29" and r["available_at_ms"] <= cutoff
        ]

    expected = prefix(config, "baseline")
    assert any(r["kind"] == "fill" and r["status"] == "FILLED" for r in expected)
    future = deepcopy(config)
    revision = deepcopy(future["scanner"]["history"]["AAA"][0])
    revision.update(
        revision=1, volume="999999", available_at="2024-11-29T15:17:01+00:00"
    )
    future["scanner"]["history"]["AAA"].append(revision)
    for spec in future["sessions"][1]["symbols"].values():
        for source in spec["bars" if mode == "bar" else "trades"]:
            source["volume" if mode == "bar" else "size"] = "700"
    assert prefix(future, "future") == expected
    earlier = deepcopy(config)
    for row in earlier["scanner"]["history"]["AAA"]:
        if row["session_date"] == "2024-11-27":
            row["volume"] = "400"
    changed = prefix(earlier, "earlier")
    assert changed != expected
    assert (
        next(
            r["symbol"]
            for r in changed
            if r["kind"] == "fill" and r["status"] == "FILLED"
        )
        == "BBB"
    )

"""BR-03: public progress, read-only inspection, truthful counts and safe recovery."""

import hashlib
import io
import json
import multiprocessing
import os
import queue
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from test_alpaca_pipeline import cached
from test_public_history_scanner import build_fixture
from test_public_sessions import config, invoke
from test_streaming_run_evidence import (
    BASELINE,
    ROOT,
    execute,
    request_for,
    start_paused,
)

from rsys.application.diagnostics import ExecutionDiagnostics
from rsys.application.historical import download
from rsys.application.run import run_cli_entry
from rsys.cli import main
from rsys.cli.progress import ProgressReporter
from rsys.reporting.html import render_report
from rsys.research.comparison import load_run_manifest
from rsys.research.diagnostics import start_invocation
from rsys.research.errors import (
    CompareCliError,
    DiagnosticWriteError,
    RecoveryInProgressError,
)
from rsys.research.inspection import inspect_attempt, recover_attempts
from rsys.research.models import DiagnosticEventCode as Code
from rsys.research.models import DiagnosticRecord
from rsys.research.storage import RunStorage, recovery_lock


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "rsys.cli", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )


def tree_bytes(root):
    return {
        str(p.relative_to(root)): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in root.rglob("*")
        if p.is_file()
    }


class Console(io.StringIO):
    def __init__(self, tty=False):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


@pytest.mark.parametrize(
    "mode,tty,visible",
    [
        ("auto", False, False),
        ("auto", True, True),
        ("on", False, True),
        ("off", True, False),
    ],
)
def test_progress_modes_keep_stdout_and_money_unchanged(
    tmp_path, monkeypatch, capsys, mode, tty, visible
):
    _, request = request_for("legacy", tmp_path / "input")
    console = Console(tty)
    monkeypatch.setattr(sys, "stderr", console)
    out = tmp_path / "runs"
    assert (
        main(["run", "--config", str(request), "--out", str(out), "--progress", mode])
        == 0
    )
    stdout = capsys.readouterr().out
    assert stdout.startswith("ok: 18 decision(s), 2 fill(s)\n")
    assert "progress:" not in stdout and "ending_equity: 9880" in stdout
    assert bool(console.getvalue()) is visible
    (attempt,) = (out / "attempts").iterdir()
    assert (
        hashlib.sha256((attempt / "trace.jsonl").read_bytes()).hexdigest()
        == BASELINE["legacy"][1]
    )


def test_public_progress_is_readable_before_process_completes(tmp_path):
    _, request = request_for("legacy", tmp_path / "input")
    # Block after the real early event has been emitted, before config reading.
    code = """
import sys
from rsys.cli import main
from rsys.research.diagnostics import DiagnosticWriter
from rsys.research.models import DiagnosticEventCode
original = DiagnosticWriter.event
def paused(self, code, *a, **kw):
    original(self, code, *a, **kw)
    if code == DiagnosticEventCode.INVOCATION_START:
        if not sys.stdin.readline():
            raise RuntimeError('test barrier closed')
DiagnosticWriter.event = paused
raise SystemExit(main(sys.argv[1:]))
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            "run",
            "--config",
            str(request),
            "--out",
            str(tmp_path / "out"),
            "--progress",
            "on",
        ],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines = queue.Queue()
    reader = threading.Thread(
        target=lambda: lines.put(process.stderr.readline()), daemon=True
    )
    reader.start()
    try:
        line = lines.get(timeout=15)
        assert "progress: invocation=" in line and "stage=preparing" in line
        assert "sessions=0/unknown" in line and "%" not in line
        assert process.poll() is None
        stdout, stderr = process.communicate("continue\n", timeout=20)
        assert process.returncode == 0 and stdout.startswith("ok:")
        assert "event=success" in stderr
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
        reader.join(5)


def event(sequence=1, code=Code.CONFIG_LOADED, details=None):
    return DiagnosticRecord(
        invocation_id="test",
        sequence=sequence,
        wall_time="2024-01-01T00:00:00Z",
        event=code,
        stage="preparing",
        details=details or {},
    )


def test_progress_throttles_with_injected_clock_and_survives_a_broken_console():
    now = [0.0]
    console = Console()
    reporter = ProgressReporter("on", console, clock=lambda: now[0], interval=1)
    reporter(event())
    now[0] = 0.5
    reporter(event(2))
    assert len(console.getvalue().splitlines()) == 1
    now[0] = 1
    reporter(event(3))
    assert len(console.getvalue().splitlines()) == 2
    assert "elapsed=1.0s" in console.getvalue() and "%" not in console.getvalue()
    console.close()
    reporter.finish("success")  # closed stdout/stderr cannot change committed outcomes
    assert not reporter.enabled


def test_checkpoint_clock_throttles_evidence_without_sleeping(tmp_path):
    now = [0.0]
    writer = start_invocation(tmp_path, command="run")
    writer.use_config({})
    diagnostics = ExecutionDiagnostics(writer, ["2024-11-29"], clock=lambda: now[0])
    diagnostics.session("start", {"session_date": "2024-11-29", "status": "running"})
    sequence = writer.sequence
    diagnostics.checkpoint()
    now[0] = 0.99
    diagnostics.checkpoint()
    assert writer.sequence == sequence
    now[0] = 1
    diagnostics.checkpoint()
    assert writer.sequence == sequence + 1


@pytest.mark.parametrize("mode", ["legacy", "bar", "trade", "historical"])
def test_status_by_either_id_is_read_only_and_matches_authority(tmp_path, mode):
    cfg, request = request_for(mode, tmp_path / "input")
    out = tmp_path / "out"
    _, run_id, _, _ = execute(mode, cfg, request, out)
    (invocation,) = (out / "invocations").iterdir()
    before = tree_bytes(out)
    for identity in (run_id, invocation.name):
        result = cli("status", "--storage-root", out, "--id", identity, "--json")
        assert result.returncode == 0, result.stderr
        status = json.loads(result.stdout)
        assert status["attempt_id"] == run_id and status["run_id"] == run_id
        assert status["outcome"] == "success" and status["completeness"] == "complete"
        assert status["progress"]["entry_fills"] == (1 if mode == "legacy" else 2)
        assert status["last_observed_at"] and status["paths"]["trace"]
    preview = cli("recover", "--storage-root", out, "--dry-run", "--json")
    assert preview.returncode == 0
    assert len(json.loads(preview.stdout)["attempts"]) == 1  # no linked double-count
    assert tree_bytes(out) == before


def test_read_only_storage_rejects_writes_and_missing_status_creates_nothing(tmp_path):
    root = tmp_path / "missing"
    result = cli("status", "--storage-root", root, "--id", "absent", "--json")
    assert result.returncode == 1 and "not found" in result.stderr
    assert not root.exists()
    db = RunStorage(tmp_path)
    db.close()
    before = tree_bytes(tmp_path)
    db = RunStorage(tmp_path, read_only=True)
    try:
        with pytest.raises(__import__("sqlite3").OperationalError, match="readonly"):
            db._conn.execute("DELETE FROM runs")
    finally:
        db.close()
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize("mutation", ["manifest", "journal", "trace", "state", "input"])
def test_status_rejects_corrupt_sealed_evidence(tmp_path, mutation):
    cfg, request = request_for("legacy", tmp_path / "input")
    out = tmp_path / "out"
    _, run_id, trace, manifest = execute("legacy", cfg, request, out)
    (inv,) = (out / "invocations").iterdir()
    paths = {
        "manifest": manifest,
        "journal": inv / "diagnostics.jsonl",
        "trace": trace,
        "state": inv / "invocation.json",
        "input": trace.parent / "input.json",
    }
    path = paths[mutation]
    path.write_bytes(path.read_bytes() + b"garbage\n")
    before = tree_bytes(out)
    result = cli("status", "--storage-root", out, "--id", run_id, "--json")
    assert result.returncode == 1 and "error: cannot status:" in result.stderr
    assert not result.stdout and tree_bytes(out) == before


def preflight(root, *, host=None, pid=None):
    writer = start_invocation(root, command="backtest")
    state = json.loads(writer.invocation_path.read_text())
    state.update(
        owner_host=host or socket.gethostname(),
        owner_pid=pid if pid is not None else 999999999,
    )
    writer.invocation_path.write_text(json.dumps(state))
    return writer


def test_recovery_preflight_prefix_is_preserved_and_idempotent(tmp_path):
    writer = preflight(tmp_path)
    writer.journal_path.write_bytes(writer.journal_path.read_bytes() + b'{"torn":')
    original = tree_bytes(tmp_path)
    status = inspect_attempt(tmp_path, writer.invocation_id)
    assert status.owner_state == "dead" and status.progress["incomplete_tail"]
    actions = recover_attempts(tmp_path)
    assert (
        len(actions) == 1
        and actions[0].action == "mark_crashed"
        and not actions[0].applied
    )
    assert tree_bytes(tmp_path) == original
    applied = recover_attempts(tmp_path, writer.invocation_id, apply=True)
    assert applied[0].applied
    status = inspect_attempt(tmp_path, writer.invocation_id)
    assert status.outcome == "crashed" and status.completeness == "partial"
    assert not (tmp_path / "runs.db").exists()
    after = tree_bytes(tmp_path)
    for path, value in original.items():
        assert after[path] == value
    assert set(after) - set(original) == {
        ".recovery.guard",
        f"invocations/{writer.invocation_id}/recovery.json",
    }  # persistent OS guard plus separate receipt; no evidence rewrite
    assert not any(a.applied for a in recover_attempts(tmp_path, apply=True))
    assert tree_bytes(tmp_path) == after


@pytest.mark.parametrize(
    "host,pid,owner",
    [
        (None, os.getpid(), "alive"),
        ("another-host", 999999999, "unknown"),
        (None, 0, "unknown"),
        (None, 10**100, "unknown"),
    ],
)
def test_recovery_will_not_guess_that_live_remote_or_unknown_owners_died(
    tmp_path, host, pid, owner
):
    writer = preflight(tmp_path, host=host, pid=pid)
    before = tree_bytes(tmp_path)
    assert inspect_attempt(tmp_path, writer.invocation_id).owner_state == owner
    actions = recover_attempts(tmp_path, apply=True)
    assert not any(a.applied for a in actions)
    assert tree_bytes(tmp_path) == before


def test_recovery_respects_locks_and_requires_explicit_mode(tmp_path):
    preflight(tmp_path)
    lock = tmp_path / ".recovery.lock"
    lock.write_text(json.dumps({"host": "remote", "pid": 999999999}))
    (tmp_path / ".recovery.guard").touch()
    before = tree_bytes(tmp_path)
    assert (
        recover_attempts(tmp_path)[0].action == "mark_crashed"
    )  # preview is read-only
    with pytest.raises(RecoveryInProgressError):
        recover_attempts(tmp_path, apply=True)
    assert tree_bytes(tmp_path) == before
    for modes in ([], ["--apply", "--dry-run"]):
        result = cli("recover", "--storage-root", tmp_path, *modes)
        assert result.returncode == 2 and tree_bytes(tmp_path) == before


def test_public_recovery_only_changes_dead_process_while_other_run_stays_live(tmp_path):
    _, request = request_for("trade", tmp_path / "input")
    ctx, out = multiprocessing.get_context("spawn"), tmp_path / "out"
    dead, dead_pipe, dead_trace = start_paused(ctx, "trade", request, out)
    live = live_pipe = None
    try:
        live, live_pipe, live_trace = start_paused(ctx, "trade", request, out)
        live_status = inspect_attempt(out, live_trace.parent.name)
        assert live_status.outcome == "running" and live_status.owner_state == "alive"
        dead.kill()
        dead.join(10)
        before = tree_bytes(out)
        preview = cli("recover", "--storage-root", out, "--dry-run", "--json")
        assert preview.returncode == 0, preview.stderr
        actions = json.loads(preview.stdout)["attempts"]
        assert len(actions) == 2
        assert [a["attempt_id"] for a in actions if a["action"] == "mark_crashed"] == [
            dead_trace.parent.name
        ]
        assert tree_bytes(out) == before
        result = cli("recover", "--storage-root", out, "--apply", "--json")
        assert result.returncode == 0, result.stderr
        assert sum(a["applied"] for a in json.loads(result.stdout)["attempts"]) == 1
        assert inspect_attempt(out, dead_trace.parent.name).outcome == "crashed"
        assert inspect_attempt(out, live_trace.parent.name).outcome == "running"
        assert (
            tree_bytes(out)[str(dead_trace.relative_to(out))]
            == before[str(dead_trace.relative_to(out))]
        )
        with pytest.raises(CompareCliError):
            load_run_manifest(out, dead_trace.parent.name)
        live_pipe.send("continue")
        live.join(30)
        assert live.exitcode == 0
        assert inspect_attempt(out, live_trace.parent.name).outcome == "success"
    finally:
        for process, connection in ((dead, dead_pipe), (live, live_pipe)):
            if process is not None:
                if process.is_alive():
                    process.kill()
                process.join(5)
            if connection is not None:
                connection.close()


def test_download_status_uses_final_manifest_and_preserves_terminal_state(tmp_path):
    acquisition, _ = cached(tmp_path / "cache_setup")
    _, manifest = download(acquisition, tmp_path / "out")  # cache hit only
    (invocation,) = (tmp_path / "out" / "invocations").iterdir()
    before = tree_bytes(tmp_path / "out")
    status = inspect_attempt(tmp_path / "out", invocation.name)
    assert status.command == "download" and status.run_id is None
    assert status.outcome == "success" and status.completeness == "complete"
    assert status.paths["manifest"] == str(manifest)
    assert not any(a.applied for a in recover_attempts(tmp_path / "out", apply=True))
    assert tree_bytes(tmp_path / "out") == before


@pytest.mark.parametrize("mode,opportunities", [("bar", 20), ("trade", 19)])
def test_funnel_counts_calls_and_committed_fills_once(tmp_path, mode, opportunities):
    cfg = config(mode)
    # Add one early eligible opportunity while ORB is still collecting its range.
    for session in cfg["sessions"]:
        session["scanner_inputs"]["09:31:00"] = {"AAA": {"rvol": "2", "gain": ".03"}}
    result, attempt, manifest = invoke(tmp_path, cfg)
    assert result.returncode == 0, result.stderr
    for row in manifest["funnel"]["sessions"]:
        assert row["opportunities"] == opportunities
        assert row["scanner_eligible"] == 2
        assert (
            row["proposals"]
            == row["acceptances"]
            == row["entry_fills"]
            == row["exits"]
            == 1
        )
        assert row["reasons"]["strategy:NO_PROPOSAL"]["count"] == 1
    journal = next((tmp_path / "runs" / "invocations").glob("*/diagnostics.jsonl"))
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    for row in manifest["funnel"]["sessions"]:
        for reason in row["reasons"].values():
            for reference in reason["examples"]:
                event = records[reference["journal_sequence"] - 1]
                assert event["details"]["event_id"] == reference["event_id"]
                assert event["details"]["code"] == reason["code"]
    report = (attempt / "report.html").read_text()
    assert "Session funnel" in report and "internal reason is not available" in report


def test_zero_trade_status_is_success_with_actual_no_candidate_reason(tmp_path):
    cfg = config()
    for session in cfg["sessions"]:
        session["scanner_inputs"] = {}
    result, _, manifest = invoke(tmp_path, cfg)
    assert result.returncode == 0
    status = cli(
        "status",
        "--storage-root",
        tmp_path / "runs",
        "--id",
        manifest["run_id"],
        "--json",
    )
    assert status.returncode == 0
    assert json.loads(status.stdout)["outcome"] == "success"
    assert "zero entry fills" in json.loads(status.stdout)["explanation"]
    row = manifest["funnel"]["sessions"][0]
    assert row["proposals"] == row["entry_fills"] == 0
    assert row["reasons"]["scanner_batch:NO_SCANNER_CANDIDATE"]["count"] == 19
    assert (
        "strategy:NO_PROPOSAL" not in row["reasons"]
    )  # cannot invent a strategy cause


@pytest.mark.parametrize("case", ["missing", "zero", "stale"])
def test_history_reasons_distinguish_missing_zero_and_staleness(tmp_path, case):
    cfg = build_fixture("bar")
    cfg["sessions"] = cfg["sessions"][:1]
    expected = {
        "missing": "MISSING_SELECTED_HISTORY",
        "zero": "ZERO_DENOMINATOR",
        "stale": "STALE_CURRENT_BAR",
    }[case]
    if case == "missing":
        cfg["scanner"]["history"]["AAA"] = []
    elif case == "zero":
        for row in cfg["scanner"]["history"]["AAA"]:
            if row["minute_index"] < 30:
                row.update(status="NO_TRADE", close=None, volume="0")
    else:
        spec = cfg["sessions"][0]["symbols"]["AAA"]
        spec["bars"] = [b for b in spec["bars"] if "T09:30:" in b["bar_start"]]
    result, _, manifest = invoke(tmp_path, cfg)
    assert result.returncode == 0, result.stderr
    reasons = manifest["funnel"]["sessions"][0]["reasons"]
    assert reasons[f"history:{expected}"]["count"] > 0
    assert reasons[f"history:{expected}"]["examples"][0]["symbol"] == "AAA"


def test_insufficient_cash_is_an_acceptance_reason(tmp_path):
    cfg = config("bar")
    cfg["initial_cash"] = "50"
    cfg["risk"]["per_trade_fraction"] = (
        ".5"  # enough stop risk, but not one share of cash
    )
    result, _, manifest = invoke(tmp_path, cfg)
    assert result.returncode == 0, result.stderr
    row = manifest["funnel"]["sessions"][0]
    assert row["proposals"] == 1 and row["acceptances"] == row["entry_fills"] == 0
    assert row["reasons"]["acceptance:INSUFFICIENT_CASH"]["count"] == 1


@pytest.mark.parametrize(
    "halt,price", [("DAILY_LOSS_HALT", "95"), ("ACCOUNT_DRAWDOWN_HALT", "70")]
)
def test_actual_risk_halt_is_counted_once_per_session(tmp_path, halt, price):
    cfg = config("trade")
    cfg["costs"].update(adverse_bps="0", feed_delay_ms=0)
    for trade in cfg["sessions"][0]["symbols"]["AAA"]["trades"]:
        if trade["event_time"].split("T")[1] >= "09:47:00":
            trade["price"] = price
    result, _, manifest = invoke(tmp_path, cfg)
    assert result.returncode == 0, result.stderr
    row = manifest["funnel"]["sessions"][0]
    assert row["reasons"][f"risk:{halt}"]["count"] == 1
    assert row["entry_fills"] == row["exits"] == 1


def test_repeated_unusable_references_do_not_inflate_proposals_and_intent_expires(
    tmp_path,
):
    cfg = config("trade")
    for session in cfg["sessions"]:
        for trade in session["symbols"]["AAA"]["trades"]:
            if trade["event_time"].split("T")[1] >= "09:46:00":
                trade["conditions"] = ["LATE_REPORT"]
    result, _, manifest = invoke(tmp_path, cfg)
    assert result.returncode == 0, result.stderr
    row = manifest["funnel"]["sessions"][0]
    assert row["proposals"] == row["acceptances"] == 1
    assert row["entry_fills"] == row["exits"] == 0
    assert row["reasons"]["entry_reference:REJECTED_CONDITION"]["count"] > 1
    assert row["reasons"]["intent:EXPIRED"]["count"] == 1


def test_public_missing_exit_reference_remains_failed_partial_and_inspectable(tmp_path):
    cfg = config("bar")
    cfg["sessions"][0]["symbols"]["AAA"]["bars"] = cfg["sessions"][0]["symbols"]["AAA"][
        "bars"
    ][:-2]
    result, attempt, manifest = invoke(tmp_path, cfg)
    assert result.returncode == 3 and "incomplete:" in result.stderr
    assert [s["status"] for s in manifest["sessions"]] == ["failed", "not_started"]
    first, second = manifest["funnel"]["sessions"]
    assert first["reasons"]["completion:MISSING_EXIT_REFERENCE"]["count"] == 1
    assert first["entry_fills"] == 1 and first["exits"] == 0
    assert second["status"] == "not_started" and second["opportunities"] == 0
    assert (attempt / "input.json").is_file() and (attempt / "fills.json").is_file()
    report = (attempt / "report.html").read_text()
    assert "partial_attempt" in report and "MISSING_EXIT_REFERENCE" in report
    before = tree_bytes(tmp_path / "runs")
    result = cli(
        "status",
        "--storage-root",
        tmp_path / "runs",
        "--id",
        manifest["run_id"],
        "--json",
    )
    assert result.returncode == 0  # inspection success is not a successful simulation
    status = json.loads(result.stdout)
    assert status["outcome"] == "failed" and status["completeness"] == "partial"
    assert not any(a.applied for a in recover_attempts(tmp_path / "runs", apply=True))
    assert tree_bytes(tmp_path / "runs") == before


def test_report_escapes_every_new_reason_and_reference_field(tmp_path):
    cfg, request = request_for("legacy", tmp_path / "input")
    _, _, _, manifest_path = execute("legacy", cfg, request, tmp_path / "out")
    manifest = json.loads(manifest_path.read_text())
    payload = '<script>alert("unsafe")</script>'
    row = manifest["funnel"]["sessions"][0]
    row["session_date"] = payload
    row["reasons"] = {
        "x": {
            "stage": payload,
            "code": payload,
            "count": 1,
            "examples": [
                {"event_id": payload, "symbol": payload, "journal_sequence": payload}
            ],
        }
    }
    manifest["funnel"]["units"] = {payload: payload}
    report = render_report(manifest, [])
    assert payload not in report and "<script>" not in report
    assert "&lt;script&gt;" in report


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "failed", "error": "bad shape"},
        {"status": "success", "diagnostics": []},
    ],
)
def test_malformed_download_objects_have_a_useful_cli_error(tmp_path, payload):
    writer = preflight(tmp_path)
    target = tmp_path / "downloads" / "case" / "manifest.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(payload))
    writer.link_download_attempt(str(target))
    result = cli(
        "status", "--storage-root", tmp_path, "--id", writer.invocation_id, "--json"
    )
    assert result.returncode == 1 and "invalid download" in result.stderr
    assert "Traceback" not in result.stderr and not result.stdout


def test_os_guard_prevents_stale_lock_reclaim_race(tmp_path, monkeypatch):
    lock = tmp_path / ".recovery.lock"
    lock.write_text("999999999")
    original = Path.unlink
    intercepted = []

    def interleave(path, *args, **kwargs):
        if path == lock and not intercepted:
            intercepted.append(True)
            # This exact boundary let a second sweep reclaim the stale file
            # and enter before the first sweep deleted its new active lock.
            with (
                pytest.raises(RecoveryInProgressError, match="OS guard"),
                recovery_lock(tmp_path),
            ):
                pytest.fail("two recovery scopes entered concurrently")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interleave)
    with recovery_lock(tmp_path):
        assert intercepted
    inode = (tmp_path / ".recovery.guard").stat().st_ino
    with recovery_lock(tmp_path):
        assert (tmp_path / ".recovery.guard").stat().st_ino == inode


def _early_process(root, pipe, lock=False):
    if lock:
        with recovery_lock(root):
            pipe.send("locked")
            pipe.recv()
    else:
        writer = start_invocation(root, command="backtest")
        pipe.send(writer.invocation_id)
        pipe.recv()


@pytest.mark.parametrize("lock", [False, True])
def test_real_early_process_and_recovery_guard_survive_owner_kill(tmp_path, lock):
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_early_process, args=(str(tmp_path), child, lock))
    process.start()
    child.close()
    try:
        assert parent.poll(15)
        identity = parent.recv()
        if lock:
            with pytest.raises(RecoveryInProgressError), recovery_lock(tmp_path):
                pytest.fail("live owner must exclude recovery")
        else:
            assert inspect_attempt(tmp_path, identity).owner_state == "alive"
        process.kill()
        process.join(10)
        if lock:
            with recovery_lock(tmp_path):
                pass  # OS releases the guard; dead PID metadata can be reclaimed
        else:
            result = cli(
                "recover",
                "--storage-root",
                tmp_path,
                "--id",
                identity,
                "--apply",
                "--json",
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["attempts"][0]["applied"]
            assert inspect_attempt(tmp_path, identity).outcome == "crashed"
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
        parent.close()


@pytest.mark.parametrize("mode", ["legacy", "bar", "trade"])
@pytest.mark.parametrize("boundary", ["start", "filled"])
def test_poisoned_progress_journal_still_retains_known_financial_evidence(
    tmp_path, monkeypatch, mode, boundary
):
    _, request = request_for(mode, tmp_path / "input")
    out = tmp_path / "out"
    writer = start_invocation(out, command="run")
    original = writer._open_append
    checkpoint = ExecutionDiagnostics.checkpoint
    monkeypatch.setattr(
        ExecutionDiagnostics,
        "checkpoint",
        lambda self, **kw: checkpoint(self, force=True),
    )

    class Poison:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def write(self, line):
            row = json.loads(line)
            if row["event"] == "progress" and (
                boundary == "start" or row["details"].get("entry_fills") == 1
            ):
                raise OSError("simulated journal full")
            return self.handle.write(line)

    writer._open_append = lambda path: Poison(original(path))
    with pytest.raises(DiagnosticWriteError):
        run_cli_entry(request, out, report=True, invocation=writer)
    (attempt,) = (out / "attempts").iterdir()
    fills = json.loads((attempt / "fills.json").read_text())
    manifest = json.loads((attempt / "manifest.json").read_text())
    assert manifest["status"] == "failed" and not manifest["trace_complete"]
    if boundary == "filled":
        assert any(f["status"] == "FILLED" for f in fills)
    else:
        assert fills == []
    if mode == "legacy":
        account = json.loads((attempt / "account.json").read_text())
        assert bool(account["positions"]) is (boundary == "filled")
    db = RunStorage(out, read_only=True)
    try:
        assert db.load_record(attempt.name).status == "failed"
    finally:
        db.close()


@pytest.mark.parametrize("interrupted", [False, True])
def test_handled_preflight_failure_is_never_reclassified_as_crash(
    tmp_path, interrupted
):
    writer = preflight(tmp_path)
    writer.use_config({})
    writer.fail(KeyboardInterrupt() if interrupted else ValueError("invalid request"))
    writer.close()
    before = tree_bytes(tmp_path)
    status = inspect_attempt(tmp_path, writer.invocation_id)
    assert status.outcome == ("interrupted" if interrupted else "failed")
    assert not any(a.applied for a in recover_attempts(tmp_path, apply=True))
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize("command", ["backtest", "download"])
def test_historical_cli_progress_uses_only_pinned_fixture_cache(tmp_path, command):
    acquisition, backtest = cached(tmp_path / "cache_setup")
    request = tmp_path / "request.json"
    request.write_text(json.dumps(acquisition if command == "download" else backtest))
    out = tmp_path / "out"
    result = cli(command, "--config", request, "--out", out, "--progress", "on")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ok:") and "progress:" not in result.stdout
    assert "stage=preparing" in result.stderr and "event=success" in result.stderr
    assert "session=2024-" in result.stderr
    (invocation,) = (out / "invocations").iterdir()
    status = inspect_attempt(out, invocation.name)
    assert status.outcome == "success" and status.progress["completed_sessions"] > 0


def test_uncommitted_success_manifest_is_still_an_incomplete_run(tmp_path, monkeypatch):
    cfg, request = request_for("bar", tmp_path / "input")
    original = RunStorage.write_record

    def fail_success(storage, record):
        if record.status == "success":
            raise OSError("terminal commit failed")
        return original(storage, record)

    monkeypatch.setattr(RunStorage, "write_record", fail_success)
    out = tmp_path / "out"
    with pytest.raises(OSError, match="terminal commit failed"):
        execute("bar", cfg, request, out)
    manifest = next((out / "attempts").glob("*/manifest.json"))
    assert json.loads(manifest.read_text())["status"] == "success"
    status = inspect_attempt(out, manifest.parent.name)
    assert status.outcome == "running" and status.completeness == "partial"
    assert status.stage == "failed" and status.error
    assert "no terminal outcome was committed" in status.explanation


def test_reference_observations_do_not_call_the_adapter_again(tmp_path, monkeypatch):
    from rsys.execution.trade_adapter import TradeExecutionAdapter

    returned = []
    original = TradeExecutionAdapter.consider_entry

    def spy(adapter, intent, consumed, trade):
        outcome = original(adapter, intent, consumed, trade)
        returned.append((trade.trade_id, outcome.status))
        return outcome

    monkeypatch.setattr(TradeExecutionAdapter, "consider_entry", spy)
    cfg, request = request_for("trade", tmp_path / "input")
    _, _, _, path = execute("trade", cfg, request, tmp_path / "out")
    manifest = json.loads(path.read_text())
    reasons = sum(
        reason["count"]
        for s in manifest["funnel"]["sessions"]
        for reason in s["reasons"].values()
        if reason["stage"] == "entry_reference"
    )
    assert returned and len(returned) == reasons + 2  # two committed entry results
    assert sum(status == "FILLED" for _, status in returned) == 2

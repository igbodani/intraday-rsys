"""A production reader rejects damaged evidence without changing the original."""

import hashlib
import json
from pathlib import Path

import pytest

from rsys.application.run import run_cli_entry
from rsys.research import diagnostics
from rsys.research.comparison import load_run_manifest
from rsys.research.diagnostics import (
    read_diagnostic_journal,
    start_invocation,
    verify_diagnostic_seal,
)
from rsys.research.errors import (
    CompareCliError,
    DiagnosticIntegrityError,
    DiagnosticWriteError,
)
from rsys.research.models import DiagnosticEventCode as Code
from rsys.research.storage import RunStorage

ROOT = Path(__file__).resolve().parents[1]


def test_reader_reports_torn_prefix_without_repair(tmp_path):
    writer = start_invocation(tmp_path, command="run")
    writer.event(Code.CONFIG_LOADED, "preparing")
    original = writer.journal_path.read_bytes() + b'{"sequence":3'
    writer.journal_path.write_bytes(original)
    result = read_diagnostic_journal(writer.journal_path)
    assert result.record_count == 2 and result.incomplete_tail
    assert [record.sequence for record in result.records] == [1, 2]
    assert writer.journal_path.read_bytes() == original
    with pytest.raises(DiagnosticWriteError):
        writer.seal()
    assert writer.journal_path.read_bytes() == original


@pytest.mark.parametrize(
    "mutation", ["malformed", "gap", "invocation", "run", "version"]
)
def test_reader_rejects_complete_corruption_instead_of_skipping_it(tmp_path, mutation):
    writer = start_invocation(tmp_path, command="run")
    writer.link_run("run-a")
    writer.event(Code.CONFIG_LOADED, "resolved")
    writer.event(Code.PREFLIGHT_CHECK, "resolved")
    rows = [json.loads(line) for line in writer.journal_path.read_bytes().splitlines()]
    if mutation == "gap":
        rows[1]["sequence"] = 7
    elif mutation == "invocation":
        rows[1]["invocation_id"] = "another"
    elif mutation == "run":
        rows[2]["run_id"] = "run-b"
    elif mutation == "version":
        rows[1]["schema_version"] = 99
    raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    if mutation == "malformed":
        lines = raw.splitlines(keepends=True)
        lines[1] = b"not-json\n"
        raw = b"".join(lines)
    writer.journal_path.write_bytes(raw)
    with pytest.raises(DiagnosticIntegrityError):
        read_diagnostic_journal(writer.journal_path)
    assert writer.journal_path.read_bytes() == raw


def test_seal_checks_actual_bytes_and_forbids_later_append(tmp_path):
    writer = start_invocation(tmp_path, command="run")
    writer.artifacts_ready()
    seal = writer.seal()
    original = writer.journal_path.read_bytes()
    assert seal["journal_sha256"] == hashlib.sha256(original).hexdigest()
    verify_diagnostic_seal(tmp_path, seal)
    with pytest.raises(DiagnosticWriteError, match="sealed"):
        writer.event(Code.CONFIG_LOADED, "preparing")
    assert writer.journal_path.read_bytes() == original
    writer.journal_path.write_bytes(
        original.replace(b'"level": "info"', b'"level": "warning"', 1)
    )
    with pytest.raises(DiagnosticIntegrityError):
        verify_diagnostic_seal(tmp_path, seal)


@pytest.mark.parametrize("digest", [None, "", "invalid", "g" * 64])
def test_a_missing_or_invalid_hash_is_never_an_optional_check(tmp_path, digest):
    writer = start_invocation(tmp_path, command="run")
    seal = writer.seal()
    seal["journal_sha256"] = digest
    with pytest.raises(DiagnosticIntegrityError):
        verify_diagnostic_seal(tmp_path, seal)


@pytest.mark.parametrize(
    "fixture", ["standalone_run_fixture.json", "sessions_bar_fixture.json"]
)
@pytest.mark.parametrize("mutation", ["delete", "tamper"])
def test_comparison_requires_new_diagnostic_evidence(tmp_path, fixture, mutation):
    _, run_id, _, _ = run_cli_entry(ROOT / "fixtures" / fixture, tmp_path)
    assert load_run_manifest(tmp_path, run_id)["diagnostics_version"] == 1
    journal = next((tmp_path / "invocations").glob("*/diagnostics.jsonl"))
    if mutation == "delete":
        journal.unlink()
    else:
        journal.write_bytes(journal.read_bytes() + b"{}\n")
    with pytest.raises(CompareCliError, match="integrity"):
        load_run_manifest(tmp_path, run_id)


@pytest.mark.parametrize(
    "fixture", ["standalone_run_fixture.json", "sessions_bar_fixture.json"]
)
def test_original_schema_two_and_three_do_not_require_new_diagnostics(
    tmp_path, fixture
):
    _, run_id, _, manifest_path = run_cli_entry(ROOT / "fixtures" / fixture, tmp_path)
    manifest = json.loads(manifest_path.read_text())
    del manifest["diagnostics_version"]
    manifest_path.write_text(json.dumps(manifest))
    # Construct a correctly pinned original-format record in a separate store.
    db = RunStorage(tmp_path)
    try:
        record = db.load_record(run_id)
    finally:
        db.close()
    record.decision_gate_status.pop("diagnostics")
    record.decision_gate_status["manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()

    # A reader test must not weaken the production terminal-record immutability.
    class OriginalStorage:
        def __init__(self, root):
            pass

        def load_record(self, identity):
            assert identity == run_id
            return record

        def close(self):
            pass

    from unittest.mock import patch

    with patch("rsys.research.comparison.RunStorage", OriginalStorage):
        assert load_run_manifest(tmp_path, run_id) == manifest


def test_atomic_replacement_failure_retains_previous_complete_summary(
    tmp_path, monkeypatch
):
    path = tmp_path / "manifest.json"
    original = b'{"status":"finalizing"}'
    path.write_bytes(original)

    def fail(*args):
        raise PermissionError("replace denied")

    monkeypatch.setattr(diagnostics.os, "replace", fail)
    monkeypatch.setattr(diagnostics.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        diagnostics.atomic_write_json(path, {"status": "success"})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]

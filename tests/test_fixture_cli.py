"""CLI subprocess tests for `rsys fixture` (docs/M2_IMPLEMENTATION_HANDOFF.md step 4)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "import sys; from rsys.cli import main; sys.exit(main(sys.argv[1:]))", *args],
        cwd=cwd, capture_output=True, text=True, check=False,
    )


def test_fixture_all_cases_pass(tmp_path: Path) -> None:
    result = _run_cli("fixture", "--case", "all", "--out", str(tmp_path / "out"), cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MISMATCH" not in result.stdout
    for n in range(1, 18):
        assert f"ok: F{n:02d}" in result.stdout


def test_fixture_unknown_case_is_invalid_config(tmp_path: Path) -> None:
    result = _run_cli("fixture", "--case", "F99", "--out", str(tmp_path / "out"), cwd=REPO_ROOT)
    assert result.returncode == 2
    assert "unknown fixture case" in result.stderr


def test_repeated_invocations_get_distinct_retained_attempts(tmp_path: Path) -> None:
    out = tmp_path / "out"
    first = _run_cli("fixture", "--case", "F01", "--out", str(out), cwd=REPO_ROOT)
    second = _run_cli("fixture", "--case", "F01", "--out", str(out), cwd=REPO_ROOT)
    assert first.returncode == 0 and second.returncode == 0
    attempts_dir = out / "attempts"
    attempt_ids = [p.name for p in attempts_dir.iterdir() if p.is_dir()]
    assert len(attempt_ids) == 2
    traces = [json.loads((attempts_dir / aid / "trace.jsonl").read_text().splitlines()[0]) for aid in attempt_ids]
    assert traces[0]["case_id"] == traces[1]["case_id"] == "F01"
    assert traces[0]["checkpoints"] == traces[1]["checkpoints"]  # identical semantic output


def test_mutated_expected_checkpoint_fails_visibly(tmp_path: Path) -> None:
    """Negative control: proves the CLI actually checks expected values rather than always
    reporting success."""
    cases = json.loads((REPO_ROOT / "fixtures" / "cases.json").read_text())
    for case in cases["cases"]:
        if case["id"] == "F01":
            case["expected"][-1]["cash"] = "999999"  # deliberately wrong
    mutated_path = tmp_path / "cases_mutated.json"
    mutated_path.write_text(json.dumps(cases))

    # Point the CLI at the mutated fixture file by monkeypatching its known path via copy:
    # fixture_cli.py reads a fixed repo-relative path, so exercise the engine/compare path
    # directly here instead of through the CLI's fixed file location.
    import importlib
    fixture_engine = importlib.import_module("rsys.fixture_engine")
    case_f01 = next(c for c in cases["cases"] if c["id"] == "F01")
    result = fixture_engine.run_case(case_f01)
    problems = fixture_engine.compare_case(case_f01, result)
    assert problems, "mutated expected checkpoint must be detected as a mismatch"

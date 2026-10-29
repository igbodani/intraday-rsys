"""CLI subprocess tests for `rsys compare` (docs/M2_IMPLEMENTATION_HANDOFF.md step 9,
comparison subset). Compares `rsys run` (StandaloneRunner) manifests only."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CONFIG = REPO_ROOT / "fixtures" / "standalone_run_fixture.json"
_RUN_ID_RE = re.compile(r"run_id: (\S+)")


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "import sys; from rsys.cli import main; sys.exit(main(sys.argv[1:]))", *args],
        cwd=cwd, capture_output=True, text=True, check=False,
    )


def _run_once(out_dir: Path, config_path: Path = FIXTURE_CONFIG) -> str:
    result = _run_cli("run", "--config", str(config_path), "--out", str(out_dir), cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    return _RUN_ID_RE.search(result.stdout).group(1)


def test_identical_configs_compare_with_no_assumption_diffs_and_zero_delta(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    run_a = _run_once(storage_root)
    run_b = _run_once(storage_root)

    result = _run_cli(
        "compare", "--storage-root", str(storage_root), "--runs", run_a, run_b,
        "--out", str(tmp_path / "cmp"), cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "assumption_diffs: {}" in result.stdout
    assert "ending_equity_delta: 0" in result.stdout
    report = json.loads((tmp_path / "cmp" / "comparison.json").read_text())
    assert report["assumption_diffs"] == {}
    assert report["ending_equity_delta"] == "0"


def test_different_universe_is_an_unexplained_difference(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    run_a = _run_once(storage_root)

    config = json.loads(FIXTURE_CONFIG.read_text())
    config["symbols"]["BBB"] = dict(config["symbols"]["AAA"])
    config["scanner_inputs"] = {}  # BBB never a candidate, but still changes the declared universe
    modified_config = tmp_path / "modified.json"
    modified_config.write_text(json.dumps(config))
    run_b = _run_once(storage_root, modified_config)

    result = _run_cli(
        "compare", "--storage-root", str(storage_root), "--runs", run_a, run_b,
        "--out", str(tmp_path / "cmp"), cwd=REPO_ROOT,
    )
    assert result.returncode == 4  # EXIT_INVARIANT_FAILURE
    assert "universe" in result.stderr


def test_a_declared_permitted_difference_is_reported_not_rejected(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    run_a = _run_once(storage_root)

    config = json.loads(FIXTURE_CONFIG.read_text())
    config["sizing"]["max_quantity"] = "5"
    modified_config = tmp_path / "modified.json"
    modified_config.write_text(json.dumps(config))
    run_b = _run_once(storage_root, modified_config)

    result = _run_cli(
        "compare", "--storage-root", str(storage_root), "--runs", run_a, run_b,
        "--permitted-difference", "risk_profile", "--out", str(tmp_path / "cmp"), cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "risk_profile" in result.stdout


def test_unknown_run_id_is_a_data_error(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    run_a = _run_once(storage_root)
    result = _run_cli(
        "compare", "--storage-root", str(storage_root), "--runs", run_a, "does-not-exist",
        "--out", str(tmp_path / "cmp"), cwd=REPO_ROOT,
    )
    assert result.returncode == 3  # EXIT_DATA_ERROR
    assert "no retained manifest" in result.stderr

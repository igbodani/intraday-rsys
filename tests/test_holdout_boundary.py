"""Holdout-boundary tests (docs/M2_IMPLEMENTATION_HANDOFF.md step 9 "Holdout boundary").
Uses synthetic segregated partitions; no real final-holdout data is needed."""
from __future__ import annotations

from pathlib import Path

import pytest

from rsys.research.errors import HoldoutAccessDenied
from rsys.research.holdout import (
    FINAL_EVALUATE_EXIT_CODE,
    HoldoutGuard,
    compute_file_hash,
    final_evaluate,
    verify_partition_integrity,
)
from rsys.research.models import PartitionManifest


@pytest.fixture
def partitions(tmp_path: Path):
    dev_dir = tmp_path / "dev"
    dev_dir.mkdir()
    dev_file = dev_dir / "dev.json"
    dev_file.write_text('{"synthetic": true}')

    holdout_dir = tmp_path / "holdout"
    holdout_dir.mkdir()
    holdout_file = holdout_dir / "final.json"
    holdout_file.write_text('{"sealed": true}')

    dev_manifest = PartitionManifest(
        partition_id="dev", paths=(str(dev_file),), content_hash=compute_file_hash(dev_file), sealed=False,
    )
    holdout_manifest = PartitionManifest(
        partition_id="final_holdout", paths=(str(holdout_file),),
        content_hash=compute_file_hash(holdout_file), sealed=True,
    )
    return dev_manifest, holdout_manifest, dev_file, holdout_file


def test_development_partition_is_allowed(partitions):
    dev_manifest, holdout_manifest, dev_file, _holdout_file = partitions
    guard = HoldoutGuard([dev_manifest, holdout_manifest])
    manifest = guard.check_access(str(dev_file))
    assert manifest.partition_id == "dev"
    assert guard.access_log[-1].allowed is True


def test_sealed_partition_is_denied_before_any_read(partitions):
    dev_manifest, holdout_manifest, _dev_file, holdout_file = partitions
    guard = HoldoutGuard([dev_manifest, holdout_manifest])

    def failing_reader(_path):
        pytest.fail("reader must never be called for a denied path")

    with pytest.raises(HoldoutAccessDenied):
        guard.guarded_read(str(holdout_file), failing_reader)
    assert guard.access_log[-1].allowed is False
    assert guard.access_log[-1].partition_id == "final_holdout"


def test_warmup_labeled_request_against_sealed_partition_is_still_denied(partitions):
    dev_manifest, holdout_manifest, _dev_file, holdout_file = partitions
    guard = HoldoutGuard([dev_manifest, holdout_manifest])
    with pytest.raises(HoldoutAccessDenied):
        guard.check_access(str(holdout_file), purpose="warmup")


def test_path_alias_resolving_to_the_sealed_path_is_still_denied(partitions, tmp_path):
    dev_manifest, holdout_manifest, _dev_file, _holdout_file = partitions
    guard = HoldoutGuard([dev_manifest, holdout_manifest])
    alias = tmp_path / "holdout" / ".." / "holdout" / "final.json"  # resolves to the same sealed path
    with pytest.raises(HoldoutAccessDenied):
        guard.check_access(str(alias))


def test_unknown_undeclared_path_is_denied_not_silently_allowed(partitions, tmp_path):
    dev_manifest, holdout_manifest, _dev_file, _holdout_file = partitions
    guard = HoldoutGuard([dev_manifest, holdout_manifest])
    stray = tmp_path / "elsewhere.json"
    stray.write_text("{}")
    with pytest.raises(HoldoutAccessDenied):
        guard.check_access(str(stray))


def test_duplicate_path_declared_in_two_partitions_rejected_at_construction(partitions, tmp_path):
    dev_manifest, _holdout_manifest, dev_file, _holdout_file = partitions
    conflicting = PartitionManifest(
        partition_id="other", paths=(str(dev_file),), content_hash="irrelevant", sealed=True,
    )
    with pytest.raises(ValueError, match="declared in more than one partition"):
        HoldoutGuard([dev_manifest, conflicting])


def test_tampered_file_fails_integrity_verification(partitions):
    dev_manifest, _holdout_manifest, dev_file, _holdout_file = partitions
    dev_file.write_text('{"tampered": true}')  # content changed after the manifest was frozen
    problems = verify_partition_integrity(dev_manifest, base_dir=dev_file.parent)
    assert len(problems) == 1
    assert "content hash mismatch" in problems[0]


def test_final_evaluate_is_always_blocked_with_truthful_reasons():
    result = final_evaluate()
    assert result.blocked is True
    assert len(result.missing_requirements) >= 1
    assert FINAL_EVALUATE_EXIT_CODE == 5

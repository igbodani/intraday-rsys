"""Windows failure contracts, with deterministic controls on every host."""

import os
import socket
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from rsys.data import bar_cache
from rsys.data.bar_cache import BarCache, encode, request_identity
from rsys.research import storage
from rsys.research.errors import RecoveryInProgressError


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink-parent semantics")
def test_posix_cache_root_resolves_symlinks_before_parent_segments(tmp_path):
    child = tmp_path / "actual" / "child"
    child.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(child, target_is_directory=True)
    assert BarCache(alias / ".." / "cache").root == tmp_path / "actual" / "cache"


@pytest.mark.parametrize(
    "given,expected",
    [
        (r"C:\cache", r"\\?\C:\cache"),
        (r"\\server\share\cache", r"\\?\UNC\server\share\cache"),
        (r"\\?\C:\cache", r"\\?\C:\cache"),
        (r"\\?\UNC\server\share\cache", r"\\?\UNC\server\share\cache"),
    ],
)
def test_extended_windows_cache_paths_preserve_drive_or_share(given, expected):
    assert bar_cache._windows_extended_path(given) == expected


def test_atomic_cache_temp_collision_does_not_remove_another_writers_file(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bar_cache, "uuid4", lambda: SimpleNamespace(hex="reserved"))
    reserved = tmp_path / ".reserved.tmp"
    reserved.write_bytes(b"another writer")
    target = tmp_path / ("a" * 64 + ".json")
    with pytest.raises(FileExistsError):
        BarCache._write(target, {"complete": True})
    assert reserved.read_bytes() == b"another writer"
    assert not target.exists()


def test_atomic_cache_write_does_not_exceed_a_valid_destination_path(
    tmp_path, monkeypatch
):
    parent = tmp_path / ("p" * max(1, 175 - len(str(tmp_path))))
    target = parent / ("a" * 64 + ".json")
    assert len(str(target)) < 260
    real_open = Path.open

    def limited_open(path, *args, **kwargs):
        if len(str(path)) >= 260:
            raise FileNotFoundError("synthetic Windows MAX_PATH limit")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", limited_open)
    BarCache._write(target, {"complete": True})
    assert target.read_bytes() == encode({"complete": True})
    assert list(parent.iterdir()) == [target]


@pytest.mark.parametrize("phase", ["write", "replace"])
def test_failed_atomic_cache_write_preserves_prior_bytes_and_cleans_temp(
    tmp_path, monkeypatch, phase
):
    target = tmp_path / ("a" * 64 + ".json")
    target.write_bytes(b"original")
    real_open = Path.open

    def fail_open(path, mode="r", *args, **kwargs):
        if path.suffix == ".tmp" and any(flag in mode for flag in "wx"):
            raise OSError("synthetic write failure")
        return real_open(path, mode, *args, **kwargs)

    def fail_replace(*args, **kwargs):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(
        Path,
        "open" if phase == "write" else "replace",
        fail_open if phase == "write" else fail_replace,
    )
    with pytest.raises(OSError, match="synthetic"):
        BarCache._write(target, {"replacement": True})
    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]


def test_long_cache_root_round_trips_reuses_and_checks_integrity(tmp_path):
    # The finished object, not just its temporary sibling, exceeds MAX_PATH.
    cache = BarCache(tmp_path / ("deep-" * 20) / ("cache-" * 16))
    request = request_identity("AAPL", date(2024, 11, 27), "iex", "-")
    calls = []

    def pages(params):
        calls.append(params)
        yield {"bars": {"AAPL": []}}

    client = SimpleNamespace(pages=pages)
    identity = cache.acquire(request, client)
    assert cache.acquire(request, client) == identity and len(calls) == 1
    payload = cache.load(identity, request)
    assert payload["quality"]["coverage_complete"] is False
    assert len(payload["quality"]["missing_minutes"]) == 390
    assert cache.object_size(identity, request) == len(encode(payload))
    (target,) = cache.root.glob(f"objects/*/{identity}.json")
    assert len(str(target)) > 260
    target.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        cache.load(identity, request)


@pytest.mark.parametrize("pid", [2**32, 2**32 + 42, 10**100])
def test_unrepresentable_windows_pid_is_unknown_before_os_call(monkeypatch, pid):
    calls = []

    def open_process(*args):
        calls.append(args)
        return 0

    monkeypatch.setattr(storage, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        storage, "_kernel32", SimpleNamespace(OpenProcess=open_process), raising=False
    )
    monkeypatch.setattr(storage.ctypes, "get_last_error", lambda: 87, raising=False)
    assert storage.owner_state(socket.gethostname(), pid) == "unknown"
    assert not calls


@pytest.mark.parametrize("guard_bytes", [b"", b"0"])
def test_windows_rejected_recovery_preserves_existing_guard(
    tmp_path, monkeypatch, guard_bytes
):
    guard = tmp_path / ".recovery.guard"
    guard.write_bytes(guard_bytes)
    metadata = tmp_path / ".recovery.lock"
    metadata.write_text('{"host":"different-host","pid":42}')
    before = (guard.read_bytes(), guard.stat().st_mtime_ns, metadata.read_bytes())
    locks = []
    monkeypatch.setattr(storage, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        storage,
        "msvcrt",
        SimpleNamespace(
            LK_NBLCK=1,
            LK_UNLCK=2,
            locking=lambda fd, operation, count: locks.append(
                (operation, count, os.lseek(fd, 0, os.SEEK_CUR))
            ),
        ),
        raising=False,
    )
    with pytest.raises(RecoveryInProgressError), storage.recovery_lock(tmp_path):
        pytest.fail("remote owner must not be recovered")
    assert (
        guard.read_bytes(),
        guard.stat().st_mtime_ns,
        metadata.read_bytes(),
    ) == before
    assert locks == [(1, 1, 0), (2, 1, 0)]

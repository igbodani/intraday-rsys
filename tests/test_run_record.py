import multiprocessing
import time

import pytest

from rsys import storage as storage_module
from rsys.experiments import (
    canonical_hash,
    compute_code_revision,
    compute_dependency_lock_hash,
    compute_source_fingerprint,
    extract_secret_values,
    mark_interrupted,
    redact_secrets,
    run_context,
    start_run,
)
from rsys.storage import (
    FrozenFieldError,
    IllegalTransitionError,
    InvalidRunIdError,
    RecoveryInProgressError,
    RunAlreadyExistsError,
    RunStorage,
)

NO_DATA = {"data_not_applicable_reason": "fixture-only synthetic test run, no external input data"}


def _storage(tmp_path):
    return RunStorage(tmp_path / "runs")


def test_run_ids_are_unique(tmp_path):
    storage = _storage(tmp_path)
    r1 = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    r2 = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    assert r1.run_id != r2.run_id


def test_failed_run_is_retained(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={"a": 1}, **NO_DATA)

    with pytest.raises(RuntimeError), run_context(storage, record):
        raise RuntimeError("boom")

    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "failed"
    assert "boom" in reloaded.error
    assert reloaded.ended_at is not None


def test_successful_run_transitions_to_success(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)

    with run_context(storage, record):
        pass

    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "success"
    assert reloaded.error is None


def test_duplicate_run_id_does_not_overwrite(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={"a": 1}, **NO_DATA)

    duplicate = record.model_copy(deep=True)
    duplicate.resolved_config = {"a": 999}
    with pytest.raises(RunAlreadyExistsError):
        storage.create_run(duplicate)

    reloaded = storage.load_record(record.run_id)
    assert reloaded.resolved_config == {"a": 1}


def test_secrets_are_excluded_from_persisted_record(tmp_path):
    storage = _storage(tmp_path)
    secret_config = {"provider": "alpaca", "api_key": "super-secret-value", "nested": {"auth_token": "also-secret"}}
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config=secret_config, **NO_DATA)

    reloaded = storage.load_record(record.run_id)
    assert "super-secret-value" not in str(reloaded.resolved_config)
    assert "also-secret" not in str(reloaded.resolved_config)
    assert reloaded.resolved_config["api_key"] == "REDACTED"


def test_redact_secrets_matches_key_patterns():
    redacted = redact_secrets({"api_key": "x", "password": "y", "normal": "z", "nested": {"TOKEN": "w"}})
    assert redacted == {"api_key": "REDACTED", "password": "REDACTED", "normal": "z", "nested": {"TOKEN": "REDACTED"}}


def test_redact_secrets_does_not_false_positive_on_ranking_sort_key():
    """Regression: a plain substring match on 'key' wrongly redacted research settings."""
    redacted = redact_secrets({"ranking_sort_key": "rvol_desc", "quantity_step": "1"})
    assert redacted == {"ranking_sort_key": "rvol_desc", "quantity_step": "1"}


@pytest.mark.parametrize(
    "field_name",
    ["apiKey", "authToken", "clientSecret", "Authorization", "ALPACA_API_KEY", "X-Api-Key", "Bearer"],
)
def test_redact_secrets_catches_camelcase_and_header_style_canaries(field_name):
    redacted = redact_secrets({field_name: "shh"})
    assert redacted[field_name] == "REDACTED"


def test_canonical_hash_is_order_independent():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


# ---- Immutable inputs (D04/M0-02 #1) ----------------------------------------

def test_start_run_requires_data_provenance_or_explicit_reason(tmp_path):
    storage = _storage(tmp_path)
    with pytest.raises(ValueError):
        start_run(storage, hypothesis="h", phase="M0", resolved_config={})


def test_start_run_rejects_blank_data_identity_and_blank_reason(tmp_path):
    storage = _storage(tmp_path)
    with pytest.raises(ValueError):
        start_run(storage, hypothesis="h", phase="M0", resolved_config={}, data_identity="   ")
    with pytest.raises(ValueError):
        start_run(storage, hypothesis="h", phase="M0", resolved_config={}, data_not_applicable_reason="   ")


def test_start_run_rejects_contradictory_data_declarations(tmp_path):
    storage = _storage(tmp_path)
    with pytest.raises(ValueError):
        start_run(
            storage, hypothesis="h", phase="M0", resolved_config={},
            data_identity="sha256:abc", data_not_applicable_reason="no data used",
        )


def test_resolved_config_cannot_change_after_creation(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={"a": 1}, **NO_DATA)
    record.resolved_config = {"a": 2}
    with pytest.raises(FrozenFieldError):
        storage.write_record(record)


def test_config_hash_cannot_change_after_creation(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={"a": 1}, **NO_DATA)
    record.config_hash = "tampered"
    with pytest.raises(FrozenFieldError):
        storage.write_record(record)


def test_hypothesis_cannot_change_after_creation(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    record.hypothesis = "different hypothesis"
    with pytest.raises(FrozenFieldError):
        storage.write_record(record)


def test_identical_resolved_config_gets_separate_run_ids(tmp_path):
    storage = _storage(tmp_path)
    r1 = start_run(storage, hypothesis="h", phase="M0", resolved_config={"a": 1}, **NO_DATA)
    r2 = start_run(storage, hypothesis="h", phase="M0", resolved_config={"a": 1}, **NO_DATA)
    assert r1.run_id != r2.run_id
    assert r1.config_hash == r2.config_hash


def test_create_run_rejects_mismatched_config_hash(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={"a": 1}, **NO_DATA)
    tampered = record.model_copy(deep=True)
    tampered.run_id = "a-different-run-id"
    tampered.config_hash = "not-the-real-hash"
    with pytest.raises(ValueError):
        storage.create_run(tampered)


# ---- Legal lifecycle and recovery (M0-02 #2) --------------------------------

def test_finalized_run_cannot_transition_back_to_running(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    with run_context(storage, record):
        pass
    assert record.status == "success"

    record.status = "running"
    with pytest.raises(IllegalTransitionError):
        storage.write_record(record)


def test_rerun_after_success_gets_a_fresh_id(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    with run_context(storage, record):
        pass

    rerun = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    assert rerun.run_id != record.run_id


def test_handled_interruption_preserves_truthful_status(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    record.status = "running"
    storage.write_record(record)

    mark_interrupted(storage, record, reason="SIGTERM")

    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "interrupted"
    assert reloaded.ended_at is not None
    assert "SIGTERM" in reloaded.error


def test_keyboard_interrupt_inside_run_context_is_recorded_as_interrupted_not_failed(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)

    with pytest.raises(KeyboardInterrupt), run_context(storage, record):
        raise KeyboardInterrupt

    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "interrupted"
    assert reloaded.ended_at is not None


def test_run_context_does_not_overwrite_an_already_finalized_record(tmp_path):
    """A concurrent recovery sweep (or another finalizer) may finish first; the
    context manager's own finalize must not clobber that outcome or raise."""
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    record.status = "running"
    storage.write_record(record)

    with run_context(storage, record) as ctx_record:
        # Simulate a concurrent finalizer (e.g. recovery) beating run_context to it.
        already_crashed = storage.load_record(record.run_id)
        already_crashed.status = "crashed"
        already_crashed.ended_at = "2024-01-01T00:00:00+00:00"
        already_crashed.error = "recovered: simulated concurrent finalize"
        storage.write_record(already_crashed)
        ctx_record.status = "running"  # run_context's own in-memory copy is unaware

    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "crashed"  # the concurrent outcome stands; run_context did not overwrite it


def test_recover_crashed_runs_does_not_touch_finalized_runs(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    with run_context(storage, record):
        pass

    recovered_ids = storage.recover_crashed_runs()

    assert recovered_ids == []
    assert storage.load_record(record.run_id).status == "success"


def test_recover_crashed_runs_marks_a_run_with_no_live_owner_as_crashed(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    record.status = "running"
    record.owner_pid = None  # unknown/legacy owner: cannot be verified alive
    storage.write_record(record)

    recovered_ids = storage.recover_crashed_runs(reason="test harness simulated crash")

    assert record.run_id in recovered_ids
    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "crashed"
    assert reloaded.ended_at is not None
    assert "recovered" in reloaded.error


def test_recovery_lock_rejects_a_concurrent_sweep(tmp_path):
    storage = _storage(tmp_path)
    lock_path = storage.root_dir / ".recovery.lock"
    lock_path.touch()
    try:
        with pytest.raises(RecoveryInProgressError):
            storage.recover_crashed_runs()
    finally:
        lock_path.unlink(missing_ok=True)


def _mp_child_crash(root_dir: str, queue: multiprocessing.Queue) -> None:
    storage = RunStorage(root_dir)
    record = start_run(
        storage, hypothesis="h", phase="M0", resolved_config={},
        data_not_applicable_reason="multiprocessing recovery test",
    )
    record.status = "running"
    record.owner_pid = __import__("os").getpid()
    storage.write_record(record)
    queue.put(record.run_id)
    # Process exits here without ever finalizing -- simulates a crash.


def _mp_child_stay_alive(root_dir: str, queue: multiprocessing.Queue, sleep_seconds: float) -> None:
    storage = RunStorage(root_dir)
    record = start_run(
        storage, hypothesis="h", phase="M0", resolved_config={},
        data_not_applicable_reason="multiprocessing recovery test",
    )
    record.status = "running"
    record.owner_pid = __import__("os").getpid()
    storage.write_record(record)
    queue.put(record.run_id)
    time.sleep(sleep_seconds)


def test_recover_crashed_runs_marks_a_real_dead_child_process_as_crashed(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_mp_child_crash, args=(str(tmp_path / "runs"), queue))
    proc.start()
    run_id = queue.get(timeout=30)
    proc.join(timeout=30)
    assert not proc.is_alive()

    storage = _storage(tmp_path)
    recovered_ids = storage.recover_crashed_runs()

    assert run_id in recovered_ids
    assert storage.load_record(run_id).status == "crashed"


def test_recover_crashed_runs_leaves_a_live_owned_run_running(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_mp_child_stay_alive, args=(str(tmp_path / "runs"), queue, 5.0))
    proc.start()
    try:
        run_id = queue.get(timeout=30)

        storage = _storage(tmp_path)
        recovered_ids = storage.recover_crashed_runs()

        assert run_id not in recovered_ids
        assert storage.load_record(run_id).status == "running"
    finally:
        proc.terminate()
        proc.join(timeout=10)


# ---- Consistent persistence (M0-02 #3) --------------------------------------

def test_injected_persistence_failure_leaves_record_in_last_good_state(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)

    def _boom(*_args, **_kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(storage, "_insert_transition", _boom)
    record.status = "running"
    with pytest.raises(OSError):
        storage.write_record(record)

    monkeypatch.undo()
    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "pending"  # the whole transaction rolled back, including the UPDATE


def test_transition_log_records_every_status_change_with_payload_integrity(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    with run_context(storage, record):
        pass

    transitions = storage.read_transitions(record.run_id)
    assert [t["status"] for t in transitions] == ["pending", "running", "success"]
    assert all(t["payload_sha256"] for t in transitions)


def test_reopening_storage_yields_one_truthful_committed_state(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    with run_context(storage, record):
        pass

    reopened = RunStorage(tmp_path / "runs")
    reloaded = reopened.load_record(record.run_id)
    assert reloaded.status == "success"
    assert reopened.read_transitions(record.run_id) == storage.read_transitions(record.run_id)


# ---- Correct secret handling (M0-02 #4) -------------------------------------

def test_extract_secret_values_finds_configured_secrets():
    values = extract_secret_values({"api_key": "shh", "nested": {"auth_token": "also-shh"}, "normal": "visible"})
    assert set(values) == {"shh", "also-shh"}


def test_fake_secret_is_scrubbed_from_saved_error_text(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(
        storage, hypothesis="h", phase="M0", resolved_config={"api_key": "FAKE_SECRET_CANARY"}, **NO_DATA
    )

    with pytest.raises(RuntimeError), run_context(storage, record):
        raise RuntimeError("request failed for key FAKE_SECRET_CANARY")

    reloaded = storage.load_record(record.run_id)
    assert "FAKE_SECRET_CANARY" not in reloaded.error
    assert "REDACTED" in reloaded.error


def test_scrubbing_is_safe_after_reload_even_though_in_memory_secrets_are_gone(tmp_path):
    """Reloaded records never carry real secret values forward (they were already redacted
    at save time), so scrubbing with an empty in-memory secret list is safe, not a leak."""
    storage = _storage(tmp_path)
    record = start_run(
        storage, hypothesis="h", phase="M0", resolved_config={"api_key": "FAKE_SECRET_CANARY"}, **NO_DATA
    )
    with run_context(storage, record):
        pass

    reloaded = storage.load_record(record.run_id)
    assert reloaded._secret_values == []
    assert "FAKE_SECRET_CANARY" not in str(reloaded.resolved_config)


# ---- Complete provenance (M0-02 #5 / H2 content provenance) -----------------

def test_run_record_captures_code_and_dependency_provenance(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    assert record.code_hash
    assert record.code_manifest
    assert record.code_revision
    assert record.dependency_lock_hash


def test_data_not_applicable_reason_is_recorded_explicitly(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    assert record.data_hash is not None
    assert "fixture-only synthetic test run" in record.data_hash


def test_data_identity_is_recorded_when_provided(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(
        storage, hypothesis="h", phase="M0", resolved_config={}, data_identity="sha256:deadbeef",
    )
    assert record.data_hash == "sha256:deadbeef"


def test_compute_source_fingerprint_differs_for_differently_dirty_trees(tmp_path):
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    for repo in (repo_a, repo_b):
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (repo_b / "src" / "mod.py").write_text("x = 2\n", encoding="utf-8")

    hash_a, manifest_a = compute_source_fingerprint(repo_root=repo_a)
    hash_b, manifest_b = compute_source_fingerprint(repo_root=repo_b)
    assert hash_a != hash_b
    assert manifest_a == manifest_b == ["src/mod.py"]


def test_compute_source_fingerprint_excludes_credential_shaped_files(tmp_path):
    repo = tmp_path / "repo_c"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _, manifest = compute_source_fingerprint(repo_root=repo)
    assert ".env" not in manifest


def test_compute_code_revision_returns_sentinel_outside_a_git_repo(tmp_path):
    assert compute_code_revision(repo_root=tmp_path).startswith("unavailable:")


def test_compute_dependency_lock_hash_returns_sentinel_without_lockfile(tmp_path):
    assert compute_dependency_lock_hash(repo_root=tmp_path) == "unavailable:no_lockfile"


# ---- Recovery ownership (M0-02 #5 / H2) -------------------------------------

def test_owner_pid_and_host_recorded_when_run_starts(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    assert record.owner_pid is None  # not yet running
    with run_context(storage, record):
        assert record.owner_pid is not None
        assert record.owner_host is not None


def test_owner_pid_cannot_be_reassigned_once_set(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    record.status = "running"
    record.owner_pid = 111
    storage.write_record(record)

    record.owner_pid = 222
    with pytest.raises(FrozenFieldError):
        storage.write_record(record)


# ---- Safe storage paths (M0-02 #6 / H2: single-component run IDs) ----------

@pytest.mark.parametrize("bad_run_id", ["../escape", "/etc/passwd", "a/../../b", "", "a/b", "a\\b"])
def test_non_single_component_run_ids_are_rejected(tmp_path, bad_run_id):
    storage = _storage(tmp_path)
    with pytest.raises(InvalidRunIdError):
        storage.load_record(bad_run_id)


def test_valid_single_component_run_id_is_accepted(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    assert storage.load_record(record.run_id).run_id == record.run_id


# ---- Validated execution boundary (M0-02 #7) --------------------------------

def test_write_record_requires_an_existing_run(tmp_path):
    storage = _storage(tmp_path)
    phantom = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    phantom.run_id = "never-created"
    with pytest.raises(storage_module.RunNotFoundError):
        storage.write_record(phantom)



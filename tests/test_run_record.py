import pytest

from rsys import storage as storage_module
from rsys.experiments import (
    canonical_hash,
    compute_code_hash,
    compute_dependency_lock_hash,
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

    record_path = storage.root_dir / record.run_id / "record.json"
    contents = record_path.read_text(encoding="utf-8")
    assert "super-secret-value" not in contents
    assert "also-secret" not in contents
    assert "REDACTED" in contents


def test_redact_secrets_matches_key_patterns():
    redacted = redact_secrets({"api_key": "x", "password": "y", "normal": "z", "nested": {"TOKEN": "w"}})
    assert redacted == {"api_key": "REDACTED", "password": "REDACTED", "normal": "z", "nested": {"TOKEN": "REDACTED"}}


def test_redact_secrets_does_not_false_positive_on_ranking_sort_key():
    """Regression: a plain substring match on 'key' wrongly redacted research settings."""
    redacted = redact_secrets({"ranking_sort_key": "rvol_desc", "quantity_step": "1"})
    assert redacted == {"ranking_sort_key": "rvol_desc", "quantity_step": "1"}


def test_canonical_hash_is_order_independent():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_atomic_write_leaves_no_temp_files(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    run_dir = storage.root_dir / record.run_id
    assert not list(run_dir.glob("*.tmp"))


# ---- Immutable inputs (D04/M0-02 #1) ----------------------------------------

def test_start_run_requires_data_provenance_or_explicit_reason(tmp_path):
    storage = _storage(tmp_path)
    with pytest.raises(ValueError):
        start_run(storage, hypothesis="h", phase="M0", resolved_config={})


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


def test_recover_crashed_runs_marks_stuck_running_as_crashed_not_success(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    record.status = "running"
    storage.write_record(record)  # simulate a process that died mid-run, never reaching run_context's cleanup

    recovered_ids = storage.recover_crashed_runs(reason="test harness simulated crash")

    assert record.run_id in recovered_ids
    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "crashed"
    assert reloaded.ended_at is not None
    assert "recovered" in reloaded.error


def test_recover_crashed_runs_does_not_touch_finalized_runs(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    with run_context(storage, record):
        pass

    recovered_ids = storage.recover_crashed_runs()

    assert recovered_ids == []
    assert storage.load_record(record.run_id).status == "success"


# ---- Consistent persistence (M0-02 #3) --------------------------------------

def test_injected_persistence_failure_leaves_record_in_last_good_state(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)

    def _boom(*_args, **_kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(storage_module, "_atomic_write_json", _boom)
    record.status = "running"
    with pytest.raises(OSError):
        storage.write_record(record)

    monkeypatch.undo()
    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "pending"  # last durably-committed state, not corrupted or half-written
    assert not list((storage.root_dir / record.run_id).glob("*.tmp"))


def test_transition_log_records_every_status_change(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    with run_context(storage, record):
        pass

    statuses = [entry["status"] for entry in storage.read_transitions(record.run_id)]
    assert statuses == ["pending", "running", "success"]


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


# ---- Complete provenance (M0-02 #5) ------------------------------------------

def test_run_record_captures_code_and_dependency_provenance(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    assert record.code_hash
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


def test_compute_code_hash_returns_sentinel_outside_a_git_repo(tmp_path):
    assert compute_code_hash(repo_root=tmp_path).startswith("unavailable:")


def test_compute_dependency_lock_hash_returns_sentinel_without_lockfile(tmp_path):
    assert compute_dependency_lock_hash(repo_root=tmp_path) == "unavailable:no_lockfile"


# ---- Safe storage paths (M0-02 #6) ------------------------------------------

@pytest.mark.parametrize("bad_run_id", ["../escape", "/etc/passwd", "a/../../b", ""])
def test_traversal_run_ids_cannot_escape_storage_root(tmp_path, bad_run_id):
    storage = _storage(tmp_path)
    with pytest.raises(InvalidRunIdError):
        storage._run_dir(bad_run_id)


def test_valid_nested_run_id_stays_inside_root(tmp_path):
    storage = _storage(tmp_path)
    run_dir = storage._run_dir("2024-01-01/abc123")
    assert storage.root_dir.resolve() in run_dir.parents


# ---- Validated execution boundary (M0-02 #7) --------------------------------

def test_write_record_requires_an_existing_run(tmp_path):
    storage = _storage(tmp_path)
    phantom = start_run(storage, hypothesis="h", phase="M0", resolved_config={}, **NO_DATA)
    phantom.run_id = "never-created"
    with pytest.raises(storage_module.RunNotFoundError):
        storage.write_record(phantom)


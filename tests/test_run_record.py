import pytest

from rsys.experiments import canonical_hash, redact_secrets, run_context, start_run
from rsys.storage import RunAlreadyExistsError, RunStorage


def _storage(tmp_path):
    return RunStorage(tmp_path / "runs")


def test_run_ids_are_unique(tmp_path):
    storage = _storage(tmp_path)
    r1 = start_run(storage, hypothesis="h", phase="M0", resolved_config={})
    r2 = start_run(storage, hypothesis="h", phase="M0", resolved_config={})
    assert r1.run_id != r2.run_id


def test_failed_run_is_retained(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={"a": 1})

    with pytest.raises(RuntimeError), run_context(storage, record):
        raise RuntimeError("boom")

    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "failed"
    assert "boom" in reloaded.error
    assert reloaded.ended_at is not None


def test_successful_run_transitions_to_success(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={})

    with run_context(storage, record):
        pass

    reloaded = storage.load_record(record.run_id)
    assert reloaded.status == "success"
    assert reloaded.error is None


def test_duplicate_run_id_does_not_overwrite(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={"a": 1})

    duplicate = record.model_copy(deep=True)
    duplicate.resolved_config = {"a": 999}
    with pytest.raises(RunAlreadyExistsError):
        storage.create_run(duplicate)

    reloaded = storage.load_record(record.run_id)
    assert reloaded.resolved_config == {"a": 1}


def test_secrets_are_excluded_from_persisted_record(tmp_path):
    storage = _storage(tmp_path)
    secret_config = {"provider": "alpaca", "api_key": "super-secret-value", "nested": {"auth_token": "also-secret"}}
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config=secret_config)

    record_path = storage.root_dir / record.run_id / "record.json"
    contents = record_path.read_text(encoding="utf-8")
    assert "super-secret-value" not in contents
    assert "also-secret" not in contents
    assert "REDACTED" in contents


def test_redact_secrets_matches_key_patterns():
    redacted = redact_secrets({"api_key": "x", "password": "y", "normal": "z", "nested": {"TOKEN": "w"}})
    assert redacted == {"api_key": "REDACTED", "password": "REDACTED", "normal": "z", "nested": {"TOKEN": "REDACTED"}}


def test_canonical_hash_is_order_independent():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_atomic_write_leaves_no_temp_files(tmp_path):
    storage = _storage(tmp_path)
    record = start_run(storage, hypothesis="h", phase="M0", resolved_config={})
    run_dir = storage.root_dir / record.run_id
    assert not list(run_dir.glob("*.tmp"))

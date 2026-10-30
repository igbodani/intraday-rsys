"""Public CLI/config coverage, timing, evidence and synthetic observer acceptance."""

import json
from pathlib import Path

import pytest
import yaml

from rsys.cli import EXIT_DATA_ERROR, main
from rsys.config import ConfigError, load_config, load_raw, parse_config
from rsys.replay_io import ReplayInputError, run_replay_from_config
from rsys.storage import RunStorage

ROOT = Path(__file__).resolve().parents[1]


def config(mode="trade"):
    return load_config(ROOT / f"configs/replay_{mode}_fixture.yaml")


def input_config(tmp_path, document, cfg=None):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(document))
    return (cfg or config()).model_copy(update={"input": str(path)})


def test_unreadable_input_retains_failed_attempt_and_explicit_unavailable_provenance(
    tmp_path,
):
    cfg = config().model_copy(update={"input": str(tmp_path / "missing.json")})
    with pytest.raises(ReplayInputError):
        run_replay_from_config(cfg, tmp_path / "out")
    manifest_path = next((tmp_path / "out/attempts").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    storage = RunStorage(tmp_path / "out")
    record = storage.load_record(manifest["run_id"])
    storage.close()
    assert record.status == "failed"
    assert record.data_hash.startswith("unavailable:")
    assert (manifest_path.parent / "error.json").exists()
    assert manifest["complete"] is False


def test_recorded_observations_without_receipt_evidence_fail_truthfully(tmp_path):
    document = json.loads(Path(config().input).read_text())
    document["events"][0].pop("received_at")
    with pytest.raises(ReplayInputError, match="received_at"):
        run_replay_from_config(input_config(tmp_path, document), tmp_path / "out")
    manifest = json.loads(
        next((tmp_path / "out/attempts").glob("*/manifest.json")).read_text()
    )
    assert manifest["complete"] is False


def test_source_inventory_detects_truncation_even_with_a_closing_timer(tmp_path):
    document = json.loads(Path(config().input).read_text())
    document["events"] = [e for e in document["events"] if e["event_id"] != "T4"]
    result = run_replay_from_config(input_config(tmp_path, document), tmp_path / "out")
    assert not result.complete and "inventory" in result.incomplete_reason


def test_source_inventory_counts_repeated_ids_instead_of_collapsing_them(tmp_path):
    document = json.loads(Path(config().input).read_text())
    document["coverage"]["expected_event_ids"].append("T4")
    result = run_replay_from_config(input_config(tmp_path, document), tmp_path / "out")
    assert not result.complete and "inventory" in result.incomplete_reason


def test_verified_quiet_minute_completes_without_fabricating_a_bar(tmp_path):
    cfg = config()
    document = {
        "events": [],
        "coverage": {
            "expected_event_ids": [],
            "available_through": cfg.coverage.available_through_at,
            "intervals": [
                {
                    "instrument_id": "AAA",
                    "bar_start": cfg.coverage.window_start_at,
                    "state": "verified_no_trade",
                }
            ],
        },
    }
    result = run_replay_from_config(input_config(tmp_path, document), tmp_path / "out")
    assert result.complete
    rows = [json.loads(line) for line in result.trace_path.read_text().splitlines()]
    assert any(r["internal"] and r["phase"] == 20 for r in rows)
    assert not any(r.get("closures") for r in rows)


@pytest.mark.parametrize(
    "mode,observer,capability,cadence,threshold",
    [
        ("trade", "synthetic_threshold:v1", "intrabar", "availability_batch", "101"),
        (
            "trade",
            "synthetic_closed_recorder:v1",
            "closed_only",
            "completed_bar_boundary",
            None,
        ),
        (
            "bar",
            "synthetic_closed_recorder:v1",
            "closed_only",
            "completed_bar_boundary",
            None,
        ),
    ],
)
def test_real_cli_dispatches_named_synthetic_observers(
    tmp_path, mode, observer, capability, cadence, threshold
):
    cfg = config(mode).model_dump(mode="json")
    cfg["observation"] = {
        "mode": capability,
        "cadence": cadence,
        "observer_id": observer,
        "threshold": threshold,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    assert main(["replay", "--config", str(path), "--out", str(tmp_path / "out")]) == 0
    trace = next((tmp_path / "out/attempts").glob("*/trace.jsonl"))
    evaluations = [
        json.loads(line)
        for line in trace.read_text().splitlines()
        if json.loads(line)["kind"] == "observation"
    ]
    assert evaluations
    assert all(e["phase"] == 40 for e in evaluations)
    emitted = [e for e in evaluations if e["result"] is not None]
    assert emitted and all(e["input_versions"] for e in emitted)
    if capability == "intrabar":
        assert emitted[0]["result"]["close"] == "102"
        assert any(e["result"] is None for e in evaluations)
    else:
        assert len(evaluations) == (2 if mode == "bar" else 1)


def test_config_relative_input_works_from_another_working_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    result = run_replay_from_config(config(), tmp_path / "out")
    assert result.complete


@pytest.mark.parametrize(
    "update",
    [
        {
            "quotes": {
                "enabled": True,
                "freshness_limit_ms": 1000,
                "quality_policy": "unknown:v1",
            }
        },
        {
            "replay": {
                "input_mode": "historical_trade",
                "ordering_profile": "trade_replay_v1",
                "availability_model": "modeled_availability",
            }
        },
    ],
)
def test_unknown_policy_or_undeclared_modeled_delay_is_rejected(update):
    raw = load_raw(ROOT / "configs/replay_trade_fixture.yaml")
    raw.update(update)
    with pytest.raises(ConfigError):
        parse_config(raw)


def test_modeled_delay_is_enforced_and_cannot_be_labeled_recorded(tmp_path):
    raw = config().model_dump(mode="json")
    raw["replay"]["availability_model"] = "modeled_availability"
    raw["replay"]["modeled_delay_ms"] = 0
    cfg = parse_config(raw)
    document = json.loads(Path(cfg.input).read_text())
    with pytest.raises(ReplayInputError, match="modeled delay"):
        run_replay_from_config(input_config(tmp_path, document, cfg), tmp_path / "out")


def test_incomplete_cli_uses_data_error_exit_code(tmp_path):
    cfg = config().model_dump(mode="json")
    cfg["input"] = str(ROOT / "fixtures/replay_truncated_fixture_events.json")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    assert (
        main(["replay", "--config", str(path), "--out", str(tmp_path / "out")])
        == EXIT_DATA_ERROR
    )


def test_invalid_event_error_does_not_persist_submitted_secret_values(tmp_path):
    document = json.loads(Path(config().input).read_text())
    document["events"][0]["price"] = "CANARY_REPLAY_INPUT_SECRET_198732"
    with pytest.raises(ReplayInputError):
        run_replay_from_config(input_config(tmp_path, document), tmp_path / "out")
    db = (tmp_path / "out/runs.db").read_bytes()
    assert b"CANARY_REPLAY_INPUT_SECRET_198732" not in db
    for path in (tmp_path / "out/attempts").glob("*/*.json"):
        assert "CANARY_REPLAY_INPUT_SECRET_198732" not in path.read_text()


def test_provider_split_straddle_retains_non_success_instead_of_mixed_units(tmp_path):
    cfg = config("bar")
    document = json.loads(Path(cfg.input).read_text())
    event = {
        "kind": "corporate_action",
        "event_id": "split",
        "instrument_id": "AAA",
        "provider": "alpaca",
        "feed": "fixture",
        "event_time_meaning": "provider_market_time",
        "session_version": "nyse-2024-v1",
        "quality_state": "ok",
        "timing_label": "recorded",
        "event_time": "2024-06-10T13:30:30Z",
        "available_at": "2024-06-10T13:30:50Z",
        "received_at": "2024-06-10T13:30:50Z",
        "effective_at": "2024-06-10T13:30:30Z",
        "known_at": "2024-06-10T13:30:30Z",
        "action_type": "split",
        "ratio": "2",
    }
    document["events"].append(event)
    document["coverage"]["expected_event_ids"].append("split")
    result = run_replay_from_config(
        input_config(tmp_path, document, cfg), tmp_path / "out"
    )
    assert not result.complete and "unit transition" in result.incomplete_reason


def test_explicit_modeled_timing_can_run_when_every_external_event_agrees(tmp_path):
    cfg = config("bar").model_dump(mode="json")
    cfg["replay"]["availability_model"] = "modeled_availability"
    cfg["replay"]["modeled_delay_ms"] = 0  # declared synthetic idealization
    document = json.loads(Path(cfg["input"]).read_text())
    for event in document["events"]:
        if event["kind"] != "timer":
            event.pop("received_at")
            event["timing_label"] = "modeled"
    result = run_replay_from_config(
        input_config(tmp_path, document, parse_config(cfg)), tmp_path / "out"
    )
    assert result.complete
    assert (
        json.loads(result.manifest_path.read_text())["timing"]["modeled_delay_ms"] == 0
    )


@pytest.mark.parametrize(
    "start,end",
    [
        ("2024-03-08T14:30:00Z", "2024-03-08T14:31:00Z"),
        ("2024-03-11T13:30:00Z", "2024-03-11T13:31:00Z"),
        ("2024-11-01T13:30:00Z", "2024-11-01T13:31:00Z"),
        ("2024-11-04T14:30:00Z", "2024-11-04T14:31:00Z"),
        ("2024-11-29T17:59:00Z", "2024-11-29T18:00:00Z"),
    ],
)
def test_declared_replay_windows_follow_dst_and_early_close(start, end):
    raw = config().model_dump(mode="json")
    raw["coverage"].update(
        window_start_at=start, required_boundary_at=end, available_through_at=end
    )
    parse_config(raw)


def test_outside_session_and_unknown_calendar_dates_are_rejected():
    for start, end in [
        ("2024-11-29T18:00:00Z", "2024-11-29T18:01:00Z"),
        ("2025-06-10T13:30:00Z", "2025-06-10T13:31:00Z"),
    ]:
        raw = config().model_dump(mode="json")
        raw["coverage"].update(
            window_start_at=start, required_boundary_at=end, available_through_at=end
        )
        with pytest.raises(ConfigError):
            parse_config(raw)

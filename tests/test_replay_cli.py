"""R3-C: the `rsys replay --config PATH --out auto` CLI command and its JSONL trace output."""
import json
from pathlib import Path

import yaml

from rsys.cli import EXIT_DATA_ERROR, EXIT_SUCCESS, main

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_BAR_CONFIG = REPO_ROOT / "configs" / "replay_bar_fixture.yaml"
REPLAY_TRADE_CONFIG = REPO_ROOT / "configs" / "replay_trade_fixture.yaml"
TRUNCATED_EVENTS = REPO_ROOT / "fixtures" / "replay_truncated_fixture_events.json"


def _single_trace_path(out_dir: Path) -> Path:
    """Each test uses a fresh attempts root with exactly one run_id subdirectory."""
    return next((out_dir / "attempts").glob("*/trace.jsonl"))


def _single_manifest_path(out_dir: Path) -> Path:
    return next((out_dir / "attempts").glob("*/manifest.json"))


def test_replay_cli_succeeds_for_the_bar_fixture_config(tmp_path):
    out_dir = tmp_path / "out"
    assert main(["replay", "--config", str(REPLAY_BAR_CONFIG), "--out", str(out_dir)]) == EXIT_SUCCESS
    assert _single_trace_path(out_dir).exists()
    assert _single_manifest_path(out_dir).exists()
    assert (out_dir / "runs.db").exists()  # IR-05: a real retained run-evidence database


def test_replay_cli_succeeds_for_the_trade_fixture_config(tmp_path):
    out_dir = tmp_path / "out"
    assert main(["replay", "--config", str(REPLAY_TRADE_CONFIG), "--out", str(out_dir)]) == EXIT_SUCCESS
    assert _single_trace_path(out_dir).exists()


def test_replay_cli_trace_matches_the_worked_example_table(tmp_path):
    out_dir = tmp_path / "out"
    main(["replay", "--config", str(REPLAY_TRADE_CONFIG), "--out", str(out_dir)])
    lines = _single_trace_path(out_dir).read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]

    t4_record = next(r for r in records if r["event_id"] == "T4")
    assert t4_record["bar_version"]["close"] == "101"
    assert t4_record["bar_version"]["volume"] == "45"
    assert t4_record["bar_version"]["status"] == "forming"

    closure_record = next(r for r in records if r.get("internal") and "closures" in r)
    assert closure_record["phase"] == 20
    assert closure_record["closures"]["AAA"][0]["status"] == "closed_as_of"
    assert closure_record["closures"]["AAA"][0]["close"] == "101"

    final_record = next(r for r in records if r["event_id"] == "C5")
    assert final_record["bar_version"]["close"] == "101"
    assert final_record["bar_version"]["volume"] == "25"
    assert final_record["bar_version"]["trade_count"] == 3


# ---- IR-05 / R3I-05: distinct retained attempts, never a reused/overwritten directory ----

def test_repeated_replay_runs_produce_identical_semantic_trace_hashes_but_distinct_run_ids(tmp_path):
    """R3-C/IR-05: fresh run IDs/attempt directories/wall-clock each time, but identical
    semantic trace hashes for identical inputs/config/code -- and BOTH attempts remain
    retained and readable afterward (R3I-05: `--out auto` must never reuse a fixed directory)."""
    from rsys.config import load_config
    from rsys.replay_io import run_replay_from_config

    config = load_config(REPLAY_TRADE_CONFIG)
    out_dir_1 = tmp_path / "run1"
    out_dir_2 = tmp_path / "run2"
    result_1 = run_replay_from_config(config, out_dir_1)
    result_2 = run_replay_from_config(config, out_dir_2)

    assert result_1.run_id != result_2.run_id
    assert result_1.trace_path != result_2.trace_path
    assert result_1.semantic_hash == result_2.semantic_hash

    # Running a SECOND time into the SAME attempts root also yields a distinct attempt,
    # and the first attempt's files are untouched.
    first_trace_bytes = result_1.trace_path.read_bytes()
    result_3 = run_replay_from_config(config, out_dir_1)
    assert result_3.run_id != result_1.run_id
    assert result_3.trace_path != result_1.trace_path
    assert result_1.trace_path.read_bytes() == first_trace_bytes  # untouched by the later run
    assert result_1.trace_path.exists() and result_3.trace_path.exists()  # both retained


def test_reversed_input_order_yields_the_same_semantic_trace_hash(tmp_path):
    """Acceptance (section 6): reverse input file/page order with event identities and source
    metadata unchanged; trace content remains identical."""
    from rsys.config import load_config
    from rsys.replay_io import load_events_from_json, run_replay_from_config

    config = load_config(REPLAY_TRADE_CONFIG)
    events = load_events_from_json(config.input)

    forward_dir = tmp_path / "forward"
    reversed_dir = tmp_path / "reversed"

    forward_result = run_replay_from_config(config, forward_dir)
    reversed_result = _run_with_reversed_events(config, list(reversed(events)), reversed_dir)
    assert forward_result.semantic_hash == reversed_result.semantic_hash


def _run_with_reversed_events(config, events, out_dir):
    from rsys.replay_io import run_replay_from_config
    # Exercise the exact public path on reversed source records, not a test-only runner.
    data = json.loads(Path(config.input).read_text(encoding="utf-8"))
    data["events"] = list(reversed(data["events"]))
    reversed_input = out_dir.parent / "reversed_input.json"
    reversed_input.write_text(json.dumps(data), encoding="utf-8")
    return run_replay_from_config(config.model_copy(update={"input": str(reversed_input)}), out_dir)


def test_truncated_input_without_a_coverage_timer_is_an_explicit_non_successful_result(tmp_path):
    """Section 7.6/9: incomplete input is a declared non-successful run result, never a crash
    and never silently treated as a successful complete replay."""
    truncated_config_path = tmp_path / "truncated.yaml"
    config_dict = yaml.safe_load(REPLAY_TRADE_CONFIG.read_text(encoding="utf-8"))
    config_dict["input"] = str(TRUNCATED_EVENTS)
    truncated_config_path.write_text(yaml.safe_dump(config_dict), encoding="utf-8")

    out_dir = tmp_path / "out"
    exit_code = main(["replay", "--config", str(truncated_config_path), "--out", str(out_dir)])
    assert exit_code == EXIT_DATA_ERROR
    # The trace is still written (inspectable) even though the run is marked incomplete, and
    # the attempt is still RETAINED in run-evidence (IR-05), not silently dropped.
    assert _single_trace_path(out_dir).exists()
    manifest = json.loads(_single_manifest_path(out_dir).read_text(encoding="utf-8"))
    assert manifest["complete"] is False
    assert manifest["incomplete_reason"] is not None

    from rsys.storage import RunStorage
    storage = RunStorage(out_dir)
    run_id = manifest["run_id"]
    record = storage.load_record(run_id)
    assert record.status == "failed"
    assert record.error is not None
    storage.close()


# ---- IR-05 / R3I-03: a provider-price change must change the trace and its semantic hash ----

def test_provider_price_change_changes_trace_and_semantic_hash(tmp_path):
    from rsys.config import load_config
    from rsys.replay_io import run_replay_from_config

    config = load_config(REPLAY_BAR_CONFIG)
    baseline = run_replay_from_config(config, tmp_path / "baseline")

    data = json.loads(Path(config.input).read_text(encoding="utf-8"))
    for event in data["events"]:
        if event["kind"] == "provider_bar":
            for field in ("open", "high", "low", "close"):
                event[field] = str(int(event[field]) + 100)
    perturbed_input = tmp_path / "perturbed_bar_fixture_events.json"
    perturbed_input.write_text(json.dumps(data), encoding="utf-8")

    perturbed_config = config.model_copy(update={"input": str(perturbed_input)})
    perturbed = run_replay_from_config(perturbed_config, tmp_path / "perturbed")

    assert baseline.semantic_hash != perturbed.semantic_hash
    perturbed_trace = perturbed.trace_path.read_text(encoding="utf-8")
    assert '"open":"200"' in perturbed_trace.replace(" ", "")


# ---- IR-05 / R3I-04: a future quote must never change an earlier quote's trace row ----

def test_quote_trace_prefix_is_unchanged_by_future_quotes(tmp_path):
    from rsys.config import ReplayConfig, load_config
    from rsys.replay_io import run_replay_from_config

    config = load_config(REPLAY_TRADE_CONFIG)
    quote_config = config.model_copy(update={
        "quotes": config.quotes.model_copy(update={
            "enabled": True, "freshness_limit_ms": 2000, "quality_policy": "synthetic_test_quote_policy:v1",
        }),
    })
    assert isinstance(quote_config, ReplayConfig)

    data = json.loads(Path(config.input).read_text(encoding="utf-8"))
    q1 = {
        "kind": "quote", "event_id": "Q1", "instrument_id": "AAA", "provider": "alpaca", "feed": "fixture",
        "event_time_meaning": "provider_market_time", "session_version": "nyse-2024-v1",
        "quality_state": "ok", "timing_label": "recorded",
        "event_time": "2024-06-10T13:30:07Z", "available_at": "2024-06-10T13:30:07Z",
        "bid_price": "99", "bid_size": "1", "ask_price": "101", "ask_size": "1", "venue": "V",
    }
    q2 = {**q1, "event_id": "Q2", "event_time": "2024-06-10T13:30:40Z", "available_at": "2024-06-10T13:30:40Z",
          "bid_price": "150", "ask_price": "152"}
    q1["received_at"] = q1["available_at"]
    q2["received_at"] = q2["available_at"]
    data["events"].extend([q1, q2])
    data["coverage"]["expected_event_ids"] = sorted(e["event_id"] for e in data["events"])
    quote_input = tmp_path / "trade_fixture_with_quotes.json"
    quote_input.write_text(json.dumps(data), encoding="utf-8")
    quote_config = quote_config.model_copy(update={"input": str(quote_input)})

    with_q2 = run_replay_from_config(quote_config, tmp_path / "with_q2")
    with_q2_lines = with_q2.trace_path.read_text(encoding="utf-8").strip().splitlines()
    q1_record_with_q2 = next(json.loads(line) for line in with_q2_lines if json.loads(line)["event_id"] == "Q1")

    data["events"] = [e for e in data["events"] if e.get("event_id") != "Q2"]
    data["events"].append({**q2, "bid_price": "999", "ask_price": "999"})  # perturbed/removed Q2
    no_q2_input = tmp_path / "trade_fixture_without_q2.json"
    no_q2_input.write_text(json.dumps(data), encoding="utf-8")
    quote_config_2 = quote_config.model_copy(update={"input": str(no_q2_input)})
    without_q2 = run_replay_from_config(quote_config_2, tmp_path / "without_q2")
    without_q2_lines = without_q2.trace_path.read_text(encoding="utf-8").strip().splitlines()
    q1_record_without_q2 = next(json.loads(line) for line in without_q2_lines if json.loads(line)["event_id"] == "Q1")

    assert q1_record_with_q2 == q1_record_without_q2
    assert q1_record_with_q2["quote"]["source_event_id"] == "Q1"


# ---- IR-03 / R3I-06: an unrelated later timer must never certify coverage ----

def test_early_unrelated_timer_cannot_complete_truncated_replay(tmp_path):
    """R3I-06 regression: at 7084d4a, a trade at second 5 plus an unrelated heartbeat timer at
    second 6 produced complete=true merely because SOME timer existed in the stream."""
    from rsys.config import load_config
    from rsys.replay_io import run_replay_from_config

    config = load_config(REPLAY_TRADE_CONFIG)
    data = {
        "schema_version": 1, "evidence_type": "synthetic_only",
        "events": [
            {
                "kind": "trade", "event_id": "T1", "instrument_id": "AAA", "provider": "alpaca", "feed": "fixture",
                "event_time_meaning": "provider_market_time", "session_version": "nyse-2024-v1",
                "quality_state": "ok", "timing_label": "recorded",
                "event_time": "2024-06-10T13:30:05Z", "available_at": "2024-06-10T13:30:05Z",
                "price": "100", "size": "10", "trade_id": "T1", "venue": "V", "conditions": ["ALL"],
            },
            {
                # An UNRELATED timer: different boundary_name, arrives shortly after the trade.
                "kind": "timer", "event_id": "heartbeat1", "instrument_id": "AAA",
                "provider": "alpaca", "feed": "fixture", "event_time_meaning": "scheduled_boundary",
                "session_version": "nyse-2024-v1", "quality_state": "ok", "timing_label": "recorded",
                "event_time": "2024-06-10T13:30:06Z", "available_at": "2024-06-10T13:30:06Z",
                "boundary_name": "heartbeat", "calendar_version": "nyse-2024-v1",
            },
        ],
    }
    data["events"][0]["received_at"] = data["events"][0]["available_at"]
    truncated_input = tmp_path / "unrelated_timer_events.json"
    truncated_input.write_text(json.dumps(data), encoding="utf-8")
    truncated_config = config.model_copy(update={"input": str(truncated_input)})

    result = run_replay_from_config(truncated_config, tmp_path / "out")
    assert result.complete is False
    assert result.incomplete_reason is not None

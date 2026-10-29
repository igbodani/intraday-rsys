"""R3-C: the `rsys replay --config PATH --out auto` CLI command and its JSONL trace output."""
import json
from pathlib import Path

import yaml

from rsys.cli import EXIT_OTHER_FAILURE, EXIT_SUCCESS, main

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_BAR_CONFIG = REPO_ROOT / "configs" / "replay_bar_fixture.yaml"
REPLAY_TRADE_CONFIG = REPO_ROOT / "configs" / "replay_trade_fixture.yaml"
TRUNCATED_EVENTS = REPO_ROOT / "fixtures" / "replay_truncated_fixture_events.json"


def test_replay_cli_succeeds_for_the_bar_fixture_config(tmp_path):
    out_dir = tmp_path / "out"
    assert main(["replay", "--config", str(REPLAY_BAR_CONFIG), "--out", str(out_dir)]) == EXIT_SUCCESS
    assert (out_dir / "trace.jsonl").exists()
    assert (out_dir / "manifest.json").exists()


def test_replay_cli_succeeds_for_the_trade_fixture_config(tmp_path):
    out_dir = tmp_path / "out"
    assert main(["replay", "--config", str(REPLAY_TRADE_CONFIG), "--out", str(out_dir)]) == EXIT_SUCCESS
    assert (out_dir / "trace.jsonl").exists()


def test_replay_cli_trace_matches_the_worked_example_table(tmp_path):
    out_dir = tmp_path / "out"
    main(["replay", "--config", str(REPLAY_TRADE_CONFIG), "--out", str(out_dir)])
    lines = (out_dir / "trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]

    t4_record = next(r for r in records if r["event_id"] == "T4")
    assert t4_record["bar_version"]["close"] == "101"
    assert t4_record["bar_version"]["volume"] == "45"
    assert t4_record["bar_version"]["status"] == "forming"

    closure_record = next(r for r in records if r["event_id"] == "close1")
    assert closure_record["closures"]["AAA"][0]["status"] == "closed_as_of"
    assert closure_record["closures"]["AAA"][0]["close"] == "101"

    final_record = next(r for r in records if r["event_id"] == "C5")
    assert final_record["bar_version"]["close"] == "101"
    assert final_record["bar_version"]["volume"] == "25"
    assert final_record["bar_version"]["trade_count"] == 3


def test_repeated_replay_runs_produce_identical_semantic_trace_hashes(tmp_path):
    """R3-C: fresh run IDs/wall-clock each time, but identical semantic trace hashes for
    identical inputs/config/code."""
    out_dir_1 = tmp_path / "run1"
    out_dir_2 = tmp_path / "run2"
    main(["replay", "--config", str(REPLAY_TRADE_CONFIG), "--out", str(out_dir_1)])
    main(["replay", "--config", str(REPLAY_TRADE_CONFIG), "--out", str(out_dir_2)])

    manifest_1 = json.loads((out_dir_1 / "manifest.json").read_text(encoding="utf-8"))
    manifest_2 = json.loads((out_dir_2 / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_1["semantic_trace_hash"] == manifest_2["semantic_trace_hash"]

    trace_1 = (out_dir_1 / "trace.jsonl").read_text(encoding="utf-8")
    trace_2 = (out_dir_2 / "trace.jsonl").read_text(encoding="utf-8")
    assert trace_1 == trace_2


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
    from rsys.data.quotes import QuoteFreshnessPolicy
    from rsys.replay import ReplayProfile
    from rsys.replay_io import (
        _REGISTERED_RULE_SETS,
        ReplayRunResult,
        build_trace_records,
        compute_semantic_hash,
        write_trace_jsonl,
    )
    from rsys.replay_runtime import ReplayRunner, SeriesRouter

    profile = ReplayProfile(config.replay.ordering_profile)
    router = None
    if config.bars.authority == "trade_builder":
        rule_set = _REGISTERED_RULE_SETS[config.bars.rule_set_id]
        quote_policy = QuoteFreshnessPolicy(freshness_limit_ms=config.quotes.freshness_limit_ms) \
            if config.quotes.enabled else None
        router = SeriesRouter(
            events[0].feed, config.interval_policy.interval, rule_set,
            retention_limit=config.revision.retention_limit, quote_freshness_policy=quote_policy,
        )
    runner = ReplayRunner(events, profile, router)
    runner.run_to_completion()
    records = build_trace_records(
        runner, router, observation_mode=config.observation.mode, quotes_enabled=config.quotes.enabled,
    )
    semantic_hash = compute_semantic_hash(records)
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / "trace.jsonl"
    write_trace_jsonl(records, trace_path)
    return ReplayRunResult(trace_path, out_dir / "manifest.json", semantic_hash, True, None, len(runner.processed))


def test_truncated_input_without_a_coverage_timer_is_an_explicit_non_successful_result(tmp_path):
    """Section 7.6/9: incomplete input is a declared non-successful run result, never a crash
    and never silently treated as a successful complete replay."""
    truncated_config_path = tmp_path / "truncated.yaml"
    config_dict = yaml.safe_load(REPLAY_TRADE_CONFIG.read_text(encoding="utf-8"))
    config_dict["input"] = str(TRUNCATED_EVENTS)
    truncated_config_path.write_text(yaml.safe_dump(config_dict), encoding="utf-8")

    out_dir = tmp_path / "out"
    exit_code = main(["replay", "--config", str(truncated_config_path), "--out", str(out_dir)])
    assert exit_code == EXIT_OTHER_FAILURE
    # The trace is still written (inspectable) even though the run is marked incomplete.
    assert (out_dir / "trace.jsonl").exists()
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is False
    assert manifest["incomplete_reason"] is not None

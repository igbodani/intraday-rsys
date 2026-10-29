import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from rsys.cli import EXIT_INVALID_CONFIG, EXIT_SUCCESS, main
from rsys.config import (
    ConfigError,
    load_config,
    load_raw,
    parse_config,
    validate_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CONFIG = REPO_ROOT / "configs" / "fixture.yaml"
BASELINE_CONFIG = REPO_ROOT / "configs" / "baseline_pending.yaml"

# Every promotion_gates leaf the registry tracks (D17); `null` and `{}` must block identically.
PROMOTION_GATE_LEAVES = {
    "promotion_gates.minimum_observations.minimum_evaluation_days",
    "promotion_gates.minimum_observations.minimum_evaluation_trades",
    "promotion_gates.minimum_observations.dependent_observation_treatment",
    "promotion_gates.net_return.net_return_requirement",
    "promotion_gates.net_return.metric_definition",
    "promotion_gates.control_improvement.control_id",
    "promotion_gates.control_improvement.improvement_measure",
    "promotion_gates.drawdown.drawdown_ceiling",
    "promotion_gates.uncertainty.confidence_level",
    "promotion_gates.uncertainty.resampling_design",
    "promotion_gates.uncertainty.acceptance_criterion",
    "promotion_gates.cost_stress.scenarios",
    "promotion_gates.cost_stress.acceptance_criteria",
    "promotion_gates.concentration.limits",
    "promotion_gates.concentration.diagnostics",
    "promotion_gates.paper_observation.forward_duration_days",
    "promotion_gates.paper_observation.sample_requirement",
    "promotion_gates.paper_observation.incident_handling",
}

# The subset of PROMOTION_GATE_LEAVES validated by `_is_method_reference` (IR-04/R3I-09): a
# research gate requires a research-approved specification, and no method is research-approved
# yet, so these leaves remain blocked even in an otherwise fully-populated synthetic profile.
PROMOTION_GATE_METHOD_LEAVES = {
    "promotion_gates.minimum_observations.dependent_observation_treatment",
    "promotion_gates.net_return.metric_definition",
    "promotion_gates.control_improvement.improvement_measure",
    "promotion_gates.uncertainty.resampling_design",
    "promotion_gates.uncertainty.acceptance_criterion",
    "promotion_gates.cost_stress.acceptance_criteria",
    "promotion_gates.concentration.limits",
    "promotion_gates.concentration.diagnostics",
    "promotion_gates.paper_observation.sample_requirement",
    "promotion_gates.paper_observation.incident_handling",
}

EXPECTED_BASELINE_UNRESOLVED = {
    "feed", "data_manifest",
    "account.profile", "account.quantity_step", "account.minimum_quantity", "account.proceeds_availability",
    "risk.fraction_per_trade", "risk.daily_loss_limit_usd", "risk.drawdown_limit_fraction",
    "risk.max_positions", "risk.max_position_notional_usd",
    "execution.spread_bps", "execution.slippage_bps", "execution.fee_profile",
    "execution.feed_delay_ms", "execution.decision_latency_ms",
    "scanner.min_prior_dollar_volume",
    "experiment.development_start", "experiment.development_end",
    "experiment.validation_start", "experiment.validation_end",
    "experiment.final_start", "experiment.final_end",
    "experiment.trial_budget", "experiment.seed",
    "data_adjustment.symbol_mapping_asof",
} | PROMOTION_GATE_LEAVES


def _fixture_dict() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8")))


def _research_dict() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(BASELINE_CONFIG.read_text(encoding="utf-8")))


def _set_path(data: dict[str, Any], dotted_path: str, value: Any) -> dict[str, Any]:
    node = data
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return data


def _fully_populated_synthetic_research_dict() -> dict[str, Any]:
    """Explicitly labeled synthetic test values; never to be reused as market defaults (AGENTS.md)."""
    data = _research_dict()
    overrides = {
        "feed": "synthetic_test_feed",
        "data_manifest": "synthetic_test_manifest_v1",
        "account.profile": "synthetic_test_account",
        "account.quantity_step": "1",
        "account.minimum_quantity": "1",
        "account.proceeds_availability": "synthetic_test_policy",
        "risk.fraction_per_trade": "0.01",
        "risk.daily_loss_limit_usd": "50",
        "risk.drawdown_limit_fraction": "0.2",
        "risk.max_positions": 3,
        "risk.max_position_notional_usd": "500",
        "execution.spread_bps": "1",
        "execution.slippage_bps": "1",
        "execution.fee_profile": "synthetic_test_fees",
        "execution.feed_delay_ms": 0,
        "execution.decision_latency_ms": 0,
        "scanner.min_prior_dollar_volume": "1000000",
        "experiment.development_start": "2024-01-01",
        "experiment.development_end": "2024-06-30",
        "experiment.validation_start": "2024-07-01",
        "experiment.validation_end": "2024-09-30",
        "experiment.final_start": "2024-10-01",
        "experiment.final_end": "2024-12-31",
        "experiment.trial_budget": 10,
        "experiment.seed": 0,
        "data_adjustment.symbol_mapping_asof": "2024-01-01",
    }
    for path, value in overrides.items():
        _set_path(data, path, value)
    data["promotion_gates"] = {
        "minimum_observations": {
            "minimum_evaluation_days": 60,
            "minimum_evaluation_trades": 30,
            "dependent_observation_treatment": "synthetic_test_block_bootstrap:v1",
        },
        "net_return": {"net_return_requirement": "0.05", "metric_definition": "synthetic_test_net_return:v1"},
        "control_improvement": {
            "control_id": "synthetic_test_control", "improvement_measure": "synthetic_test_paired_delta:v1",
        },
        "drawdown": {"drawdown_ceiling": "0.2"},
        "uncertainty": {
            "confidence_level": "0.95",
            "resampling_design": "synthetic_test_block_bootstrap:v1",
            "acceptance_criterion": "synthetic_test_ci_excludes_zero:v1",
        },
        "cost_stress": {
            "scenarios": ["synthetic_test_double_costs"], "acceptance_criteria": "synthetic_test_still_passes:v1",
        },
        "concentration": {
            "limits": "synthetic_test_max_30pct_one_symbol:v1", "diagnostics": "synthetic_test_herfindahl:v1",
        },
        "paper_observation": {
            "forward_duration_days": 30,
            "sample_requirement": "synthetic_test_min_20_trades:v1",
            "incident_handling": "synthetic_test_halt_and_review:v1",
        },
    }
    return data


# ---- Fixture profile ---------------------------------------------------------

def test_fixture_config_has_no_blockers():
    config = load_config(FIXTURE_CONFIG)
    result = validate_config(config)
    assert result.ok
    assert result.blockers == []


def test_fixture_config_rejects_unknown_fields(tmp_path):
    bad = tmp_path / "bad_fixture.yaml"
    bad.write_text(FIXTURE_CONFIG.read_text(encoding="utf-8") + "\nunexpected_field: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("network_allowed", True),
        ("evidence_type", "market"),
        ("initial_cash_default", "0"),
        ("initial_cash_default", "-1"),
        ("quantity_step", "0"),
        ("price_tick", "0"),
        ("spread", "-0.01"),
        ("slippage", "-0.01"),
        ("fees", "-0.01"),
        ("feed_delay_ms", -1),
        ("decision_latency_ms", -1),
        ("schema_version", True),
    ],
)
def test_fixture_invariant_violations_rejected(field, value):
    data = _fixture_dict()
    data[field] = value
    with pytest.raises(ConfigError):
        parse_config(data)


# ---- Input/CLI failure handling ---------------------------------------------

def test_malformed_yaml_is_controlled_failure(tmp_path):
    bad = tmp_path / "malformed.yaml"
    bad.write_text("mode: fixture\n  bad_indent: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_raw(bad)


def test_malformed_yaml_does_not_leak_fake_secret(tmp_path):
    bad = tmp_path / "malformed_secret.yaml"
    bad.write_text("mode: fixture\napi_key: FAKE_SECRET_CANARY\n  bad_indent: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_raw(bad)
    assert "FAKE_SECRET_CANARY" not in str(exc_info.value)


@pytest.mark.parametrize("mode", [None, 123, ["fixture"], {"mode": "fixture"}, "unknown_mode"])
def test_unsupported_or_malformed_mode_is_controlled_failure(mode):
    with pytest.raises(ConfigError):
        parse_config({"mode": mode})


def test_unknown_field_error_does_not_leak_fake_secret():
    data = _fixture_dict()
    data["unexpected_field"] = "FAKE_SECRET_CANARY"
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data)
    assert "FAKE_SECRET_CANARY" not in str(exc_info.value)


def test_missing_file_is_controlled_failure():
    with pytest.raises(ConfigError):
        load_config("does/not/exist.yaml")


def test_cli_validate_exits_zero_for_fixture_config():
    assert main(["validate", "--config", str(FIXTURE_CONFIG)]) == EXIT_SUCCESS


def test_cli_validate_exits_two_for_draft_market_config():
    assert main(["validate", "--config", str(BASELINE_CONFIG)]) == EXIT_INVALID_CONFIG


def test_cli_validate_exits_two_for_missing_file():
    assert main(["validate", "--config", "does/not/exist.yaml"]) == EXIT_INVALID_CONFIG


# ---- Research profile: supported-value / structural enforcement -------------

@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("capital_usd", "-1000"),
        ("capital_usd", "0"),
        ("side", "short"),
        ("provider", "ibkr"),
        ("acceptance", "discretionary"),
        ("data_session", "premarket"),
        ("trading_session", "extended"),
        ("scanner.id", "custom_scanner_v2"),
        ("strategy.id", "vwap_touch_hold_v1"),
        ("execution.ambiguous_exit", "target_first"),
        ("data_adjustment.execution", "adjusted"),
        ("data_adjustment.historical_features", "raw"),
        ("strategy.entry_cutoff_local", "25:99"),
        ("strategy.entry_cutoff_local", "11am"),
        ("schema_version", True),
    ],
)
def test_research_structural_violations_rejected(path, value):
    data = _research_dict()
    _set_path(data, path, value)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_lookback_cap_below_lookback_sessions_rejected():
    data = _research_dict()
    _set_path(data, "scanner.max_lookback_calendar_sessions", 5)
    assert data["scanner"]["lookback_sessions"] == 20
    with pytest.raises(ConfigError):
        parse_config(data)


def test_lookback_cap_equal_to_lookback_sessions_accepted():
    data = _research_dict()
    _set_path(data, "scanner.max_lookback_calendar_sessions", 20)
    parse_config(data)  # must not raise


@pytest.mark.parametrize("bad_date", ["not-a-date", "2024-13-01", "2024/01/01", ""])
def test_malformed_experiment_date_rejected(bad_date):
    data = _research_dict()
    _set_path(data, "experiment.development_start", bad_date)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_overlapping_experiment_intervals_rejected():
    data = _research_dict()
    data["experiment"].update(
        {
            "development_start": "2024-01-01",
            "development_end": "2024-07-01",
            "validation_start": "2024-06-01",  # overlaps development
            "validation_end": "2024-09-30",
            "final_start": "2024-10-01",
            "final_end": "2024-12-31",
        }
    )
    with pytest.raises(ConfigError):
        parse_config(data)


def test_chronological_nonoverlapping_experiment_intervals_accepted():
    data = _research_dict()
    data["experiment"].update(
        {
            "development_start": "2024-01-01",
            "development_end": "2024-06-30",
            "validation_start": "2024-07-01",
            "validation_end": "2024-09-30",
            "final_start": "2024-10-01",
            "final_end": "2024-12-31",
        }
    )
    parse_config(data)  # must not raise


def test_partial_experiment_dates_leave_named_blockers_not_format_errors():
    data = _research_dict()
    _set_path(data, "experiment.development_start", "2024-01-01")
    config = parse_config(data)  # must not raise: incomplete group, not a format/ordering error
    result = validate_config(config)
    assert "experiment.development_end" in {b.field_path for b in result.blockers}


# ---- Research profile: numeric-domain blockers on resolved open fields ------

def test_zero_trial_budget_is_blocked():
    data = _research_dict()
    _set_path(data, "experiment.trial_budget", 0)
    config = parse_config(data)
    result = validate_config(config)
    blocker = next(b for b in result.blockers if b.field_path == "experiment.trial_budget")
    assert blocker.decision_id == "D16"


def test_out_of_range_risk_fraction_is_blocked():
    data = _research_dict()
    _set_path(data, "risk.fraction_per_trade", "1.5")
    config = parse_config(data)
    result = validate_config(config)
    blocker = next(b for b in result.blockers if b.field_path == "risk.fraction_per_trade")
    assert blocker.decision_id == "D10"


def test_zero_max_positions_is_blocked():
    data = _research_dict()
    _set_path(data, "risk.max_positions", 0)
    config = parse_config(data)
    result = validate_config(config)
    assert any(b.field_path == "risk.max_positions" for b in result.blockers)


def test_negative_quantity_step_is_blocked():
    data = _research_dict()
    _set_path(data, "account.quantity_step", "-1")
    config = parse_config(data)
    result = validate_config(config)
    assert any(b.field_path == "account.quantity_step" for b in result.blockers)


def test_zero_seed_is_not_blocked():
    """A seed of 0 is a legal declared seed; only its presence matters, not its value."""
    data = _research_dict()
    _set_path(data, "experiment.seed", 0)
    config = parse_config(data)
    result = validate_config(config)
    assert "experiment.seed" not in {b.field_path for b in result.blockers}


# ---- promotion_gates structural meaningfulness (D17) -------------------------

def test_promotion_gates_empty_object_blocks_same_as_null():
    data = _research_dict()
    data["promotion_gates"] = {}
    config = parse_config(data)
    result = validate_config(config)
    blocked = {b.field_path for b in result.blockers}
    assert PROMOTION_GATE_LEAVES <= blocked


def test_promotion_gates_partial_group_only_blocks_missing_leaves():
    data = _research_dict()
    data["promotion_gates"] = {
        "minimum_observations": {
            "minimum_evaluation_days": 60,
            "minimum_evaluation_trades": 30,
            "dependent_observation_treatment": "synthetic_test_block_bootstrap:v1",
        }
    }
    config = parse_config(data)
    result = validate_config(config)
    blocked = {b.field_path for b in result.blockers}
    assert "promotion_gates.minimum_observations.minimum_evaluation_days" not in blocked
    assert "promotion_gates.minimum_observations.minimum_evaluation_trades" not in blocked
    # A fixture-only method still blocks a research gate (IR-04/R3I-09), independent of this
    # test's actual point: a present/well-formed sibling leaf is not ALSO blocked merely
    # because the group is only partially filled.
    assert "promotion_gates.minimum_observations.dependent_observation_treatment" in blocked
    assert "promotion_gates.drawdown.drawdown_ceiling" in blocked


def test_promotion_gates_unknown_field_rejected():
    data = _research_dict()
    data["promotion_gates"] = {"minimum_observations": {"unexpected": 1}}
    with pytest.raises(ConfigError):
        parse_config(data)


# ---- Baseline draft profile: exact blocker set -------------------------------

def test_baseline_pending_config_blocks_every_unresolved_field():
    config = load_config(BASELINE_CONFIG)
    result = validate_config(config)
    assert not result.ok

    blocked_paths = {b.field_path for b in result.blockers}
    assert blocked_paths == EXPECTED_BASELINE_UNRESOLVED
    assert all(b.decision_id for b in result.blockers)


# ---- Positive case: fully populated synthetic profile passes, except unapproved gate methods ----

def test_fully_populated_synthetic_research_profile_has_no_blockers_except_unapproved_gate_methods():
    """Proves validation isn't blanket rejection; synthetic values here are test-only (AGENTS.md).
    The gate-method leaves remain blocked (IR-04/R3I-09): a fixture-only method is never
    research-approved evidence, regardless of how fully the rest of the profile is populated."""
    config = parse_config(_fully_populated_synthetic_research_dict())
    result = validate_config(config)
    assert not result.ok
    assert {b.field_path for b in result.blockers} == PROMOTION_GATE_METHOD_LEAVES


# ---- H1 regression: implemented schema versions only -------------------------

def test_unimplemented_schema_version_rejected():
    data = _fixture_dict()
    data["schema_version"] = 999
    with pytest.raises(ConfigError):
        parse_config(data)


def test_unimplemented_schema_version_rejected_for_research():
    data = _research_dict()
    data["schema_version"] = 999
    with pytest.raises(ConfigError):
        parse_config(data)


# ---- H1 regression: booleans never satisfy a numeric field -------------------

@pytest.mark.parametrize(
    "path",
    [
        "risk.max_positions", "experiment.trial_budget", "experiment.seed",
        "scanner.top_n", "scanner.lookback_sessions", "scanner.max_lookback_calendar_sessions",
        "account.quantity_step", "account.minimum_quantity", "risk.fraction_per_trade",
        "execution.spread_bps", "execution.feed_delay_ms", "capital_usd",
    ],
)
def test_boolean_never_satisfies_a_numeric_field(path):
    data = _research_dict()
    _set_path(data, path, True)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_boolean_never_satisfies_fixture_numeric_field():
    data = _fixture_dict()
    data["feed_delay_ms"] = False
    with pytest.raises(ConfigError):
        parse_config(data)


def test_boolean_does_not_satisfy_promotion_gate_numeric_leaf():
    data = _research_dict()
    data["promotion_gates"] = {"minimum_observations": {"minimum_evaluation_days": True}}
    with pytest.raises(ConfigError):
        parse_config(data)


def test_zero_is_still_a_legal_seed_and_zero_cost():
    data = _fully_populated_synthetic_research_dict()
    _set_path(data, "experiment.seed", 0)
    _set_path(data, "execution.feed_delay_ms", 0)
    config = parse_config(data)
    result = validate_config(config)
    blocked = {b.field_path for b in result.blockers}
    assert "experiment.seed" not in blocked
    assert "execution.feed_delay_ms" not in blocked


# ---- H1 regression: offset-free local cutoff time ----------------------------

@pytest.mark.parametrize("bad_cutoff", ["11:00+05:00", "11:00Z", "11am", "9:30", "11:00:00"])
def test_offset_bearing_or_malformed_entry_cutoff_rejected(bad_cutoff):
    data = _research_dict()
    _set_path(data, "strategy.entry_cutoff_local", bad_cutoff)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_well_formed_entry_cutoff_accepted():
    data = _research_dict()
    _set_path(data, "strategy.entry_cutoff_local", "11:00")
    parse_config(data)  # must not raise


# ---- H1 regression: partial-pair date-ordering enforcement -------------------

def test_partial_reversed_development_interval_rejected_even_with_other_dates_missing():
    data = _research_dict()
    data["experiment"]["development_start"] = "2024-06-01"
    data["experiment"]["development_end"] = "2024-01-01"  # reversed; validation/final still missing
    with pytest.raises(ConfigError):
        parse_config(data)


def test_partial_adjacent_pair_overlap_rejected_even_with_other_dates_missing():
    data = _research_dict()
    data["experiment"]["development_end"] = "2024-06-01"
    data["experiment"]["validation_start"] = "2024-06-01"  # touching, not strictly after
    with pytest.raises(ConfigError):
        parse_config(data)


# ---- H1 regression: blank required identifiers / blank list elements --------

def test_blank_required_identifier_is_blocked_not_silently_resolved():
    data = _research_dict()
    _set_path(data, "feed", "   ")
    config = parse_config(data)
    result = validate_config(config)
    assert any(b.field_path == "feed" for b in result.blockers)


def test_blank_list_element_in_cost_stress_scenarios_is_blocked():
    data = _research_dict()
    data["promotion_gates"] = {"cost_stress": {"scenarios": ["double_costs", "   "]}}
    config = parse_config(data)
    result = validate_config(config)
    assert any(b.field_path == "promotion_gates.cost_stress.scenarios" for b in result.blockers)


# ---- H1 regression: gate method references must be declared and versioned ---

def test_placeholder_method_string_is_insufficient_for_a_gate_method_field():
    data = _research_dict()
    data["promotion_gates"] = {"uncertainty": {"resampling_design": "defined"}}
    config = parse_config(data)
    result = validate_config(config)
    assert any(b.field_path == "promotion_gates.uncertainty.resampling_design" for b in result.blockers)


def test_fixture_only_method_reference_never_satisfies_a_research_gate():
    """R3I-09 regression: at 7084d4a, `synthetic_test_net_return:v1` (registered as
    `fixture_only`) satisfied a research gate field. A research gate requires a
    research-approved specification; no method is research-approved yet, so every declared
    fixture-only reference must still block a research profile, however well-formed."""
    data = _fully_populated_synthetic_research_dict()
    config = parse_config(data)
    result = validate_config(config)
    assert "promotion_gates.uncertainty.resampling_design" in {b.field_path for b in result.blockers}


def test_registered_fixture_only_method_resolves_in_the_registry_itself():
    """A separate, legitimate synthetic-registry test (IR-04): `resolve_method` recognizes a
    declared fixture-only method as REGISTERED (useful for e.g. a replay fixture's own
    `bars.rule_set_id`), which is a different question from whether it may satisfy a research
    gate (it may not -- see the regression above)."""
    from rsys.registry import METHOD_REGISTRY, resolve_method

    spec = resolve_method("synthetic_test_net_return:v1")
    assert spec is not None
    assert spec.evidence_class == "fixture_only"
    assert METHOD_REGISTRY["synthetic_test_net_return:v1"] is spec


def test_syntactically_valid_but_unregistered_method_reference_is_rejected():
    """C08 regression: 'nonexistent_method:v999' matches the name:vN pattern but was never
    registered, so a naming convention alone must not resolve a declared method."""
    data = _research_dict()
    data["promotion_gates"] = {"uncertainty": {"resampling_design": "nonexistent_method:v999"}}
    config = parse_config(data)
    result = validate_config(config)
    assert any(b.field_path == "promotion_gates.uncertainty.resampling_design" for b in result.blockers)


# ---- H1 regression: controlled decoding failures and safe error messages ----

def test_invalid_utf8_file_is_a_controlled_failure(tmp_path):
    bad = tmp_path / "invalid_utf8.yaml"
    bad.write_bytes(b"mode: fixture\n\xff\xfe\x00bad_bytes")
    with pytest.raises(ConfigError):
        load_raw(bad)


def test_custom_validator_error_does_not_leak_the_submitted_value():
    data = _fixture_dict()
    data["initial_cash_default"] = "FAKE_SECRET_CANARY"
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data)
    assert "FAKE_SECRET_CANARY" not in str(exc_info.value)


def test_registry_blocker_reason_does_not_leak_the_resolved_value():
    data = _research_dict()
    _set_path(data, "risk.fraction_per_trade", "FAKE_SECRET_CANARY_NOT_A_NUMBER")
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data)
    # Decimal parsing itself rejects a non-numeric string before the registry ever sees it;
    # the pydantic type error must still not echo the raw value.
    assert "FAKE_SECRET_CANARY_NOT_A_NUMBER" not in str(exc_info.value)


# ---- R3-A: replay profile settings (docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md 7.1) -----

REPLAY_BAR_CONFIG = REPO_ROOT / "configs" / "replay_bar_fixture.yaml"
REPLAY_TRADE_CONFIG = REPO_ROOT / "configs" / "replay_trade_fixture.yaml"


def _replay_dict() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(REPLAY_BAR_CONFIG.read_text(encoding="utf-8")))


def test_replay_bar_fixture_config_has_no_blockers():
    config = load_config(REPLAY_BAR_CONFIG)
    result = validate_config(config)
    assert result.ok
    assert result.blockers == []


def test_replay_trade_fixture_config_has_no_blockers():
    config = load_config(REPLAY_TRADE_CONFIG)
    result = validate_config(config)
    assert result.ok
    assert result.blockers == []


def test_a_legacy_fixture_config_is_not_accepted_as_a_replay_config():
    """A legacy fixture need not become a replay fixture: switching `mode` to `replay` must not
    let a fixture's own fields silently satisfy the replay profile's required fields."""
    data = _fixture_dict()
    data["mode"] = "replay"
    with pytest.raises(ConfigError):
        parse_config(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("replay.input_mode", "streaming_live"),
        ("replay.ordering_profile", "bar_v2"),
        ("replay.availability_model", "captured_live"),
        ("bars.authority", "both"),
        ("interval_policy.session", "premarket"),
        ("interval_policy.unit_policy", "adjusted"),
        ("revision.revision_policy", "recompute_current"),
        ("observation.mode", "per_trade"),
        ("execution.data_basis", "bar_close"),
    ],
)
def test_replay_unsupported_setting_value_rejected(path, value):
    data = _replay_dict()
    _set_path(data, path, value)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_replay_closed_only_observation_requires_completed_bar_boundary_cadence():
    data = _replay_dict()
    _set_path(data, "observation.mode", "closed_only")
    _set_path(data, "observation.cadence", "availability_batch")
    with pytest.raises(ConfigError):
        parse_config(data)


def test_replay_enabled_quotes_without_a_freshness_policy_is_rejected():
    data = _replay_dict()
    _set_path(data, "quotes.enabled", True)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_replay_enabled_quotes_with_explicit_policy_is_accepted():
    data = _replay_dict()
    data["quotes"] = {"enabled": True, "freshness_limit_ms": 500, "quality_policy": "synthetic_test_quote_policy:v1"}
    data["replay"]["input_mode"] = "recorded_event"
    data["replay"]["ordering_profile"] = "trade_replay_v1"
    parse_config(data)  # must not raise


def test_replay_disabled_quotes_is_a_complete_supported_configuration():
    data = _replay_dict()
    assert data["quotes"] == {"enabled": False}
    config = parse_config(data)
    assert config.quotes.enabled is False


def test_replay_unknown_field_rejected():
    data = _replay_dict()
    data["unexpected_field"] = 1
    with pytest.raises(ConfigError):
        parse_config(data)


def test_replay_unimplemented_schema_version_rejected():
    data = _replay_dict()
    data["schema_version"] = 999
    with pytest.raises(ConfigError):
        parse_config(data)


def test_replay_network_allowed_must_be_false():
    data = _replay_dict()
    data["network_allowed"] = True
    with pytest.raises(ConfigError):
        parse_config(data)


def test_replay_retention_limit_must_be_positive_when_set():
    data = _replay_dict()
    _set_path(data, "revision.retention_limit", 0)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_cli_validate_exits_zero_for_both_replay_fixture_configs():
    assert main(["validate", "--config", str(REPLAY_BAR_CONFIG)]) == EXIT_SUCCESS
    assert main(["validate", "--config", str(REPLAY_TRADE_CONFIG)]) == EXIT_SUCCESS


# ---- IR-04 / R3I-08: input_mode/authority must describe the same actual data -----------

def test_completed_bar_input_mode_rejects_trade_builder_authority():
    """A completed-bar input stream has no trades to build from; the two settings must agree."""
    data = _replay_dict()  # replay.input_mode=completed_bar, bars.authority=provider_publication
    _set_path(data, "bars.authority", "trade_builder")
    with pytest.raises(ConfigError):
        parse_config(data)


def test_historical_trade_input_mode_rejects_provider_publication_authority():
    data = copy.deepcopy(yaml.safe_load(REPLAY_TRADE_CONFIG.read_text(encoding="utf-8")))
    assert data["replay"]["input_mode"] == "historical_trade"
    _set_path(data, "bars.authority", "provider_publication")
    with pytest.raises(ConfigError):
        parse_config(data)



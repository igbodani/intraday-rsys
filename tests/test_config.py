from pathlib import Path

import pytest

from rsys.cli import EXIT_INVALID_CONFIG, EXIT_SUCCESS, main
from rsys.config import ConfigError, load_config, validate_config

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CONFIG = REPO_ROOT / "configs" / "fixture.yaml"
BASELINE_CONFIG = REPO_ROOT / "configs" / "baseline_pending.yaml"


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


def test_baseline_pending_config_blocks_every_unresolved_field():
    config = load_config(BASELINE_CONFIG)
    result = validate_config(config)
    assert not result.ok

    expected_unresolved = {
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
        "promotion_gates",
        "data_adjustment.symbol_mapping_asof",
    }
    blocked_paths = {b.field_path for b in result.blockers}
    assert blocked_paths == expected_unresolved
    assert all(b.decision_id for b in result.blockers)


def test_cli_validate_exits_zero_for_fixture_config():
    assert main(["validate", "--config", str(FIXTURE_CONFIG)]) == EXIT_SUCCESS


def test_cli_validate_exits_two_for_draft_market_config():
    assert main(["validate", "--config", str(BASELINE_CONFIG)]) == EXIT_INVALID_CONFIG


def test_cli_validate_exits_two_for_missing_file():
    assert main(["validate", "--config", "does/not/exist.yaml"]) == EXIT_INVALID_CONFIG

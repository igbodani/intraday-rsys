"""Never selected by default; real historical data only after explicit local opt-in."""

import os
from pathlib import Path

import pytest

from rsys.application.provider_validation import validate_sample


@pytest.mark.alpaca_integration
def test_owner_enabled_alpaca_sample(request):
    if request.config.getoption("markexpr").strip() != "alpaca_integration":
        pytest.fail("Select this real-data check explicitly with -m alpaca_integration")
    if os.environ.get("RSYS_ENABLE_ALPACA_VALIDATION") != "1":
        pytest.fail("Set RSYS_ENABLE_ALPACA_VALIDATION=1 for an approved real sample")
    config = os.environ.get("RSYS_ALPACA_SAMPLE_CONFIG")
    out = os.environ.get("RSYS_ALPACA_SAMPLE_OUT")
    if not config or not Path(config).is_file() or not out:
        pytest.fail(
            "Declare local RSYS_ALPACA_SAMPLE_CONFIG and persistent RSYS_ALPACA_SAMPLE_OUT"
        )
    result, manifest = validate_sample(config, out, enable_live=True)
    assert result["status"] == "success", manifest
    # Software execution does not approve actions, arrival fidelity or research.
    assert result["research_status"] == "unscored"
    assert result["real_provider_gate"] in ("not_run", "pending_manual_review")

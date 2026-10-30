import pytest

from rsys.data.bar_rules import FixtureBarRuleSet
from rsys.data.errors import UnknownConditionError
from rsys.data.models import FieldEligibility, TradeClassificationContext

RULES = FixtureBarRuleSet()


def _ctx(*conditions: str, tape: str | None = None, interval: str | None = "1Min") -> TradeClassificationContext:
    return TradeClassificationContext(conditions=conditions, tape=tape, interval=interval)


_FULL = FieldEligibility(open=True, high=True, low=True, close=True, volume=True, count=True, vwap=True)
_VOLUME_ONLY = FieldEligibility(open=False, high=False, low=False, close=False, volume=True, count=True, vwap=False)
_LATE_REPORT = FieldEligibility(open=False, high=False, low=False, close=True, volume=True, count=True, vwap=False)


def test_all_condition_grants_full_eligibility():
    assert RULES.classify(_ctx("ALL")) == _FULL


def test_volume_only_condition_restricts_every_price_field_and_vwap():
    assert RULES.classify(_ctx("VOLUME_ONLY")) == _VOLUME_ONLY


def test_late_report_condition_allows_close_but_not_high_low_or_vwap():
    """C10: a single combined `price` flag cannot express this -- a late-reported print may be
    eligible for close (the provider's declared 'last known' price) without being eligible to
    move the already-set high/low extremes or distort VWAP with an uncertain fill."""
    result = RULES.classify(_ctx("LATE_REPORT"))
    assert result == _LATE_REPORT
    assert result.close is True
    assert result.open is False
    assert result.high is False
    assert result.low is False


def test_empty_conditions_defaults_to_all():
    assert RULES.classify(_ctx()) == _FULL


def test_unknown_condition_is_an_explicit_error():
    with pytest.raises(UnknownConditionError):
        RULES.classify(_ctx("SOME_UNDOCUMENTED_CODE"))


def test_combined_conditions_use_the_most_restrictive_eligibility_per_field():
    # ALL alone would be fully eligible; combined with VOLUME_ONLY, every price field is
    # restricted but volume/count remain eligible.
    assert RULES.classify(_ctx("ALL", "VOLUME_ONLY")) == _VOLUME_ONLY


def test_rule_set_id_is_declared_and_versioned():
    assert RULES.rule_set_id == "fixture_rules_v1"


def test_context_preserves_tape_and_a_condition_that_is_itself_a_meaningful_space():
    """C10: tape and raw condition codes are preserved verbatim; a space is a meaningful
    provider code, not a blank identifier to be stripped."""
    context = TradeClassificationContext(conditions=(" ",), tape="A")
    assert context.conditions == (" ",)
    assert context.tape == "A"

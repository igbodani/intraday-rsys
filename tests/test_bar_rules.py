import pytest

from rsys.data.bar_rules import (
    FieldEligibility,
    FixtureBarRuleSet,
    UnknownConditionError,
)

RULES = FixtureBarRuleSet()


def test_all_condition_grants_full_eligibility():
    assert RULES.classify(("ALL",)) == FieldEligibility(price=True, volume=True, vwap=True)


def test_volume_only_condition_restricts_price_and_vwap():
    assert RULES.classify(("VOLUME_ONLY",)) == FieldEligibility(price=False, volume=True, vwap=False)


def test_empty_conditions_defaults_to_all():
    assert RULES.classify(()) == FieldEligibility(price=True, volume=True, vwap=True)


def test_unknown_condition_is_an_explicit_error():
    with pytest.raises(UnknownConditionError):
        RULES.classify(("SOME_UNDOCUMENTED_CODE",))


def test_combined_conditions_use_the_most_restrictive_eligibility():
    # ALL alone would be fully eligible; combined with VOLUME_ONLY, price/vwap are restricted.
    assert RULES.classify(("ALL", "VOLUME_ONLY")) == FieldEligibility(price=False, volume=True, vwap=False)


def test_rule_set_id_is_declared_and_versioned():
    assert RULES.rule_set_id == "fixture_rules_v1"

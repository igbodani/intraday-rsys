"""Application shared definitions."""

from __future__ import annotations


class ConfigError(Exception):
    """Raised when a config file cannot be parsed or matched to a known profile."""


class UnsupportedRuleSetError(Exception):
    """`bars.rule_set_id` does not resolve to a registered rule set; fixture-only rules cannot
    certify market data, and an unregistered name must never be silently accepted."""


class _IncompleteReplayMarker(Exception):
    """Internal signal only: raised inside the run-evidence context to make `run_context`
    finalize this attempt as a retained, truthful non-success outcome (IR-05/R3I-05), then
    caught here so an incomplete replay is a normal `ReplayRunResult`, not an uncaught crash."""


class RunConfigError(ValueError):
    pass


class UnsupportedCorporateActionError(ValueError):
    """A corporate-action transformation whose units/known-at timing are not fully
    specified and supported (docs/data_rules.md); the affected session must retain this
    explicit result rather than silently continuing."""


class UnreconciledPositionError(ValueError):
    """An open position was still held when a new session began. It cannot silently carry
    overnight without an explicit, supported reconciliation."""

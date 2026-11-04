"""Finite, known-path experiment validation; no cache or runner construction."""

from copy import deepcopy

from rsys.application.historical import plan_backtest
from rsys.application.models import ExperimentArm, ExperimentPlan

ENTRY = {
    "range_minutes",
    "breakout_buffer_ticks",
    "entry_deadline_ms",
    "warmup_bars",
    "slope_lookback_bars",
    "pullback_max_bars",
    "confirmation_buffer_ticks",
}


def patch_axis(kind, path):
    path = tuple(path)
    if (
        kind == "scanner"
        and len(path) == 2
        and path[0] == "scanner"
        and path[1]
        in {
            "top_n",
            "lookback_sessions",
            "max_lookback_calendar_sessions",
            "min_dollar_volume",
            "max_current_bar_age_ms",
        }
    ):
        return "scanner_profile"
    if (
        kind == "costs"
        and len(path) == 3
        and path[:2] == ("account", "costs")
        and path[2] in {"adverse_bps", "feed_delay_ms", "entry_fee", "exit_fee"}
    ):
        return "cost_profile"
    if kind == "account":
        if path == ("account", "initial_cash"):
            return "initial_cash"
        if (
            len(path) == 3
            and path[:2] == ("account", "risk")
            and path[2]
            in {
                "per_trade_fraction",
                "daily_loss_fraction",
                "maximum_drawdown_fraction",
                "daily_loss_basis",
                "halt_action",
            }
        ):
            return "risk_profile"
        if path == ("account", "sizing", "max_quantity"):
            return "risk_profile"
        if path == ("account", "sizing", "quantity_step"):
            return "cost_profile"
    if kind == "exit" and path in (
        ("account", "flatten_at"),
        ("account", "execution", "max_hold_ms"),
    ):
        return "execution_mode"
    if path[:2] == ("strategy", "strategy_config") and len(path) == 3:
        allowed = (
            ENTRY
            if kind == "entry"
            else {"target_r"}
            if kind == "exit"
            else ENTRY | {"target_r", "tick", "base_interval_minutes"}
            if kind == "complete_system"
            else set()
        )
        if path[2] in allowed:
            return "strategies"
    if kind == "complete_system" and path in (
        ("strategy", "strategy"),
        ("strategy", "strategy_config"),
    ):
        return "strategies"
    raise ValueError("Unsupported change for the declared study kind")


def validate_experiment(raw, base, access_log, writer):
    plan = ExperimentPlan.model_validate(raw)
    if type(raw["schema_version"]) is not int or not plan.hypothesis.strip():
        raise ValueError("Explicit version and hypothesis required")
    names = ["baseline", *[v.name for v in plan.variants]]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate or reserved variant name")
    if any(not ref.strip() for ref in plan.external_attempts) or len(
        plan.external_attempts
    ) != len(set(plan.external_attempts)):
        raise ValueError("External attempt references must be unique and nonblank")
    if len(names) + len(plan.external_attempts) > plan.trial_limit:
        raise ValueError(
            "Trial limit cannot cover all declared arms and external attempts"
        )
    baseline = plan_backtest(base, access_log, writer)
    arms = [ExperimentArm("baseline", deepcopy(base))]
    for variant in plan.variants:
        config, paths = deepcopy(base), []
        for patch in variant.changes:
            path = tuple(patch.path)
            if any(path[: len(p)] == p or p[: len(path)] == path for p in paths):
                raise ValueError("Duplicate or overlapping change paths")
            paths.append(path)
            if patch_axis(plan.study_kind, path) != plan.permitted_axis:
                raise ValueError("Change differs from the permitted comparison axis")
            target = config
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = deepcopy(patch.value)
        if config == base:
            raise ValueError("Variant must change a setting")
        candidate = plan_backtest(config, access_log, writer)
        if candidate.requests != baseline.requests:
            raise ValueError("Changed history budget changes pinned data identity")
        arms.append(ExperimentArm(variant.name, config))
    return plan, arms

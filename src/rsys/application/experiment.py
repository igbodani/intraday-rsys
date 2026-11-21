"""Serial finite experiments using the existing pinned historical backtest."""

import hashlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from rsys.application.experiment_config import validate_experiment
from rsys.application.file_attempt import file_attempt
from rsys.application.historical import backtest, load_request
from rsys.application.intrabar import run_tape_command
from rsys.application.offline import deny_python_network
from rsys.data.gaps import estimate_label
from rsys.reporting.experiment import experiment_html, scorecard_metrics
from rsys.research.comparison import compare_run_pair, load_run_manifest
from rsys.research.diagnostics import (
    atomic_write_json,
    invocation_scope,
    protect_evidence_write,
    start_invocation,
)
from rsys.research.errors import DiagnosticWriteError, StudyBudgetError
from rsys.research.experiments import (
    compute_dependency_lock_hash,
    compute_source_fingerprint,
    redact_secrets,
)
from rsys.research.inspection import inspect_attempt
from rsys.research.models import ExperimentTrialContext
from rsys.research.storage import canonical_hash, new_run_id
from rsys.research.studies import StudyRegistry


def _load(path, identity):
    identity["path"] = str(Path(path).resolve())
    try:
        config, raw = load_request(path)
    except Exception as exc:
        identity["sha256"] = getattr(exc, "input_sha256", None)
        raise
    identity.update(
        sha256=hashlib.sha256(raw).hexdigest(), config=redact_secrets(config)
    )
    return config


def provenance():
    return compute_source_fingerprint()[0], compute_dependency_lock_hash()


def _verified_trade_result(root, row, experiment):
    attempt = Path(root) / "trade-attempts" / row["run_id"]
    status = inspect_attempt(root, row["run_id"])
    if status.outcome != "success" or status.completeness != "complete":
        raise ValueError("Incomplete trade evidence")
    if json.loads((attempt / "input.json").read_bytes()) != row["config"]:
        raise ValueError("Trade settings differ from the declared arm")
    if json.loads((attempt / "pins.json").read_bytes()) != experiment["trade_pins"]:
        raise ValueError("Trade tapes differ from the frozen experiment")
    data = json.loads((attempt / "result.json").read_bytes())
    ctx = data["experiment"]
    if (
        ctx["plan_hash"] != experiment["plan_hash"]
        or ctx["trial_id"] != row["trial_id"]
        or ctx["arm"] != row["name"]
        or data["code_hash"] != experiment["code_hash"]
        or data["dependency_hash"] != experiment["dependency_hash"]
    ):
        raise ValueError("Trade arm provenance mismatch")
    return data


def build_scorecards(result, root, permitted_axis):
    baseline = result["arms"][0]
    trade_baseline = None
    for row in result["arms"]:
        row["scorecard"] = {
            "status": "unavailable",
            "reason": row.get("error", row["state"]),
        }
        if row["state"] != "success":
            continue
        try:
            if "trade_pins" in result:
                data = _verified_trade_result(root, row, result)
                if row is baseline:
                    trade_baseline = data
                comparison = {"status": "unavailable", "reason": "baseline unavailable"}
                if trade_baseline is not None:
                    before = trade_baseline["metrics"]["ending_equity"]
                    after = data["metrics"]["ending_equity"]
                    comparison = {
                        "status": "available",
                        "result": {
                            "run_a": baseline["run_id"],
                            "run_b": row["run_id"],
                            "ending_equity_delta": str(
                                Decimal(after) - Decimal(before)
                            ),
                        },
                    }
                row["scorecard"] = {
                    "status": "available",
                    "estimate_label": data["estimate_label"],
                    "metrics": scorecard_metrics(data, data),
                    "calendar_year_results": data.get("calendar_year_results", []),
                    "comparison": comparison,
                }
                continue
            manifest = load_run_manifest(Path(root), row["run_id"])
            metrics = json.loads(
                (Path(root) / "attempts" / row["run_id"] / "metrics.json").read_text()
            )
            own = scorecard_metrics(manifest, metrics)
            row["scorecard"] = {
                "status": "available",
                "estimate_label": manifest.get("estimate_label"),
                "metrics": own,
                "comparison": {
                    "status": "unavailable",
                    "reason": "baseline unavailable",
                },
            }
            if baseline["state"] == "success":
                comparison = compare_run_pair(
                    Path(root),
                    baseline["run_id"],
                    row["run_id"],
                    permitted_difference=permitted_axis,
                )
                row["scorecard"]["comparison"] = {
                    "status": "available",
                    "result": comparison,
                }
        except Exception as exc:  # noqa: BLE001 -- unavailable evidence is retained
            # Never give an incompatible/tampered comparison fabricated zero metrics.
            row["scorecard"] = {
                "status": "unavailable",
                "reason": f"Evidence/comparison rejected ({type(exc).__name__})",
            }


def run_experiment(path, out_dir):
    root = Path(out_dir)
    result = {
        "schema_version": 1,
        "status": "failed",
        "promotion_status": "unscored",
        "plan": {},
        "base": {},
        "arms": [],
        "access_log": [],
    }
    with file_attempt(
        root,
        result,
        command="experiment",
        directory="experiments",
        link_method="link_experiment_attempt",
        report_name="experiment.json",
        renderer=experiment_html,
        write_json=atomic_write_json,
    ) as (writer, attempt):
        raw = _load(path, result["plan"])
        writer.use_config(raw)
        atomic_write_json(attempt / "experiment.json", result)
        # Schema validation occurs before opening an arbitrary base path.
        from rsys.application.models import ExperimentPlan

        parsed = ExperimentPlan.model_validate(raw)
        base = _load(Path(path).resolve().parent / parsed.base_config, result["base"])
        writer.use_config({"plan": raw, "base": base})
        atomic_write_json(attempt / "experiment.json", result)
        plan, arms = validate_experiment(raw, base, result["access_log"], writer)
        contract = {"plan": raw, "base": base}
        trade_pins = None
        if plan.trade_pins is not None:
            # Freeze one snapshot for the parent and every child before reserving trials.
            trade_pins = json.loads(
                (Path(path).resolve().parent / plan.trade_pins).read_bytes()
            )
            if not isinstance(trade_pins, dict) or not trade_pins:
                raise ValueError("Expected a nonempty trade pin map")
            contract["trade_pins"] = result["trade_pins"] = trade_pins
        feed = base["feed"] if trade_pins is not None else base["dataset"]["feed"]
        result["estimate_label"] = estimate_label(feed)
        result["arms"] = [
            {"name": arm.name, "state": "not_started", "config": arm.config}
            for arm in arms
        ]
        plan_hash = canonical_hash(contract)
        code, lock = provenance()
        result.update(
            plan_hash=plan_hash,
            code_hash=code,
            dependency_hash=lock,
            study_id=plan.study_id,
            study_kind=plan.study_kind,
            interpretation="complete-system account comparison"
            if plan.study_kind == "complete_system"
            else "full-account rerun varying the declared setting category; no causal/common-entry attribution",
        )
        registry = StudyRegistry(root)
        try:
            registry.register(
                plan.study_id,
                contract,
                plan.trial_limit,
                len(plan.external_attempts),
            )
            atomic_write_json(attempt / "experiment.json", result)
            for arm, row in zip(arms, result["arms"], strict=True):
                if provenance() != (code, lock):
                    raise ValueError(
                        "Source/dependency identity changed during experiment"
                    )
                child_id = new_run_id()
                try:
                    trial_id = registry.reserve(
                        plan.study_id,
                        writer.invocation_id,
                        arm.name,
                        arm.config,
                        child_id,
                    )
                except StudyBudgetError as exc:
                    row.update(state="blocked_budget", error=str(exc))
                    continue
                row.update(state="running", trial_id=trial_id, invocation_id=child_id)
                context = ExperimentTrialContext(
                    plan.study_id,
                    plan_hash,
                    writer.invocation_id,
                    trial_id,
                    arm.name,
                    plan.hypothesis,
                    code,
                    lock,
                )
                try:
                    atomic_write_json(attempt / "experiment.json", result)
                    child = start_invocation(
                        root,
                        command="trade-backtest"
                        if trade_pins is not None
                        else "experiment-arm",
                        invocation_id=child_id,
                    )
                    with invocation_scope(
                        root, command="experiment-arm", invocation=child
                    ):
                        try:
                            with deny_python_network():
                                if trade_pins is not None:
                                    directory = run_tape_command(
                                        arm.config,
                                        root,
                                        acquire=False,
                                        pins=trade_pins,
                                        invocation=child,
                                        experiment_context=context,
                                    )
                                    run_id = directory.name
                                    trace, manifest = (
                                        directory / "events.jsonl.gz",
                                        directory / "manifest.json",
                                    )
                                else:
                                    _, run_id, trace, manifest = backtest(
                                        arm.config,
                                        json.dumps(arm.config).encode(),
                                        root,
                                        report=True,
                                        invocation=child,
                                        experiment_context=context,
                                    )
                            row.update(
                                run_id=run_id, manifest=str(manifest), trace=str(trace)
                            )
                            if provenance() != (code, lock):
                                raise ValueError(
                                    "Source/dependency identity changed during experiment"
                                )
                        finally:
                            protect_evidence_write(child.close)
                    row["state"] = "success"
                    registry.finish(trial_id, "success", run_id)
                except BaseException as exc:
                    row.update(
                        state="interrupted"
                        if isinstance(exc, KeyboardInterrupt)
                        else "failed",
                        error=writer.error_message(exc),
                        run_id=row.get("run_id") or getattr(exc, "run_id", None),
                    )
                    protect_evidence_write(
                        lambda trial_id=trial_id, row=row: registry.finish(
                            trial_id, row["state"], row["run_id"], row["error"]
                        ),
                        catch=(sqlite3.Error, OSError),
                    )
                    if not isinstance(exc, Exception):
                        raise
                    if plan.failure_policy == "stop":
                        break
                finally:
                    protect_evidence_write(
                        lambda: atomic_write_json(attempt / "experiment.json", result),
                        catch=(OSError, DiagnosticWriteError),
                    )
            build_scorecards(result, root, plan.permitted_axis)
            complete = all(
                row["state"] == "success" and row["scorecard"]["status"] == "available"
                for row in result["arms"]
            )
            if complete:
                result["status"] = "success"
            else:
                writer.fail(
                    ValueError(
                        "Experiment incomplete; inspect every arm and its retained evidence"
                    )
                )
        finally:
            try:

                def snapshot_registry():
                    result["registry_trials"] = registry.trials(plan.study_id)

                protect_evidence_write(
                    snapshot_registry, catch=(sqlite3.Error, OSError)
                )
            finally:
                registry.close()
    return result, attempt / "manifest.json"

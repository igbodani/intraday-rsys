"""Public multi-session synthetic execution with one retained experiment lifecycle."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from functools import partial
from pathlib import Path

from rsys.account.models import SizingPolicy
from rsys.account.portfolio import Portfolio
from rsys.account.risk import RiskMonitor
from rsys.application.batch_config import execution_costs, risk_policy, validate_batch
from rsys.application.builders import build_bar, build_strategy
from rsys.application.errors import RunConfigError
from rsys.application.history_config import build_history_scanner, parse_scanner
from rsys.application.results import summarize_runner
from rsys.application.session_config import ms
from rsys.application.sessions import SessionCoordinator
from rsys.execution.bar import BarSessionRunner
from rsys.execution.errors import IncompleteSessionError
from rsys.execution.models import ExecutionPolicy, Trade
from rsys.execution.trade import TradeInputRunner
from rsys.research.evaluation import compute_metrics
from rsys.research.experiments import (
    SecretRedactor,
    redact_secrets,
    run_context,
    start_run,
)
from rsys.research.multi_session import compute_multi_session_metrics
from rsys.research.storage import RunStorage, canonical_hash
from rsys.strategies.minute_scanner import HISTORY_SCANNER_VERSION


def _decimal(value):
    return Decimal(str(value))


def json_value(value):
    return json.loads(json.dumps(value, default=str))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )


class SessionBatch:
    def __init__(self, config, *, allow_historical=False):
        validate_batch(config, allow_historical=allow_historical)
        self.config = deepcopy(config)
        self.history = parse_scanner(self.config)
        self.config.setdefault("top_n", 20)
        for session in self.config["sessions"]:
            for symbol, spec in session["symbols"].items():
                spec.setdefault("session_open", "09:30:00")
                spec["strategy_config"] = json_value(
                    asdict(build_strategy(symbol, spec["strategy"], spec).config)
                )
                if config["input_mode"] == "trade":
                    spec.setdefault("corrections", [])
                    for trade in spec["trades"]:
                        trade.setdefault("conditions", [])
                        trade.setdefault("tape", None)
        cash = _decimal(config["initial_cash"])
        self.portfolio = Portfolio(cash, cash, _decimal(0))
        self.risk = RiskMonitor(risk_policy(config), cash)
        self.costs = execution_costs(config)
        self.plans = {s["date"]: s for s in self.config["sessions"]}
        self.sessions = [
            {"session_date": s["date"], "status": "not_started"}
            for s in self.config["sessions"]
        ]
        self.runners = []
        self.trace = []
        self._ran = False
        sizing = SizingPolicy(
            max_quantity=_decimal(config["sizing"]["max_quantity"])
            if config["sizing"]["max_quantity"] is not None
            else None,
            quantity_step=_decimal(config["sizing"]["quantity_step"]),
            entry_fee=_decimal(config["costs"]["entry_fee"]),
            exit_fee=_decimal(config["costs"]["exit_fee"]),
        )
        self.coordinator = SessionCoordinator(
            strategy_factory=lambda day: {
                s: build_strategy(s, spec["strategy"], spec)
                for s, spec in sorted(self.plans[day.isoformat()]["symbols"].items())
            },
            portfolio=self.portfolio,
            sizing=sizing,
            top_n=self.config["top_n"],
            execution_factory=lambda day: ExecutionPolicy(
                **config["execution"],
                flatten_at_ms=ms(self.plans[day.isoformat()]["flatten_at"])
                if self.plans[day.isoformat()]["flatten_at"] is not None
                else None,
            ),
            runner_factory=partial(
                BarSessionRunner,
                provider="alpaca",
                feed=config["historical_source"]["feed"],
            )
            if config.get("evidence_type") == "historical_bar_estimate"
            else BarSessionRunner
            if config["input_mode"] == "bar"
            else TradeInputRunner,
            risk=self.risk,
            costs=self.costs,
        )

    def run(self):
        if self._ran:
            raise ValueError("a batch attempt is single-use")
        self._ran = True
        for index, plan in enumerate(self.config["sessions"]):
            day = date.fromisoformat(plan["date"])
            starting, realized_before = (
                self.portfolio.equity({}),
                self.portfolio.realized_pnl,
            )
            record = self.sessions[index]
            data, corrections = {}, None
            if self.config["input_mode"] == "bar":
                data = {
                    s: [build_bar(v["strategy"], b) for b in v["bars"]]
                    for s, v in plan["symbols"].items()
                }
            else:
                corrections = {}
                for symbol, spec in plan["symbols"].items():
                    data[symbol] = [
                        Trade(
                            t["trade_id"],
                            ms(t["event_time"]),
                            ms(t["available_at"]),
                            _decimal(t["price"]),
                            quality_ok=t["quality_ok"],
                            size=_decimal(t["size"]),
                            conditions=tuple(t["conditions"]),
                            tape=t["tape"],
                        )
                        for t in spec["trades"]
                    ]
                    corrections[symbol] = [
                        {
                            **{
                                k: (
                                    _decimal(v)
                                    if k in ("corrected_price", "corrected_size")
                                    and v is not None
                                    else v
                                )
                                for k, v in c.items()
                                if k != "available_at"
                            },
                            "available_at_ms": ms(c["available_at"]),
                        }
                        for c in spec["corrections"]
                    ]
            scanner = {
                ms(t): {
                    s: tuple(
                        _decimal(row[k]) if row[k] is not None else None
                        for k in ("rvol", "gain")
                    )
                    for s, row in rows.items()
                }
                for t, rows in plan.get("scanner_inputs", {}).items()
            }
            record["status"] = "failed"
            self.coordinator.last_runner = None
            try:
                runner = self.coordinator.run_session(
                    day,
                    data,
                    scanner,
                    corrections,
                    history_scanner=build_history_scanner(
                        self.config, self.history, day, plan["symbols"]
                    ),
                )
                if self.portfolio.positions and (
                    index < len(self.sessions) - 1
                    or self.config["completion_policy"] == "require_flat"
                ):
                    raise IncompleteSessionError(
                        "session ended with open positions under require-flat composition"
                    )
                record["status"] = "success"
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    record["status"] = "interrupted"
                record["error"] = {"type": type(exc).__name__, "message": str(exc)}
                raise
            finally:
                runner = self.coordinator.last_runner
                if runner is not None:
                    self.runners.append(runner)
                    summary = summarize_runner(runner, starting)
                    summary["metrics"] = asdict(
                        compute_metrics(
                            starting,
                            self.portfolio.equity({}),
                            self.portfolio.realized_pnl - realized_before,
                            runner.fills,
                        )
                    )
                    record.update({k: v for k, v in summary.items() if k != "trace"})
                    record["risk_state"] = self.risk.snapshot()
                    self.trace.extend(
                        {"session_date": plan["date"], **r} for r in runner.trace
                    )

    def summary(self):
        fills = [f for runner in self.runners for f in runner.fills]
        cash = _decimal(self.config["initial_cash"])
        completed_dates = {
            s["session_date"] for s in self.sessions if s["status"] == "success"
        }
        stats = compute_multi_session_metrics(
            [
                r
                for r in self.coordinator.results
                if r.session_date.isoformat() in completed_dates
            ],
            cash,
        )
        return {
            "starting_equity": cash,
            "ending_equity": self.portfolio.equity({}),
            "metrics": asdict(
                compute_metrics(
                    cash, self.portfolio.equity({}), self.portfolio.realized_pnl, fills
                )
            ),
            "multi_session_metrics": asdict(stats),
            "multi_session_metrics_scope": "completed_sessions_only",
            "sessions": self.sessions,
            "risk_state": self.risk.snapshot(),
            "decisions": [
                {"session_date": s["session_date"], **d}
                for s in self.sessions
                for d in s.get("decisions", [])
            ],
            "fills": [
                {"session_date": s["session_date"], **f}
                for s in self.sessions
                for f in s.get("fills", [])
            ],
            "open_positions": {
                s: asdict(p) for s, p in self.portfolio.positions.items()
            },
        }


def run_batch_cli(
    config, raw, out_dir: Path, *, report: bool, prepare=None, access_log=None
):
    from rsys.reporting.html import render_report

    if not isinstance(config, dict):
        config = {"invalid_input": config}
    batch, error = None, None
    try:
        if prepare is not None:
            config = prepare(config)
        batch = SessionBatch(config, allow_historical=prepare is not None)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - re-raised inside the retained run context
        # Even construction/validation interruptions get a retained attempt. Never
        # execute a partially constructed batch or silently drop an invocation.
        error = (
            RunConfigError(str(exc))
            if isinstance(exc, (ValueError, TypeError, KeyError))
            else exc
        )
    storage = RunStorage(out_dir)
    redactor = SecretRedactor.from_resolved_config(config)
    try:
        record = start_run(
            storage,
            hypothesis=(
                "Offline historical bars with modeled timing and explicit risk/cost policy."
                if config.get("evidence_type") == "historical_bar_estimate"
                else "Offline synthetic sessions with explicit risk/cost policy."
            ),
            phase="run",
            resolved_config=batch.config if batch else config,
            data_identity="sha256:" + hashlib.sha256(raw).hexdigest(),
            repo_root=Path(__file__).resolve().parents[3],
        )
        attempt = out_dir / "attempts" / record.run_id
        attempt.mkdir(parents=True, exist_ok=False)
        trace_path, manifest_path = attempt / "trace.jsonl", attempt / "manifest.json"
        manifest = {
            "schema_version": 3,
            "evidence_type": config.get("evidence_type", "unresolved_invalid_config"),
            "historical_source": redact_secrets(config.get("historical_source")),
            "run_id": record.run_id,
            "status": "failed",
            "metrics_scope": "partial_attempt",
            "config_hash": record.config_hash,
            "data_hash": record.data_hash,
            "code_hash": record.code_hash,
            "code_revision": record.code_revision,
            "dependency_lock_hash": record.dependency_lock_hash,
            "calendar_version": "nyse-2024-v1",
            "account_basis": "synthetic_immediate_settlement",
            "scanner_basis": "unresolved_invalid_config"
            if batch is None
            else (
                (
                    "historical_final_bar_features_v1"
                    if config.get("evidence_type") == "historical_bar_estimate"
                    else HISTORY_SCANNER_VERSION
                )
                if batch.history is not None
                else "supplied_synthetic_scores"
            ),
        }
        with run_context(storage, record):
            try:
                if error:
                    raise error
                manifest.update(
                    {
                        "execution_basis": "historical_bar_open_estimate_v1"
                        if config.get("evidence_type") == "historical_bar_estimate"
                        else "synthetic_bar_open_v3"
                        if config["input_mode"] == "bar"
                        else "subsequent_trade_estimate_v2",
                        "input_mode": config["input_mode"],
                        "initial_cash": config["initial_cash"],
                        "session_dates": list(batch.plans),
                        "completion_policy": config["completion_policy"],
                        "universe": sorted(
                            {s for p in batch.plans.values() for s in p["symbols"]}
                        ),
                        "risk": asdict(batch.risk.policy),
                        "costs": config["costs"],
                        "sizing": asdict(batch.coordinator.sizing),
                        "top_n": batch.config["top_n"],
                        "execution": {
                            "policy": config["execution"],
                            "flatten_times": {
                                d: p["flatten_at"] for d, p in batch.plans.items()
                            },
                        },
                        "strategies": {
                            d: {
                                s: {
                                    k: v
                                    for k, v in spec.items()
                                    if k not in ("bars", "trades", "corrections")
                                }
                                for s, spec in p["symbols"].items()
                            }
                            for d, p in batch.plans.items()
                        },
                        "market_data_hash": canonical_hash(
                            {
                                d: {
                                    s: {
                                        k: spec[k]
                                        for k in ("bars", "trades", "corrections")
                                        if k in spec
                                    }
                                    for s, spec in p["symbols"].items()
                                }
                                for d, p in batch.plans.items()
                            }
                        ),
                        "scanner_basis": (
                            "historical_final_bar_features_v1"
                            if config.get("evidence_type") == "historical_bar_estimate"
                            else HISTORY_SCANNER_VERSION
                        )
                        if batch.history is not None
                        else "supplied_synthetic_scores",
                        "scanner_hash": canonical_hash(
                            batch.config["scanner"]
                            if batch.history is not None
                            else {
                                d: p["scanner_inputs"] for d, p in batch.plans.items()
                            }
                        ),
                    }
                )
                batch.run()
                manifest["status"], manifest["metrics_scope"] = (
                    "success",
                    "complete_attempt",
                )
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    manifest["status"] = "interrupted"
                exc.run_id = record.run_id
                exc.trace_path = trace_path
                exc.manifest_path = manifest_path
                manifest["error"] = {
                    "type": type(exc).__name__,
                    "message": redactor.scrub(str(exc)),
                }
                raise
            finally:
                if access_log is not None:
                    write_json(attempt / "data_access.json", access_log)
                summary = batch.summary() if batch else {}
                manifest.update(summary)
                rows = batch.trace if batch else []
                trace_text = "".join(
                    json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows
                )
                # Hash exactly the canonical bytes written, independent of Windows
                # text-mode newline translation. The verifier still hashes raw bytes.
                trace_bytes = trace_text.encode("utf-8")
                trace_path.write_bytes(trace_bytes)
                manifest["semantic_trace_hash"] = hashlib.sha256(
                    trace_bytes
                ).hexdigest()
                write_json(attempt / "input.json", redact_secrets(config))
                write_json(attempt / "resolved_config.json", record.resolved_config)
                write_json(attempt / "fills.json", summary.get("fills", []))
                if batch is not None and batch.history is not None:
                    write_json(
                        attempt / "scanner_history.json",
                        {
                            identity: snapshot
                            for runner in batch.runners
                            for identity, snapshot in runner.history_scanner.snapshots.items()
                        },
                    )
                write_json(
                    attempt / "metrics.json",
                    {
                        k: summary[k]
                        for k in ("metrics", "multi_session_metrics", "risk_state")
                        if k in summary
                    },
                )
                for session in summary.get("sessions", []):
                    write_json(
                        attempt / "sessions" / f"{session['session_date']}.json",
                        session,
                    )
                if report:
                    (attempt / "report.html").write_text(
                        render_report(manifest, rows), encoding="utf-8"
                    )
                artifacts = sorted(p for p in attempt.rglob("*") if p.is_file())
                manifest["artifact_hashes"] = {
                    p.relative_to(attempt).as_posix(): hashlib.sha256(
                        p.read_bytes()
                    ).hexdigest()
                    for p in artifacts
                }
                write_json(manifest_path, manifest)
                record.artifacts.extend(str(p) for p in [*artifacts, manifest_path])
                record.decision_gate_status["manifest_sha256"] = hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest()
                record.metrics = json_value(summary.get("metrics", {}))
        return summary, record.run_id, trace_path, manifest_path
    finally:
        storage.close()

"""Offline validation, replay, synthetic sessions, fixtures and evidence comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from rsys.application.config import load_config, validate_config
from rsys.application.errors import ConfigError, RunConfigError, UnsupportedRuleSetError
from rsys.application.models import ReplayConfig
from rsys.application.replay import run_replay_from_config
from rsys.application.run import run_cli_entry
from rsys.cli.errors import FixtureCliError
from rsys.cli.fixtures import run_fixture_cli
from rsys.cli.progress import ProgressReporter
from rsys.data.errors import ReplayInputError
from rsys.execution.errors import IncompleteSessionError
from rsys.research.comparison import compare_run_pair
from rsys.research.errors import (
    CompareCliError,
    ComparisonError,
    DiagnosticWriteError,
    RecoveryInProgressError,
)
from rsys.research.inspection import inspect_attempt, recover_attempts

EXIT_SUCCESS = 0
EXIT_OTHER_FAILURE = 1
EXIT_INVALID_CONFIG = 2
EXIT_DATA_ERROR = 3
EXIT_INVARIANT_FAILURE = 4

_NOT_IMPLEMENTED = ("final-evaluate",)


def _blocked_final_evaluate():
    print(
        "blocked: final evaluation requires resolved research decisions, a frozen specification, and holdout authorization",
        file=sys.stderr,
    )
    return 5


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    result = validate_config(config)
    if result.ok:
        print(f"ok: {args.config} has no blockers")
        return EXIT_SUCCESS

    print(
        f"blocked: {args.config} has {len(result.blockers)} unresolved/invalid field(s)",
        file=sys.stderr,
    )
    for blocker in result.blockers:
        decision = f" ({blocker.decision_id})" if blocker.decision_id else ""
        print(f"  - {blocker.field_path}{decision}: {blocker.reason}", file=sys.stderr)
    return EXIT_INVALID_CONFIG


def _cmd_replay(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    if not isinstance(config, ReplayConfig):
        print(
            f"error: {args.config} is not a replay-mode config (mode={config.mode!r})",
            file=sys.stderr,
        )
        return EXIT_INVALID_CONFIG

    result = validate_config(config)
    if not result.ok:
        print(
            f"blocked: {args.config} has {len(result.blockers)} unresolved/invalid field(s)",
            file=sys.stderr,
        )
        for blocker in result.blockers:
            print(f"  - {blocker.field_path}: {blocker.reason}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    out_dir = (
        Path(args.config).resolve().parent.parent
        / "runs"
        / "replay"
        / Path(args.config).stem
        if args.out == "auto"
        else Path(args.out)
    )

    try:
        result = run_replay_from_config(config, out_dir)
    except (ReplayInputError, UnsupportedRuleSetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    except (ValueError, RuntimeError) as exc:
        print(
            f"error: replay failed ({type(exc).__name__}); inspect retained attempt",
            file=sys.stderr,
        )
        return EXIT_INVARIANT_FAILURE
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; lifecycle already retained the failure
        print(
            f"error: replay failed ({type(exc).__name__}); inspect retained attempt",
            file=sys.stderr,
        )
        return EXIT_OTHER_FAILURE

    if not result.complete:
        print(f"incomplete: {result.incomplete_reason}", file=sys.stderr)
        print(f"run_id: {result.run_id}", file=sys.stderr)
        print(f"trace: {result.trace_path}", file=sys.stderr)
        return EXIT_DATA_ERROR

    print(f"ok: {result.event_count} event(s) replayed")
    print(f"run_id: {result.run_id}")
    print(f"trace: {result.trace_path}")
    print(f"manifest: {result.manifest_path}")
    print(f"semantic_trace_hash: {result.semantic_hash}")
    return EXIT_SUCCESS


def _cmd_fixture(args: argparse.Namespace) -> int:
    out_dir = Path("runs") / "fixture" if args.out == "auto" else Path(args.out)
    try:
        outcomes, trace_path, manifest_path = run_fixture_cli(args.case, out_dir)
    except FixtureCliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    for outcome in outcomes:
        status = "ok" if outcome.ok else "MISMATCH"
        print(f"{status}: {outcome.case_id}")
        for problem in outcome.problems:
            print(f"  - {problem}", file=sys.stderr)
    print(f"trace: {trace_path}")
    print(f"manifest: {manifest_path}")

    if any(not o.ok for o in outcomes):
        return EXIT_INVARIANT_FAILURE
    return EXIT_SUCCESS


def _cmd_run(args: argparse.Namespace) -> int:
    """Run one or more synthetic bar/trade sessions; no real provider integration."""
    from rsys.research.diagnostics import start_invocation

    out_dir = Path("runs") / "run" if args.out == "auto" else Path(args.out)
    progress = ProgressReporter(args.progress, sys.stderr)
    try:
        invocation = start_invocation(out_dir, command="run", observer=progress)
    except DiagnosticWriteError as exc:
        print(f"error: invocation evidence could not be saved: {exc}", file=sys.stderr)
        return EXIT_OTHER_FAILURE
    try:
        summary, run_id, trace_path, manifest_path = run_cli_entry(
            args.config, out_dir, report=args.report, invocation=invocation
        )
    except RunConfigError as exc:
        print(f"error: {getattr(exc, 'diagnostic_message', str(exc))}", file=sys.stderr)
        _print_attempt_paths(exc)
        return EXIT_INVALID_CONFIG
    except IncompleteSessionError as exc:
        print(f"incomplete: {exc}; inspect retained attempt", file=sys.stderr)
        _print_attempt_paths(exc)
        return EXIT_DATA_ERROR
    except ValueError as exc:
        print(
            f"error: run invariant failed ({type(exc).__name__}); inspect retained attempt",
            file=sys.stderr,
        )
        _print_attempt_paths(exc)
        return EXIT_INVARIANT_FAILURE
    except DiagnosticWriteError as exc:
        print(
            f"error: invocation evidence could not be saved ({exc}); "
            f"invocation {invocation.invocation_id}",
            file=sys.stderr,
        )
        _print_attempt_paths(exc)
        return EXIT_OTHER_FAILURE
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; lifecycle already retained the failure
        print(
            f"error: run failed ({type(exc).__name__}); inspect retained attempt",
            file=sys.stderr,
        )
        _print_attempt_paths(exc)
        return EXIT_OTHER_FAILURE

    progress.finish("success")
    print(
        f"ok: {len(summary['decisions'])} decision(s), {len(summary['fills'])} fill(s)"
    )
    print(f"run_id: {run_id}")
    print(f"trace: {trace_path}")
    print(f"manifest: {manifest_path}")
    print(f"starting_equity: {summary['starting_equity']}")
    print(f"ending_equity: {summary['ending_equity']}")
    if args.report:
        print(f"report: {trace_path.parent / 'report.html'}")
    return EXIT_SUCCESS


def _print_attempt_paths(exc):
    for label, attribute in (
        ("invocation", "invocation_path"),
        ("journal", "journal_path"),
        ("run_id", "run_id"),
        ("trace", "trace_path"),
        ("manifest", "manifest_path"),
    ):
        value = getattr(exc, attribute, None)
        if value is not None:
            print(f"{label}: {value}", file=sys.stderr)
    # A secondary diagnostics-write failure while recording this primary failure
    # (BR-01 T04) must still be surfaced, never silently dropped -- but it must
    # never replace the primary error message/exit code printed above/by the caller.
    diagnostic_write_error = getattr(exc, "diagnostic_write_error", None)
    if diagnostic_write_error is not None:
        print(
            f"warning: invocation evidence could not be fully saved: "
            f"{type(diagnostic_write_error).__name__}; see invocation evidence when available",
            file=sys.stderr,
        )


def _cmd_compare(args: argparse.Namespace) -> int:
    """Compares two retained synthetic `rsys run` manifests -- NOT `rsys replay`
    attempts; see docs/M2_IMPLEMENTATION_STATUS.md."""
    storage_root = Path(args.storage_root)
    run_a, run_b = args.runs
    try:
        result = compare_run_pair(
            storage_root, run_a, run_b, permitted_difference=args.permitted_difference
        )
    except CompareCliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    except ComparisonError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVARIANT_FAILURE

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "comparison.json"
    report_path.write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )

    print(f"ok: compared {run_a} vs {run_b}")
    print(f"assumption_diffs: {result['assumption_diffs']}")
    print(f"ending_equity_delta: {result['ending_equity_delta']}")
    print(f"report: {report_path}")
    return EXIT_SUCCESS


def _cmd_data(args):
    from rsys.application.historical import (
        backtest,
        download,
        load_request,
        retain_input_failure,
    )
    from rsys.research.diagnostics import protect_evidence_write, start_invocation

    out_dir = Path(args.out)
    progress = ProgressReporter(args.progress, sys.stderr)
    try:
        invocation = start_invocation(out_dir, command=args.command, observer=progress)
    except DiagnosticWriteError as exc:
        print(f"error: invocation evidence could not be saved: {exc}", file=sys.stderr)
        return EXIT_OTHER_FAILURE

    try:
        try:
            config, raw = load_request(args.config)
        except Exception as exc:
            input_sha256 = getattr(exc, "input_sha256", None)
            protect_evidence_write(
                lambda: invocation.record_identity(
                    input_path=str(args.config), input_sha256=input_sha256
                ),
                catch=(DiagnosticWriteError, OSError),
            )
            retain_input_failure(
                args.config, args.out, args.command, exc, invocation=invocation
            )
            raise
        invocation.record_identity(
            input_path=str(args.config), input_sha256=hashlib.sha256(raw).hexdigest()
        )
        if args.command == "download":
            result, manifest = download(
                config, args.out, refresh=args.refresh, invocation=invocation
            )
        else:
            summary, run_id, trace, manifest = backtest(
                config, raw, args.out, report=args.report, invocation=invocation
            )
    except DiagnosticWriteError as exc:
        invocation.fail(exc)
        protect_evidence_write(invocation.close)
        print(
            f"error: invocation evidence could not be saved ({exc}); "
            f"invocation {invocation.invocation_id}",
            file=sys.stderr,
        )
        _print_attempt_paths(exc)
        return EXIT_OTHER_FAILURE
    except Exception as exc:  # noqa: BLE001 -- retained provider/loader failures
        invocation.fail(exc)
        protect_evidence_write(invocation.close)
        print(f"error: {type(exc).__name__}: {exc.diagnostic_message}", file=sys.stderr)
        _print_attempt_paths(exc)
        return EXIT_DATA_ERROR

    progress.finish("success")
    # Console delivery happens after the committed outcome. A broken output
    # pipe cannot append to or rewrite a completed invocation's evidence.
    if args.command == "download":
        print(f"ok: {len(result['datasets'])} cached session(s)")
        print(f"manifest: {manifest}")
    else:
        print(f"ok: {len(summary['fills'])} simulated fill(s)")
        print(f"run_id: {run_id}\ntrace: {trace}\nmanifest: {manifest}")
    return EXIT_SUCCESS


def _cmd_check_data(args):
    from rsys.application.preflight import check_data

    try:
        result, manifest = check_data(None, args.out, input_path=args.config)
    except Exception as exc:  # noqa: BLE001 -- retained local input/evidence failures
        print(
            f"error: {type(exc).__name__}: {getattr(exc, 'diagnostic_message', 'Unable to complete data preflight')}",
            file=sys.stderr,
        )
        _print_attempt_paths(exc)
        return (
            EXIT_OTHER_FAILURE
            if isinstance(exc, (OSError, DiagnosticWriteError))
            else EXIT_DATA_ERROR
        )
    print(f"can_execute: {str(result['can_execute']).lower()}")
    print(
        f"history_coverage_complete: {str(result['history_coverage_complete']).lower()}"
    )
    print(f"warnings: {len(result['warnings'])}")
    print(f"manifest: {manifest}")
    print(f"report: {manifest.parent / 'report.html'}")
    return EXIT_SUCCESS if result["can_execute"] else EXIT_DATA_ERROR


def _cmd_validate_alpaca(args):
    from rsys.application.provider_validation import validate_sample

    try:
        result, manifest = validate_sample(
            args.config, args.out, enable_live=args.enable_live, refresh=args.refresh
        )
    except Exception as exc:  # noqa: BLE001 -- retained input/provider/persistence failure
        print(
            f"error: {type(exc).__name__}: {getattr(exc, 'diagnostic_message', 'Unable to complete provider validation')}",
            file=sys.stderr,
        )
        _print_attempt_paths(exc)
        return (
            EXIT_OTHER_FAILURE
            if isinstance(exc, (OSError, DiagnosticWriteError))
            else EXIT_DATA_ERROR
        )
    print(f"software_status: {result['status']}")
    print(f"real_provider_gate: {result['real_provider_gate']}")
    print(f"manifest: {manifest}")
    print(f"pinned_backtest: {manifest.parent / 'backtest.json'}")
    return EXIT_SUCCESS


def _cmd_inspect(args):
    try:
        if args.command == "status":
            result = asdict(inspect_attempt(args.storage_root, args.id))
        else:
            result = {
                "mode": "apply" if args.apply else "dry_run",
                "attempts": [
                    asdict(item)
                    for item in recover_attempts(
                        args.storage_root, args.id, apply=args.apply
                    )
                ],
            }
    except (ValueError, OSError, sqlite3.Error, RecoveryInProgressError) as exc:
        print(f"error: cannot {args.command}: {exc}", file=sys.stderr)
        return EXIT_OTHER_FAILURE
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif args.command == "status":
        for key in (
            "attempt_id",
            "invocation_id",
            "run_id",
            "command",
            "outcome",
            "completeness",
            "owner_state",
            "stage",
            "last_observed_at",
            "error",
            "explanation",
        ):
            # Escape control characters in saved input/error text for terminal safety.
            value = json.dumps(result[key], ensure_ascii=True)
            print(f"{key}: {value}")
        for key, value in result["paths"].items():
            print(f"{key}: {json.dumps(value)}")
        print(f"progress: {json.dumps(result['progress'], sort_keys=True)}")
    else:
        print(f"{result['mode']}: {len(result['attempts'])} attempt(s)")
        for item in result["attempts"]:
            verb = "marked crashed" if item["applied"] else item["action"]
            print(f"{item['attempt_id']}: {verb}; {item['reason']}")
    # This is successful inspection/recovery, never a successful simulation claim.
    return EXIT_SUCCESS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rsys")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate", help="Validate types, dependencies and unresolved decisions"
    )
    validate.add_argument("--config", required=True)
    validate.set_defaults(func=_cmd_validate)

    replay = subparsers.add_parser(
        "replay", help="Run an offline synthetic replay (data-only, R3)"
    )
    replay.add_argument("--config", required=True)
    replay.add_argument(
        "--out",
        default="auto",
        help="Output directory, or 'auto' to derive one from --config",
    )
    replay.set_defaults(func=_cmd_replay)

    fixture = subparsers.add_parser(
        "fixture", help="Run F01-F17 synthetic account/execution fixture cases"
    )
    fixture.add_argument("--case", required=True, help="Case id (e.g. F01) or 'all'")
    fixture.add_argument(
        "--out", default="auto", help="Output directory, or 'auto' to derive one"
    )
    fixture.set_defaults(func=_cmd_fixture)

    run = subparsers.add_parser(
        "run",
        help="Run synthetic ORB/VWAP bar or trade sessions with retained evidence",
    )
    run.add_argument(
        "--config",
        required=True,
        help="Path to a version-1 single-session or version-2 sessions JSON config",
    )
    run.add_argument(
        "--out", default="auto", help="Output directory, or 'auto' to derive one"
    )
    run.add_argument(
        "--report",
        action="store_true",
        help="Also render a portable report.html alongside the attempt",
    )
    run.set_defaults(func=_cmd_run)

    compare = subparsers.add_parser(
        "compare",
        help="Compare two retained successful session runs or historical backtests",
    )
    compare.add_argument(
        "--storage-root",
        required=True,
        help="The --out directory used by the two run/backtest invocations",
    )
    compare.add_argument(
        "--runs", nargs=2, required=True, metavar=("RUN_ID_A", "RUN_ID_B")
    )
    compare.add_argument(
        "--permitted-difference",
        default=None,
        help="The one assumption field allowed to differ",
    )
    compare.add_argument(
        "--out", required=True, help="Output directory for comparison.json"
    )
    compare.set_defaults(func=_cmd_compare)

    download = subparsers.add_parser(
        "download", help="Download Alpaca minute bars into a versioned local cache"
    )
    download.add_argument("--config", required=True)
    download.add_argument(
        "--out", required=True, help="Retained download attempt directory"
    )
    download.add_argument(
        "--refresh",
        action="store_true",
        help="Save a new snapshot; preserve old objects",
    )
    download.set_defaults(func=_cmd_data)
    backtest = subparsers.add_parser(
        "backtest", help="Run an offline backtest from pinned cached historical bars"
    )
    backtest.add_argument("--config", required=True)
    backtest.add_argument("--out", required=True)
    backtest.add_argument("--report", action="store_true")
    backtest.set_defaults(func=_cmd_data)

    for command in (run, backtest, download):
        command.add_argument(
            "--progress",
            choices=("auto", "on", "off"),
            default="auto",
            help="Human progress on stderr (auto: interactive terminals only)",
        )

    preflight = subparsers.add_parser(
        "check-data",
        help="Check pinned historical data locally without downloading or simulating",
    )
    preflight.add_argument("--config", required=True)
    preflight.add_argument("--out", required=True)
    preflight.set_defaults(func=_cmd_check_data)

    provider = subparsers.add_parser(
        "validate-alpaca",
        help="Explicitly enabled bounded provider sample, cache reuse and offline validation",
    )
    provider.add_argument("--config", required=True, help="Local sample request JSON")
    provider.add_argument("--out", required=True)
    provider.add_argument(
        "--enable-live",
        action="store_true",
        help="Also requires RSYS_ENABLE_ALPACA_VALIDATION=1",
    )
    provider.add_argument(
        "--refresh",
        action="store_true",
        help="Explicit new acquisition; old objects remain",
    )
    provider.set_defaults(func=_cmd_validate_alpaca)

    status = subparsers.add_parser(
        "status", help="Inspect an invocation or run without changing it"
    )
    status.add_argument("--storage-root", required=True)
    status.add_argument("--id", required=True, help="Invocation ID or run ID")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_cmd_inspect)
    recover = subparsers.add_parser(
        "recover", help="Preview or mark confirmed-dead local attempts crashed"
    )
    recover.add_argument("--storage-root", required=True)
    recover.add_argument("--id", help="Inspect only this invocation or run")
    recover.add_argument("--json", action="store_true")
    mode = recover.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Read-only preview")
    mode.add_argument(
        "--apply", action="store_true", help="Apply eligible recovery transitions"
    )
    recover.set_defaults(func=_cmd_inspect)

    for name in _NOT_IMPLEMENTED:
        stub = subparsers.add_parser(
            name, help="Blocked until final-evaluation requirements are resolved"
        )
        stub.set_defaults(func=lambda _args: _blocked_final_evaluate())

    return parser


def _not_implemented(name: str) -> int:
    print(f"error: 'rsys {name}' is not implemented yet", file=sys.stderr)
    return EXIT_OTHER_FAILURE


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

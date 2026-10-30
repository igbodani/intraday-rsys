"""Offline validation, replay, synthetic sessions, fixtures and evidence comparison."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rsys.application.config import load_config, validate_config
from rsys.application.errors import ConfigError, RunConfigError, UnsupportedRuleSetError
from rsys.application.models import ReplayConfig
from rsys.application.replay import run_replay_from_config
from rsys.application.run import run_cli_entry
from rsys.cli.errors import FixtureCliError
from rsys.cli.fixtures import run_fixture_cli
from rsys.data.errors import ReplayInputError
from rsys.execution.errors import IncompleteSessionError
from rsys.research.comparison import compare_run_pair
from rsys.research.errors import CompareCliError, ComparisonError

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
    out_dir = Path("runs") / "run" if args.out == "auto" else Path(args.out)
    try:
        summary, run_id, trace_path, manifest_path = run_cli_entry(
            args.config, out_dir, report=args.report
        )
    except RunConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
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
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; lifecycle already retained the failure
        print(
            f"error: run failed ({type(exc).__name__}); inspect retained attempt",
            file=sys.stderr,
        )
        _print_attempt_paths(exc)
        return EXIT_OTHER_FAILURE

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
        ("run_id", "run_id"),
        ("trace", "trace_path"),
        ("manifest", "manifest_path"),
    ):
        value = getattr(exc, attribute, None)
        if value is not None:
            print(f"{label}: {value}", file=sys.stderr)


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
        "compare", help="Compare two retained synthetic `rsys run` manifests"
    )
    compare.add_argument(
        "--storage-root",
        required=True,
        help="The --out directory used by the two `rsys run` invocations",
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

"""CLI entry point. `validate` (M0) and `replay` (R3-C) are implemented; other subcommands
from docs/contracts.md are registered but not yet built (later tickets)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rsys.config import ConfigError, ReplayConfig, load_config, validate_config
from rsys.replay_io import (
    ReplayInputError,
    UnsupportedRuleSetError,
    run_replay_from_config,
)

EXIT_SUCCESS = 0
EXIT_OTHER_FAILURE = 1
EXIT_INVALID_CONFIG = 2
EXIT_DATA_ERROR = 3
EXIT_INVARIANT_FAILURE = 4

_NOT_IMPLEMENTED = ("fixture", "run", "compare", "final-evaluate")


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

    print(f"blocked: {args.config} has {len(result.blockers)} unresolved/invalid field(s)", file=sys.stderr)
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
        print(f"error: {args.config} is not a replay-mode config (mode={config.mode!r})", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    result = validate_config(config)
    if not result.ok:
        print(f"blocked: {args.config} has {len(result.blockers)} unresolved/invalid field(s)", file=sys.stderr)
        for blocker in result.blockers:
            print(f"  - {blocker.field_path}: {blocker.reason}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    out_dir = Path(args.config).resolve().parent.parent / "runs" / "replay" / Path(args.config).stem \
        if args.out == "auto" else Path(args.out)

    try:
        result = run_replay_from_config(config, out_dir)
    except (ReplayInputError, UnsupportedRuleSetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    except (ValueError, RuntimeError) as exc:
        print(f"error: replay failed ({type(exc).__name__}); inspect retained attempt", file=sys.stderr)
        return EXIT_INVARIANT_FAILURE
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; lifecycle already retained the failure
        print(f"error: replay failed ({type(exc).__name__}); inspect retained attempt", file=sys.stderr)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rsys")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate types, dependencies and unresolved decisions")
    validate.add_argument("--config", required=True)
    validate.set_defaults(func=_cmd_validate)

    replay = subparsers.add_parser("replay", help="Run an offline synthetic replay (data-only, R3)")
    replay.add_argument("--config", required=True)
    replay.add_argument("--out", default="auto", help="Output directory, or 'auto' to derive one from --config")
    replay.set_defaults(func=_cmd_replay)

    for name in _NOT_IMPLEMENTED:
        stub = subparsers.add_parser(name, help="Not implemented yet (later ticket)")
        stub.set_defaults(func=lambda _args, _name=name: _not_implemented(_name))

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

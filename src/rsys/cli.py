"""CLI entry point. Only `validate` is implemented in M0; other subcommands
from docs/contracts.md are registered but not yet built (later tickets)."""
from __future__ import annotations

import argparse
import sys

from rsys.config import ConfigError, load_config, validate_config

EXIT_SUCCESS = 0
EXIT_OTHER_FAILURE = 1
EXIT_INVALID_CONFIG = 2

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rsys")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate types, dependencies and unresolved decisions")
    validate.add_argument("--config", required=True)
    validate.set_defaults(func=_cmd_validate)

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

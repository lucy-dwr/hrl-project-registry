from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .registry import validate_file

DEFAULT_REGISTRY = Path("project-id-registry.csv")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hrl-project-registry",
        description="Validate the HRL program-assigned project-ID registry.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser(
        "validate", help="Validate project-id-registry.csv, and the change from --base when given."
    )
    validate.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_REGISTRY,
        help="Path to project-id-registry.csv (default: %(default)s).",
    )
    validate.add_argument(
        "--base",
        type=Path,
        default=None,
        help="Prior approved project-id-registry.csv to check the change against "
        "(in CI, the copy from origin/main).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    result = validate_file(args.path, args.base)

    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if result.errors:
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        print(f"\n{len(result.errors)} error(s); registry is invalid.", file=sys.stderr)
        return 1

    print(f"OK: {len(result.rows)} project ID(s), no errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

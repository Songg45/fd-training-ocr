"""Command-line shell for the checkpointed extraction pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

from .config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fd-training-ocr")
    parser.add_argument("--config", type=Path, help="Path to a local TOML configuration file")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("inspect-config", help="Print effective non-secret configuration")
    process = subcommands.add_parser("process", help="Process one PDF or directory (future checkpoint)")
    process.add_argument("source", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if args.command == "inspect-config":
        print(json.dumps(config.as_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "process":
        print(
            "PDF processing is not implemented in Checkpoint 1; the source was not read or modified.",
            file=sys.stderr,
        )
        return 2
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

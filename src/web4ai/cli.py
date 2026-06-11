"""CLI entry point."""

from __future__ import annotations

import argparse
import sys

from web4ai.server import serve
from web4ai.settings import ServerSettings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web4ai",
        description="web4AI extraction API server",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("dev", help="Local development server with auto-reload on port 8000")
    sub.add_parser(
        "serve",
        help="Production server (reads WEB4AI_HOST/WEB4AI_PORT; used in Cloudflare Containers)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "dev":
        serve(ServerSettings.for_dev())
        return
    if args.command == "serve":
        serve(ServerSettings())
        return
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main(sys.argv[1:])

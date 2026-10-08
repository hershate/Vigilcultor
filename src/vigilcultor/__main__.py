"""CLI entry point: ``vigilcultor <command>``."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vigilcultor",
        description="Self-hosted AI caretaker for GitHub repositories",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("web", help="run the web panel")
    sub.add_parser("init-db", help="create database tables")
    sub.add_parser("brand-check", help="scan the source tree for forbidden brand terms")

    args = parser.parse_args(argv)

    if args.command == "web":
        import uvicorn

        from vigilcultor.webapp.app import create_app

        uvicorn.run(create_app(), host="127.0.0.1", port=8000)
        return 0
    if args.command == "init-db":
        from vigilcultor.store.db import init_db

        init_db()
        return 0
    if args.command == "brand-check":
        from vigilcultor.branding import check

        return check()

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

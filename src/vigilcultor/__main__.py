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
    sub.add_parser("scheduler", help="run the polling scheduler loop")
    sub.add_parser("netproxy", help="run the egress audit proxy")
    sub.add_parser("all", help="run web + scheduler + netproxy in one process (dev)")
    sub.add_parser("init-db", help="create database tables")
    sub.add_parser("brand-check", help="scan the source tree for forbidden brand terms")

    args = parser.parse_args(argv)

    if args.command == "web":
        import uvicorn

        from vigilcultor.webapp.app import create_app

        uvicorn.run(create_app(), host="127.0.0.1", port=8000)
        return 0
    if args.command == "scheduler":
        import time

        from vigilcultor.scheduler.loop import run_loop

        try:
            run_loop()
        except KeyboardInterrupt:
            pass
        return 0
    if args.command == "netproxy":
        import time

        from vigilcultor.config import data_dir
        from vigilcultor.netproxy.defaults import effective_allowlist
        from vigilcultor.netproxy.endpoint import announce
        from vigilcultor.netproxy.server import EgressProxyServer, default_bind_host

        bind = default_bind_host()
        proxy = EgressProxyServer(
            effective_allowlist(), data_dir() / "netproxy.jsonl", bind_host=bind
        )
        proxy.start()
        announce(bind, proxy.port)
        print(f"netproxy listening on {proxy.proxy_url}")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            proxy.stop()
        return 0
    if args.command == "all":
        import threading
        import time

        import uvicorn

        from vigilcultor.config import data_dir
        from vigilcultor.netproxy.defaults import effective_allowlist
        from vigilcultor.netproxy.endpoint import announce
        from vigilcultor.netproxy.server import EgressProxyServer, default_bind_host
        from vigilcultor.scheduler.loop import run_loop
        from vigilcultor.webapp.app import create_app

        bind = default_bind_host()
        proxy = EgressProxyServer(
            effective_allowlist(), data_dir() / "netproxy.jsonl", bind_host=bind
        )
        proxy.start()
        announce(bind, proxy.port)
        print(f"netproxy on {proxy.proxy_url}")
        threading.Thread(target=run_loop, daemon=True).start()
        try:
            uvicorn.run(create_app(), host="127.0.0.1", port=8000)
        except KeyboardInterrupt:
            pass
        finally:
            proxy.stop()
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

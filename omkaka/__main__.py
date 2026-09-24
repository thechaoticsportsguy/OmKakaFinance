"""Command line entry point.  Usage (from the project folder):

    python -m omkaka init          create the live database (safe to repeat)
    python -m omkaka app           open the live dashboard in your browser
    python -m omkaka demo          open the OFFLINE DEMO dashboard
    python -m omkaka demo-reset    rebuild the demo database from fixtures
    python -m omkaka verify        check the live journal has not been tampered with
    python -m omkaka status        show settings and which secrets are set (never values)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from . import db, demo, journal
from .budget import budget_summary
from .config import PROJECT_ROOT, load_settings, secret_status

APP_FILE = PROJECT_ROOT / "app.py"


def _init_live() -> None:
    s = load_settings("live")
    db.init_db(s.db_path, "live")
    print(f"Live database ready: {s.db_path}")


def _launch(mode: str) -> int:
    env = dict(os.environ, OMKAKA_MODE=mode)
    print(f"Starting dashboard in {mode.upper()} mode. Press Ctrl+C in this window to stop.")
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run", str(APP_FILE),
         "--browser.gatherUsageStats", "false", "--server.address", "localhost"],
        env=env, cwd=str(PROJECT_ROOT),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m omkaka", description="OmKakaFinance")
    parser.add_argument("command", choices=["init", "app", "demo", "demo-reset", "verify", "status"])
    parser.add_argument("--demo", action="store_true", help="use the demo database (verify/status)")
    args = parser.parse_args(argv)

    if args.command == "init":
        _init_live()
    elif args.command == "app":
        _init_live()
        return _launch("live")
    elif args.command == "demo":
        s = load_settings("demo")
        if not s.db_path.exists():
            print(f"Demo database built: {demo.reset_demo_db(s)}")
        return _launch("demo")
    elif args.command == "demo-reset":
        print(f"Demo database rebuilt: {demo.reset_demo_db(load_settings('demo'))}")
    elif args.command == "verify":
        mode = "demo" if args.demo else "live"
        s = load_settings(mode)
        conn = db.connect(s.db_path, mode)
        ok, problems = journal.verify_chain(conn)
        count = len(journal.list_entries(conn))
        conn.close()
        print(f"{mode} journal: {count} entries. " + ("Integrity OK." if ok else "PROBLEMS FOUND:"))
        for p in problems:
            print("  -", p)
        return 0 if ok else 1
    elif args.command == "status":
        s = load_settings("demo" if args.demo else "live")
        print(f"Mode: {s.mode}\nDatabase: {s.db_path} (exists: {s.db_path.exists()})")
        for k, v in budget_summary(s).items():
            print(f"Budget {k}: {v}")
        for name, is_set in secret_status().items():
            print(f"{name}: {'set' if is_set else 'not set'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

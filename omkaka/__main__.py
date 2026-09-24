"""Command line entry point.  Usage (from the project folder):

    python -m omkaka init            create/upgrade the live database (safe to repeat)
    python -m omkaka app             open the live dashboard in your browser
    python -m omkaka demo            open the OFFLINE DEMO dashboard (fictional data)
    python -m omkaka demo-reset      rebuild the demo database from fixtures
    python -m omkaka sources-check   one small FREE test call to each data source
    python -m omkaka screen          run a full screening (free sources; takes several minutes)
    python -m omkaka packet          write the research packet for the latest screening run
    python -m omkaka watch add TICKER [note]  |  watch remove TICKER  |  watch list
    python -m omkaka verify          check the live journal has not been tampered with
    python -m omkaka status          show settings and which secrets are set (never values)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import timedelta

from . import db, demo, journal, store
from .budget import budget_summary
from .config import PROJECT_ROOT, load_settings, secret_status

APP_FILE = PROJECT_ROOT / "app.py"


def _live_conn():
    s = load_settings("live")
    db.init_db(s.db_path, "live")
    return s, db.connect(s.db_path, "live")


def _launch(mode: str) -> int:
    env = dict(os.environ, OMKAKA_MODE=mode)
    print(f"Starting dashboard in {mode.upper()} mode. Press Ctrl+C in this window to stop.")
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run", str(APP_FILE),
         "--browser.gatherUsageStats", "false", "--server.address", "localhost"],
        env=env, cwd=str(PROJECT_ROOT),
    )


def _sources_check() -> int:
    """Tiny live test of each free source. Recorded as a normal run so it shows in Source health."""
    from .pipeline import build_clients, recent_weekdays
    from .timeutil import NEW_YORK, utc_now

    s, conn = _live_conn()
    now = utc_now()
    run_id = store.start_run(conn, "manual", now + timedelta(minutes=30), {"purpose": "sources-check"}, now=now)
    c = build_clients(conn, s)
    day = recent_weekdays(now.astimezone(NEW_YORK).date(), 1)[0]
    checks = [
        ("SEC company list", lambda: c.sec.company_tickers(), None),
        ("Market data (end of day)", lambda: c.massive.grouped_daily(day), None),
        ("News", lambda: c.finnhub.company_news("AAPL", day - timedelta(days=3), day), "AAPL"),
        ("Reddit", lambda: c.reddit.search("AAPL", None), "AAPL"),
    ]
    for name, call, ticker in checks:
        r = call()
        store.record_source_check(conn, run_id, name, r.provider, r.status, reason=r.reason, ticker=ticker,
                                  query=r.url, fetched_at=r.fetched_at)
        print(f"{name:28} {r.status.value:15} {r.reason or ''}")
    store.finish_run(conn, run_id, "completed", now=utc_now())
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m omkaka", description="OmKakaFinance")
    parser.add_argument("command", choices=["init", "app", "demo", "demo-reset", "sources-check", "screen",
                                            "packet", "watch", "verify", "status"])
    parser.add_argument("args", nargs="*", help="extra arguments (watch add/remove/list TICKER)")
    parser.add_argument("--demo", action="store_true", help="use the demo database (verify/status/packet)")
    parser.add_argument("--run", help="run id (packet)")
    a = parser.parse_args(argv)

    if a.command == "init":
        s, conn = _live_conn()
        conn.close()
        print(f"Live database ready: {s.db_path}")
    elif a.command == "app":
        _live_conn()[1].close()
        return _launch("live")
    elif a.command == "demo":
        s = load_settings("demo")
        if not s.db_path.exists():
            print(f"Demo database built: {demo.reset_demo_db(s)}")
        else:
            db.init_db(s.db_path, "demo")  # apply any upgrades
        return _launch("demo")
    elif a.command == "demo-reset":
        print(f"Demo database rebuilt: {demo.reset_demo_db(load_settings('demo'))}")
    elif a.command == "sources-check":
        return _sources_check()
    elif a.command == "screen":
        from .pipeline import build_clients, run_screen

        s, conn = _live_conn()
        summary = run_screen(conn, s, build_clients(conn, s))
        conn.close()
        print(f"\nRun {summary['run_id']}: {summary.get('status')}")
        for k in ("universe", "latest_trade_date", "prefilter", "shortlist"):
            if k in summary:
                print(f"  {k}: {summary[k]}")
        for p in summary.get("source_problems", []):
            print(f"  ! {p}")
        if summary.get("status") == "completed":
            print("\nNext: python -m omkaka packet")
            return 0
        print("\nThe run stopped. Fix the problem above (see README > Troubleshooting) and run it again.")
        return 1
    elif a.command == "packet":
        from .packet import write_packet

        mode = "demo" if a.demo else "live"
        s = load_settings(mode)
        conn = db.connect(s.db_path, mode)
        try:
            path, meta = write_packet(conn, s, s.db_path.parent / "packets", a.run)
        except LookupError as exc:
            print(exc)
            return 1
        finally:
            conn.close()
        print(f"Packet written: {path}\nCompanies: {', '.join(meta['companies']) or 'none'}")
    elif a.command == "watch":
        s, conn = _live_conn()
        if a.args[:1] in (["add"], ["remove"]) and len(a.args) >= 2:
            store.watchlist_change(conn, a.args[1], a.args[0], note=" ".join(a.args[2:]) or None)
        print("Watchlist:", ", ".join(store.current_watchlist(conn)) or "(empty)")
        conn.close()
    elif a.command == "verify":
        mode = "demo" if a.demo else "live"
        s = load_settings(mode)
        conn = db.connect(s.db_path, mode)
        ok, problems = journal.verify_chain(conn)
        count = len(journal.list_entries(conn))
        conn.close()
        print(f"{mode} journal: {count} entries. " + ("Integrity OK." if ok else "PROBLEMS FOUND:"))
        for p in problems:
            print("  -", p)
        return 0 if ok else 1
    elif a.command == "status":
        s = load_settings("demo" if a.demo else "live")
        print(f"Mode: {s.mode}\nDatabase: {s.db_path} (exists: {s.db_path.exists()})")
        for k, v in budget_summary(s).items():
            print(f"Budget {k}: {v}")
        for name, is_set in secret_status().items():
            print(f"{name}: {'set' if is_set else 'not set'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

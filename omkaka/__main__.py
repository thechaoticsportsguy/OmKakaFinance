"""Command line entry point.  Usage (from the project folder):

    python -m omkaka init            create/upgrade the live database (safe to repeat)
    python -m omkaka app             open the live dashboard in your browser
    python -m omkaka demo            open the OFFLINE DEMO dashboard (fictional data)
    python -m omkaka demo-reset      rebuild the demo database from fixtures
    python -m omkaka sources-check   one small FREE test call to each data source
    python -m omkaka screen          run a full screening (free sources; takes several minutes)
    python -m omkaka packet          write the research packet for the latest screening run
    python -m omkaka watch add TICKER [note]  |  watch remove TICKER  |  watch list
    python -m omkaka review FILE     check a brief (yours or Claude's) and save it to the journal
    python -m omkaka daily           the daily job (safe to run any time; see README)
    python -m omkaka schedule install [--wake] | uninstall | status   Windows Task Scheduler
    python -m omkaka paper deposit AMOUNT | withdraw AMOUNT | buy TICKER QTY | sell TICKER QTY
                           | fill | status | dividend TICKER EXDATE AMOUNT SOURCE | split TICKER EXDATE RATIO SOURCE
    python -m omkaka backup          safe copy of the live database into data\backups
    python -m omkaka doctor          check the whole setup and explain problems
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
         "--browser.gatherUsageStats", "false", "--server.address", "localhost",
         "--server.port", os.environ.get("STREAMLIT_SERVER_PORT", "8502" if mode == "demo" else "8501")],
        env=env, cwd=str(PROJECT_ROOT),
    )


def _sources_check() -> int:
    """Tiny live test of each free source. Recorded as a normal run so it shows in Source health."""
    from .setup import check_connections
    s, conn = _live_conn()
    try:
        rows = check_connections(conn, s)
    finally:
        conn.close()
    for row in rows:
        print(f"{row['source']:28} {row['status']:15} {row['detail']}")
    return 0 if all(r["passed"] for r in rows if r["required"]) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m omkaka", description="OmKakaFinance")
    parser.add_argument("command", choices=["init", "app", "demo", "demo-reset", "sources-check", "screen",
                                            "packet", "watch", "review", "daily", "schedule", "paper", "backup", "doctor", "verify", "status"])
    parser.add_argument("args", nargs="*", help="extra arguments (watch add/remove/list TICKER)")
    parser.add_argument("--demo", action="store_true", help="use the demo database (verify/status/packet)")
    parser.add_argument("--run", help="run id (packet)")
    parser.add_argument("--scheduled", action="store_true", help="daily: started by Task Scheduler")
    parser.add_argument("--brief", help="paper buy/sell: journal entry id of the research brief behind it")
    parser.add_argument("--wake", action="store_true", help="schedule install: allow waking a sleeping PC")
    parser.add_argument("--retry-failed", action="store_true", help="daily: retry a failed result while preserving its history")
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
    elif a.command == "review":
        from pathlib import Path

        from . import review as rv

        if not a.args:
            print("Usage: python -m omkaka review path\\to\\brief.md [--run RUN_ID]")
            return 1
        mode = "demo" if a.demo else "live"
        s = load_settings(mode)
        conn = db.connect(s.db_path, mode)
        text = Path(a.args[0]).read_text(encoding="utf-8")
        report = rv.validate(conn, text, a.run)
        print(f"Check result: {report.status.upper()}")
        for e in report.errors:
            print("  ERROR  ", e)
        for w in report.warnings:
            print("  warning", w)
        entry = rv.save_brief(conn, text, report, "Brief checked from file " + Path(a.args[0]).name)
        conn.close()
        print(f"Saved to journal as {entry}" + (" (as a REJECTED note)" if report.status == "rejected" else ""))
        return 0 if report.status != "rejected" else 1
    elif a.command == "daily":
        from .daily import daily_job
        from .pipeline import build_clients
        from .timeutil import format_new_york, utc_now

        s, conn = _live_conn()
        from .setup import missing_credentials
        if missing_credentials():
            conn.close()
            print("Setup incomplete: add your SEC contact details and market/news keys in Setup & connections.")
            return 1
        stamp = lambda m: print(f"[{format_new_york(utc_now())}] {m}", flush=True)
        try:
            result = daily_job(conn, s, lambda: build_clients(conn, s),
                               run_kind="scheduled" if a.scheduled else "manual", log=stamp, retry_failed=a.retry_failed and not a.scheduled)
        finally:
            conn.close()
        stamp(f"{result['action']}: {result.get('reason') or result.get('candidate') or 'no qualifying candidate'}"
              + (" (LATE)" if result.get("late") else ""))
        return {"failed_final": 1, "retry_later": 2}.get(result["action"], 0)
    elif a.command == "schedule":
        from . import schedule

        action = a.args[0] if a.args else "status"
        if action == "install":
            try:
                print(schedule.install(PROJECT_ROOT, wake=a.wake))
            except (ValueError, RuntimeError) as exc:
                print(str(exc))
                return 1
        elif action == "uninstall":
            print(schedule.uninstall())
        else:
            print(schedule.status())
    elif a.command == "paper":
        from . import portfolio as pf

        s, conn = _live_conn()
        args = a.args
        try:
            if args[:1] == ["deposit"] or args[:1] == ["withdraw"]:
                pf.cash_movement(conn, "deposit" if args[0] == "deposit" else "withdrawal", float(args[1]))
            elif args[:1] in (["buy"], ["sell"]):
                oid = pf.place_order(conn, s, args[0], args[1], float(args[2]), linked_entry_id=a.brief)
                print(f"Order {oid} placed; it fills at the close of the first trading day ending after now.")
            elif args[:1] in (["dividend"], ["split"]):
                pf.record_corporate_action(conn, args[0], args[1], args[2], float(args[3]), " ".join(args[4:]))
            for f in pf.fill_pending(conn, s):
                print(f"Order {f['order_id']}: {f['status']} {f.get('reason') or ''}")
            v = pf.valuation(conn, s)
            print(f"Cash ${v['cash']:,.2f} | Total " + ("Data unavailable" if v["total"] is None else f"${v['total']:,.2f}"))
            for h in v["holdings"]:
                print(f"  {h['ticker']:8} {h['shares']:>10g} shares  value "
                      + ("Data unavailable" if h["value"] is None else f"${h['value']:,.2f}"))
        except (ValueError, IndexError) as exc:
            print(f"Could not do that: {exc}")
            return 1
        finally:
            conn.close()
    elif a.command == "backup":
        from .maintenance import backup_database

        s, conn = _live_conn()
        conn.close()
        print(f"Backup saved: {backup_database(s)}")
    elif a.command == "doctor":
        from .maintenance import doctor

        rows = doctor(load_settings("demo" if a.demo else "live"))
        for status, name, detail in rows:
            print(f"[{status:7}] {name}: {detail}")
        problems = [r for r in rows if r[0] == "PROBLEM"]
        warnings = [r for r in rows if r[0] == "WARN"]
        print(f"\n{len(problems)} problem(s) need attention." if problems else
              f"\nApp health passed; {len(warnings)} setup or data item(s) still need attention." if warnings else
              "\nAll essential checks passed.")
        return 1 if problems else 0
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

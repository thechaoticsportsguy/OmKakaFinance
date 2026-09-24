"""Backups and a health check (Phase 6).

    python -m omkaka backup   safe copy of the database into data/backups (keeps the newest N)
    python -m omkaka doctor   checks your setup and explains any problem in plain English
"""
from __future__ import annotations

import importlib
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from . import db, journal
from .config import Settings, secret_status
from .timeutil import NEW_YORK, format_new_york, utc_now


def backup_database(settings: Settings, now: datetime | None = None) -> Path:
    """Copy the database with SQLite's online backup (safe even while the app is open)."""
    now = now or utc_now()
    src = settings.db_path
    if not src.exists():
        raise FileNotFoundError(f"No database at {src}")
    folder = src.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{src.stem}-{now.astimezone(NEW_YORK).strftime('%Y%m%d-%H%M%S')}.db"
    with sqlite3.connect(str(src)) as s_conn, sqlite3.connect(str(dest)) as d_conn:
        s_conn.backup(d_conn)
    check = sqlite3.connect(str(dest))
    ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    check.close()
    if not ok:
        dest.unlink(missing_ok=True)
        raise RuntimeError("Backup copy failed its integrity check; the original is untouched.")
    keep = int(settings.raw.get("backup", {}).get("keep_last", 30))
    copies = sorted(folder.glob(f"{src.stem}-*.db"))
    for old in copies[:-keep] if keep > 0 else []:
        old.unlink()  # only removes older BACKUP COPIES, never the live database
    return dest


def doctor(settings: Settings) -> list[tuple[str, str, str]]:
    """Returns (status, check, detail) rows. status is OK, WARN, or PROBLEM."""
    rows: list[tuple[str, str, str]] = []

    def add(ok, name, detail, warn=False):
        rows.append(("OK" if ok else ("WARN" if warn else "PROBLEM"), name, detail))

    add(sys.version_info >= (3, 11), "Python version", sys.version.split()[0] + " (need 3.11+)")
    for pkg in ("streamlit", "dotenv", "requests", "tzdata"):
        try:
            importlib.import_module(pkg)
            add(True, f"Package {pkg}", "installed")
        except ImportError:
            add(False, f"Package {pkg}", "missing: run  .venv\\Scripts\\python.exe -m pip install -r requirements.txt")

    path = settings.db_path
    if not path.exists():
        add(False, "Database", f"{path} not found: run  python -m omkaka init")
        return rows
    raw = sqlite3.connect(str(path))
    integrity = raw.execute("PRAGMA integrity_check").fetchone()[0]
    raw.close()
    add(integrity == "ok", "Database file integrity", integrity)
    db.init_db(path, settings.mode)  # applies any pending upgrade
    conn = db.connect(path, settings.mode)
    try:
        add(db.schema_version(conn) == db.SCHEMA_VERSION, "Database version", f"{db.schema_version(conn)}")
        ok, problems = journal.verify_chain(conn)
        add(ok, "Journal integrity", "no entry altered or removed" if ok else "; ".join(problems[:3]))

        secrets = secret_status()
        for name in ("SEC_USER_AGENT", "MARKET_DATA_API_KEY", "NEWS_API_KEY"):
            add(secrets[name], name, "set" if secrets[name] else "not set (see .env.example)", warn=True)
        reddit = all(secrets[n] for n in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"))
        add(reddit, "Reddit access", "set" if reddit else "not set: Reddit shows as Data unavailable (OK)", warn=True)

        from .daily import latest_daily_status
        from .market_calendar import previous_trading_day
        last = latest_daily_status(conn)
        if not last:
            add(False, "Daily result", "none yet (run  python -m omkaka daily  or install the schedule)", warn=True)
        else:
            today = utc_now().astimezone(NEW_YORK).date()
            fresh = last["trading_date"] >= previous_trading_day(today).isoformat()
            add(fresh, "Latest daily result", f"{last['title']} for {last['trading_date']}, available "
                f"{format_new_york(last['available_at'])}{' (LATE)' if last.get('late') else ''}", warn=True)
        missed = conn.execute("SELECT COUNT(*) FROM journal_entries WHERE dedupe_key LIKE 'missed-%'").fetchone()[0]
        add(missed == 0, "Missed daily runs", f"{missed} recorded (computer off/asleep at run time)", warn=True)
        failed = conn.execute("SELECT run_id, status_reason FROM runs WHERE status='failed' "
                              "ORDER BY started_at DESC LIMIT 1").fetchone()
        if failed:
            add(False, "Most recent failed run", f"{failed['run_id']}: {failed['status_reason']}", warn=True)
    finally:
        conn.close()

    backups = sorted((path.parent / "backups").glob("*.db"))
    add(bool(backups), "Backups", f"{len(backups)} copies, newest {backups[-1].name}" if backups
        else "none yet: run  python -m omkaka backup", warn=True)
    free_gb = shutil.disk_usage(path.parent).free / 1e9
    add(free_gb > 1, "Free disk space", f"{free_gb:.1f} GB", warn=True)
    log = path.parent / "logs" / "daily.log"
    if log.exists():
        add(log.stat().st_size < 50e6, "Daily log size", f"{log.stat().st_size / 1e6:.1f} MB", warn=True)
    if sys.platform == "win32":
        from .schedule import status
        out = status()
        add("OmKakaFinance" in out and "ERROR" not in out.upper(), "Scheduled task",
            "installed" if "OmKakaFinance" in out else "not installed: run install_schedule.bat", warn=True)
    return rows

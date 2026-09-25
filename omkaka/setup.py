"""Local setup, real connection checks, and readiness for daily research."""
from __future__ import annotations

import os
import re
import tempfile
from datetime import timedelta
from pathlib import Path

from dotenv import set_key

from . import config, store
from .market_calendar import previous_trading_day
from .models import Status
from .pipeline import build_clients, extra_closed
from .sources.finnhub import parse_company_news
from .sources.massive import parse_grouped_daily
from .sources.sec import parse_company_tickers
from .timeutil import NEW_YORK, parse_utc_iso, utc_now

REQUIRED = ("SEC_USER_AGENT", "MARKET_DATA_API_KEY", "NEWS_API_KEY")
REQUIRED_SOURCES = ("SEC company list", "Market data (end of day)", "News")


def missing_credentials() -> list[str]:
    return [name for name in REQUIRED if not config.get_secret(name)]


def save_credentials(values: dict[str, str], path: Path | None = None) -> int:
    """Preserve existing values for blank inputs; replace the file atomically."""
    updates = {}
    for name, value in values.items():
        if name not in REQUIRED:
            raise ValueError("This form only changes the three required connection settings.")
        if any(c in value for c in ("\n", "\r", "\x00")):
            raise ValueError("Each setting must fit on one line.")
        value = value.strip()
        if not value:
            continue
        if name == "SEC_USER_AGENT" and not re.search(r"\S+\s+.*[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("For SEC contact details, enter your name followed by your email address.")
        updates[name] = value
    if not updates:
        return 0
    path = path or config.PROJECT_ROOT / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    staging = Path(temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(path.read_text(encoding="utf-8") if path.exists() else "")
        for name, value in updates.items():
            set_key(staging, name, value, quote_mode="always")
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)
    return len(updates)


def check_connections(conn, settings, clients=None) -> list[dict]:
    """One small request per service; missing/invalid responses fail honestly."""
    if settings.is_demo:
        raise ValueError("Connection checks run only in the live workspace.")
    clients = clients or build_clients(conn, settings)
    # A cached response cannot prove that newly entered credentials work.
    clients.http.bypass_cache = True
    now = clients.http.now()
    day = previous_trading_day(now.astimezone(NEW_YORK).date(), extra_closed(settings))
    run_id = store.start_run(conn, "manual", now + timedelta(minutes=30),
                             {"purpose": "sources-check"}, now=now)
    checks = [
        ("SEC company list", clients.sec.company_tickers, parse_company_tickers, None),
        ("Market data (end of day)", lambda: clients.massive.grouped_daily(day), parse_grouped_daily, None),
        ("News", lambda: clients.finnhub.company_news("AAPL", day - timedelta(days=3), day), parse_company_news, "AAPL"),
        ("Reddit", lambda: clients.reddit.search("AAPL", None), None, "AAPL"),
    ]
    rows = []
    for name, call, parser, ticker in checks:
        r = call()
        count = None
        if r.ok and parser:
            try:
                parsed = parser(r.data)
                count = len(parsed)
                if not parsed:
                    r.status = Status.NO_RESULTS
                    r.reason = "No records were returned for this check."
            except (TypeError, ValueError, KeyError, IndexError, AttributeError):
                r.status, r.reason = Status.FAILED, "The source returned an unexpected data format."
        passed = r.status is Status.OK or (name == "News" and r.status is Status.NO_RESULTS)
        store.record_source_check(conn, run_id, name, r.provider, r.status, reason=r.reason,
                                  ticker=ticker, query=r.url, fetched_at=r.fetched_at, result_count=count)
        rows.append({"source": name, "status": r.status.value, "detail": r.reason or "Connection verified.",
                     "passed": passed, "required": name in REQUIRED_SOURCES})
    ok = all(r["passed"] for r in rows if r["required"])
    store.finish_run(conn, run_id, "completed" if ok else "failed",
                     reason=None if ok else "Required data connections are not ready.", now=clients.http.now())
    return rows


def readiness(conn, settings, now=None) -> dict:
    """Require recent verified connections and a successful screen before scheduling."""
    now = now or utc_now()
    missing = missing_credentials()
    checks = conn.execute("SELECT * FROM runs WHERE json_extract(settings_json, '$.purpose')='sources-check' "
                          "ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    env_path = config.PROJECT_ROOT / ".env"
    changed = env_path.exists() and checks is not None and env_path.stat().st_mtime > parse_utc_iso(checks["started_at"]).timestamp()
    verified = bool(checks and checks["status"] == "completed" and not changed and
                    now - parse_utc_iso(checks["started_at"]) < timedelta(hours=24))
    if verified:
        observed = {r["source"]: r["status"] for r in conn.execute(
            "SELECT source, status FROM source_checks WHERE run_id=?", (checks["run_id"],))}
        verified = all(observed.get(s) == "OK" or (s == "News" and observed.get(s) == "NO_RESULTS")
                       for s in REQUIRED_SOURCES)
    screen = conn.execute("SELECT r.* FROM runs r WHERE r.status='completed' AND EXISTS "
                          "(SELECT 1 FROM screening_results s WHERE s.run_id=r.run_id) "
                          "ORDER BY r.started_at DESC LIMIT 1").fetchone()
    screened = bool(screen and now - parse_utc_iso(screen["started_at"]) < timedelta(hours=24))
    reasons = []
    if missing:
        reasons.append("Add the three required connection settings.")
    if not verified:
        reasons.append("Check the live connections successfully (within the last 24 hours).")
    if not screened:
        reasons.append("Complete a live research run successfully.")
    return {"missing": missing, "verified": verified, "screened": screened,
            "ready": not settings.is_demo and not reasons, "reasons": reasons}

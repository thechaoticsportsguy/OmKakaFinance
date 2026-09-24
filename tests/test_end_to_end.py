"""Phase 6: a simulated week of the whole workflow, offline.

Oct 29 - Nov 4 2026 (clocks fall back on Sunday Nov 1): daily runs, a missed day,
a late run, a duplicate trigger, a paper trade on a candidate, and final integrity checks.
"""
import itertools
import json
from datetime import datetime, timedelta, timezone

from omkaka import journal, portfolio
from omkaka.config import load_settings
from omkaka.daily import daily_job
from omkaka.pipeline import build_clients
from omkaka.sources.fixtures import FixtureTransport
from omkaka.timeutil import NEW_YORK


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NEW_YORK).astimezone(timezone.utc)


def run_day(conn, s, now, tmp_path):
    tick = itertools.count()
    make = lambda: build_clients(conn, s, transport=FixtureTransport(now.astimezone(NEW_YORK).date()),
                                 sleep=lambda x: None, now=lambda: now + timedelta(seconds=next(tick)),
                                 demo_credentials=True)
    return daily_job(conn, s, make, now=now, log=lambda m: None, lock_path=tmp_path / "lock")


def test_simulated_week(live_conn, tmp_path):
    s = load_settings("live")
    conn = live_conn
    results = {
        "thu": run_day(conn, s, at(2026, 10, 29, 4, 40), tmp_path),
        "thu-dup": run_day(conn, s, at(2026, 10, 29, 5, 10), tmp_path),
        # Friday Oct 30: computer off all day (no call).
        "sat": run_day(conn, s, at(2026, 10, 31, 4, 40), tmp_path),
        "mon": run_day(conn, s, at(2026, 11, 2, 4, 40), tmp_path),     # first day after DST ends
        "tue-late": run_day(conn, s, at(2026, 11, 3, 9, 20), tmp_path),
    }
    assert results["thu"]["action"] == "done"
    assert results["thu-dup"]["action"] == "skipped"
    assert "Saturday" in results["sat"]["reason"]
    assert results["mon"]["action"] == "done" and results["mon"]["late"] is False
    assert results["tue-late"]["late"] is True

    entries = journal.list_entries(conn)
    daily = [e for e in entries if (e["dedupe_key"] or "").startswith("daily-")]
    assert [e["dedupe_key"] for e in daily] == ["daily-2026-10-29", "daily-2026-11-02", "daily-2026-11-03"]
    assert daily[-1]["title"].startswith("LATE")
    assert [e["dedupe_key"] for e in entries if (e["dedupe_key"] or "").startswith("missed-")] == ["missed-2026-10-30"]
    for e in daily:
        assert json.loads(e["payload_json"])["validation"]["status"] in ("passed", "flagged")

    # Paper trade on Monday's candidate, placed by the user after reading it.
    mon = json.loads(daily[1]["payload_json"])
    portfolio.cash_movement(conn, "deposit", 50_000, now=at(2026, 11, 2, 8, 0))
    assert mon.get("candidate"), "Monday should produce a candidate in the fixture market"
    if mon.get("candidate"):
        portfolio.place_order(conn, s, "buy", mon["candidate"], 100, linked_entry_id=daily[1]["entry_id"],
                              now=at(2026, 11, 2, 8, 5))
        fills = portfolio.fill_pending(conn, s, now=at(2026, 11, 3, 9, 30))  # Monday close fetched Tuesday
        assert fills and fills[0]["status"] == "filled" and fills[0]["price_date"] == "2026-11-02"
    h = portfolio.history(conn, s, now=at(2026, 11, 3, 9, 30))
    assert h and h[-1]["contributions"] == 50_000 and h[-1]["spy_mirror"] > 0

    # Nothing was silently rewritten.
    assert journal.verify_chain(conn) == (True, [])
    assert conn.execute("SELECT COUNT(*) FROM metric_values WHERE status!='OK' AND value IS NOT NULL").fetchone()[0] == 0
    for table in ("evidence", "metric_values", "source_checks", "journal_entries", "runs"):
        bad = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE fetched_at NOT LIKE '%+00:00'").fetchone()[0]
        assert bad == 0, table

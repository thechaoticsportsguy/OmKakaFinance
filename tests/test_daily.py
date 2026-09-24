"""Phase 4: the daily job's timing, duplicates, late/missed runs, retries, and lock."""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from omkaka import journal
from omkaka.config import load_settings
from omkaka.daily import Lock, daily_job
from omkaka.pipeline import build_clients
from omkaka.sources.fixtures import FixtureTransport
from omkaka.sources.http import TransportResponse
from omkaka.timeutil import NEW_YORK


def ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NEW_YORK).astimezone(timezone.utc)


def job(conn, now, tmp_path, transport=None):
    settings = load_settings("live")
    tick = itertools.count()

    def make():
        t = transport or FixtureTransport(now.astimezone(NEW_YORK).date())
        return build_clients(conn, settings, transport=t, sleep=lambda s: None,
                             now=lambda: now + timedelta(seconds=next(tick)), demo_credentials=True)
    return daily_job(conn, settings, make, now=now, log=lambda m: None, lock_path=tmp_path / "daily.lock")


def daily_entries(conn):
    return [e for e in journal.list_entries(conn) if (e["dedupe_key"] or "").startswith("daily-")]


def test_weekend_holiday_and_too_early_do_nothing(live_conn, tmp_path):
    assert "Saturday" in job(live_conn, ny(2026, 9, 26, 5, 0), tmp_path)["reason"]
    assert "Thanksgiving" in job(live_conn, ny(2026, 11, 26, 5, 0), tmp_path)["reason"]
    assert "too early" in job(live_conn, ny(2026, 9, 24, 4, 0), tmp_path)["reason"]
    assert daily_entries(live_conn) == []


def test_normal_day_publishes_once(live_conn, tmp_path):
    r = job(live_conn, ny(2026, 9, 24, 4, 35), tmp_path)
    assert r["action"] == "done" and r["late"] is False and r["check"] == "passed"
    again = job(live_conn, ny(2026, 9, 24, 5, 5), tmp_path)
    assert again["action"] == "skipped" and "already recorded" in again["reason"]
    entries = daily_entries(live_conn)
    assert len(entries) == 1 and entries[0]["entry_type"] in ("research_brief", "no_candidate")


def test_late_run_is_labeled_late(live_conn, tmp_path):
    r = job(live_conn, ny(2026, 9, 24, 9, 15), tmp_path)
    assert r["action"] == "done" and r["late"] is True
    assert daily_entries(live_conn)[0]["title"].startswith("LATE")


@pytest.mark.parametrize("utc_hour,expected", [(8, "too early"), (9, "done")])
def test_window_follows_new_york_time_after_dst_ends(live_conn, tmp_path, utc_hour, expected):
    # 2026-11-02 is the Monday after clocks fall back: New York = UTC-5.
    r = job(live_conn, datetime(2026, 11, 2, utc_hour, 35, tzinfo=timezone.utc), tmp_path)
    assert (r["action"] == "done") if expected == "done" else (expected in r["reason"])


def test_same_utc_time_is_inside_window_in_summer(live_conn, tmp_path):
    # 08:35 UTC = 04:35 New York in summer (UTC-4), inside the window.
    assert job(live_conn, datetime(2026, 7, 7, 8, 35, tzinfo=timezone.utc), tmp_path)["action"] == "done"


def test_missed_days_are_recorded_not_backfilled(live_conn, tmp_path):
    job(live_conn, ny(2026, 9, 21, 4, 40), tmp_path)          # Monday
    job(live_conn, ny(2026, 9, 24, 4, 40), tmp_path)          # Thursday (Tue, Wed missed)
    missed = [e["title"] for e in journal.list_entries(live_conn) if (e["dedupe_key"] or "").startswith("missed-")]
    assert missed == ["Missed run: no research for 2026-09-22", "Missed run: no research for 2026-09-23"]
    assert [e["dedupe_key"] for e in daily_entries(live_conn)] == ["daily-2026-09-21", "daily-2026-09-24"]


class Down:
    def __call__(self, *a, **k):
        return TransportResponse(503, "down")


def test_failures_retry_then_publish_no_candidate(live_conn, tmp_path):
    r1 = job(live_conn, ny(2026, 9, 24, 4, 35), tmp_path, transport=Down())
    r2 = job(live_conn, ny(2026, 9, 24, 5, 5), tmp_path, transport=Down())
    assert r1["action"] == r2["action"] == "retry_later" and daily_entries(live_conn) == []
    r3 = job(live_conn, ny(2026, 9, 24, 5, 35), tmp_path, transport=Down())
    assert r3["action"] == "failed_final"
    e = daily_entries(live_conn)[0]
    assert e["entry_type"] == "no_candidate" and "Research unavailable" in e["payload_json"]


def test_failure_after_deadline_does_not_retry(live_conn, tmp_path):
    assert job(live_conn, ny(2026, 9, 24, 7, 0), tmp_path, transport=Down())["action"] == "failed_final"


def test_lock_blocks_a_second_copy(live_conn, tmp_path):
    now = ny(2026, 9, 24, 4, 35)
    held = Lock(tmp_path / "daily.lock", now)
    assert held.acquire()
    assert "in progress" in job(live_conn, now, tmp_path)["reason"]
    held.release()
    assert job(live_conn, now, tmp_path)["action"] == "done"


def test_stale_lock_from_a_crash_is_cleared(tmp_path):
    now = ny(2026, 9, 24, 4, 35)
    (tmp_path / "daily.lock").write_text(f"123|{(now - timedelta(hours=5)).isoformat()}")
    assert Lock(tmp_path / "daily.lock", now).acquire()


def test_published_brief_links_packet_and_eligibility(live_conn, tmp_path):
    import json
    job(live_conn, ny(2026, 9, 24, 4, 35), tmp_path)
    p = json.loads(daily_entries(live_conn)[0]["payload_json"])
    assert p["trading_date"] == "2026-09-24" and p["packet_sha256"] and p["eligibility"]
    assert p["validation"]["status"] == "passed"


def test_task_xml_is_well_formed(tmp_path):
    import xml.dom.minidom
    from omkaka.schedule import task_xml
    doc = xml.dom.minidom.parseString(task_xml(tmp_path).replace('encoding="UTF-16"', 'encoding="UTF-8"'))
    assert doc.getElementsByTagName("StartWhenAvailable")[0].firstChild.data == "true"
    assert doc.getElementsByTagName("WakeToRun")[0].firstChild.data == "false"
    assert doc.getElementsByTagName("Interval")[0].firstChild.data == "PT30M"

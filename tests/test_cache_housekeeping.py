"""The download cache is disposable and must not grow forever."""
import itertools
from datetime import datetime, timedelta, timezone

from omkaka import db, journal
from omkaka.config import load_settings
from omkaka.maintenance import compact_database, compact_if_worthwhile, doctor, unused_bytes
from omkaka.pipeline import build_clients, run_screen
from omkaka.sources.fixtures import FixtureTransport
from omkaka.sources.http import HttpClient, TransportResponse, cache_size, prune_cache
from omkaka.timeutil import NEW_YORK

T = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)


def fill_cache(conn, n, ttl_hours, start=T, size=10_000):
    body = '{"x": "' + "a" * size + '"}'
    for i in range(n):
        http = HttpClient(conn, load_settings("live"), transport=lambda *a, **k: TransportResponse(200, body),
                          sleep=lambda s: None, now=lambda: start)
        http.get("sec", f"https://example.com/{ttl_hours}/{i}", ttl_hours=ttl_hours)


def test_prune_removes_only_expired_rows(live_conn):
    fill_cache(live_conn, 3, ttl_hours=1)
    fill_cache(live_conn, 2, ttl_hours=48)
    freed = prune_cache(live_conn, T + timedelta(hours=2))
    assert freed["rows"] == 3 and freed["bytes"] > 30_000
    assert cache_size(live_conn)["rows"] == 2


def test_research_history_is_never_touched(live_conn):
    journal.append_entry(live_conn, "note", "keep", "me", now=T)
    fill_cache(live_conn, 2, ttl_hours=1)
    prune_cache(live_conn, T + timedelta(days=5))
    assert journal.list_entries(live_conn)[0]["title"] == "keep" and journal.verify_chain(live_conn)[0]


def test_each_screen_prunes_expired_cache(live_conn):
    fill_cache(live_conn, 4, ttl_hours=1, start=T - timedelta(days=3))  # left over from an old run
    tick = itertools.count()
    clients = build_clients(live_conn, load_settings("live"), transport=FixtureTransport(T.astimezone(NEW_YORK).date()),
                            sleep=lambda s: None, now=lambda: T + timedelta(seconds=next(tick)), demo_credentials=True)
    summary = run_screen(live_conn, load_settings("live"), clients, log=lambda m: None)
    assert summary["cache_pruned"]["rows"] == 4
    assert live_conn.execute("SELECT COUNT(*) FROM http_cache WHERE expires_at <= ?",
                             ("2026-09-24T09:00:00.000000+00:00",)).fetchone()[0] == 0


def test_compact_shrinks_the_file_and_keeps_history():
    s = load_settings("live")
    db.init_db(s.db_path, "live")
    conn = db.connect(s.db_path, "live")
    journal.append_entry(conn, "note", "keep", "me", now=T)
    fill_cache(conn, 40, ttl_hours=1, size=100_000)  # ~4 MB of cache
    conn.close()
    r = compact_database(s, now=T + timedelta(hours=2))
    assert r["cache_rows_deleted"] == 40 and r["vacuumed"]
    assert r["size_mb_after"] < r["size_mb_before"] - 3
    conn = db.connect(s.db_path, "live")
    assert journal.verify_chain(conn)[0] and len(journal.list_entries(conn)) == 1
    conn.close()


def test_automatic_compaction_only_when_worthwhile(monkeypatch):
    import omkaka.maintenance as m
    s = load_settings("live")
    db.init_db(s.db_path, "live")
    conn = db.connect(s.db_path, "live")
    fill_cache(conn, 5, ttl_hours=1, size=100_000)
    assert compact_if_worthwhile(s, conn, T + timedelta(hours=2)) is None  # ~0.5 MB unused: not worth it
    assert unused_bytes(conn) > 0
    monkeypatch.setattr(m, "AUTO_COMPACT_BYTES", 1)
    assert compact_if_worthwhile(s, conn, T + timedelta(hours=2))["vacuumed"]
    assert unused_bytes(conn) == 0
    conn.close()


def test_doctor_reports_database_size():
    s = load_settings("live")
    db.init_db(s.db_path, "live")
    rows = {name: (status, detail) for status, name, detail in doctor(s)}
    assert rows["Database size"][0] == "OK" and "download cache" in rows["Database size"][1]

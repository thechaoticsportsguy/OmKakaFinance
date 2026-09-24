from datetime import datetime, timedelta, timezone

import pytest

from omkaka import db, store

T0 = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)  # fixed "now" for predictable tests


@pytest.fixture
def live_conn(tmp_path):
    path = tmp_path / "live.db"
    db.init_db(path, "live")
    conn = db.connect(path, "live")
    yield conn
    conn.close()


@pytest.fixture
def run_id(live_conn):
    return store.start_run(live_conn, "manual", decision_cutoff=T0, now=T0 - timedelta(hours=3))


@pytest.fixture
def demo_settings(tmp_path, monkeypatch):
    from omkaka.config import load_settings
    monkeypatch.setenv("OMKAKA_DB_PATH", str(tmp_path / "demo" / "demo.db"))
    return load_settings("demo")

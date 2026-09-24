"""Demo fixtures must never mix with live research."""
import pytest

from omkaka import db, demo, journal, store
from omkaka.demo import NotADemoDatabase
from omkaka.display import describe_metric

from .conftest import T0


def test_demo_db_builds_and_is_fully_labelled(demo_settings):
    path = demo.reset_demo_db(demo_settings, now=T0)
    conn = db.connect(path, "demo")
    assert db.db_mode(conn) == "demo"
    assert all(r["is_demo"] == 1 for r in conn.execute("SELECT is_demo FROM runs"))
    providers = {r[0] for r in conn.execute("SELECT DISTINCT provider FROM evidence")}
    assert providers == {"DEMO FIXTURE"}
    assert all("FICTIONAL" in r[0] for r in conn.execute("SELECT title FROM evidence"))
    assert all(r[0].startswith("https://example.com/") for r in conn.execute("SELECT url FROM evidence"))
    assert journal.verify_chain(conn)[0]
    conn.close()


def test_demo_shows_unavailable_stale_and_after_cutoff_examples(demo_settings):
    conn = db.connect(demo.reset_demo_db(demo_settings, now=T0), "demo")
    rev_y = conn.execute("SELECT * FROM metric_values WHERE metric_id='demo-m-rev-y'").fetchone()
    assert rev_y["value"] is None
    assert describe_metric(rev_y, 96, now=T0)["value"].startswith("Data unavailable")
    price_y = conn.execute("SELECT * FROM metric_values WHERE metric_id='demo-m-price-y'").fetchone()
    assert describe_metric(price_y, 96, now=T0)["freshness"] == "STALE"
    usable = {e["evidence_id"] for e in store.evidence_for_run(conn, "demo-run-001")}
    assert "demo-ev-late" not in usable and "demo-ev-10q" in usable
    mcap = conn.execute("SELECT * FROM metric_values WHERE metric_id='demo-m-mcap-x'").fetchone()
    assert mcap["value"] == pytest.approx(12.34 * 33_400_000) and mcap["calculation_json"]
    conn.close()


def test_demo_fixtures_refuse_live_database(live_conn):
    with pytest.raises(NotADemoDatabase):
        demo.seed_demo(live_conn)


def test_live_db_cannot_be_opened_as_demo_or_reset(tmp_path, monkeypatch):
    from omkaka.config import load_settings
    live = tmp_path / "live.db"
    db.init_db(live, "live")
    with pytest.raises(db.WrongDatabaseMode):
        db.connect(live, "demo")
    monkeypatch.setenv("OMKAKA_DB_PATH", str(live))
    with pytest.raises(db.WrongDatabaseMode):
        demo.reset_demo_db(load_settings("demo"))
    assert live.exists()  # the live file was not deleted


def test_demo_runs_rejected_in_live_db(live_conn):
    with pytest.raises(ValueError):
        store.start_run(live_conn, "demo", decision_cutoff=T0)


def test_reset_requires_demo_settings(tmp_path, monkeypatch):
    from omkaka.config import load_settings
    monkeypatch.setenv("OMKAKA_DB_PATH", str(tmp_path / "x.db"))
    with pytest.raises(NotADemoDatabase):
        demo.reset_demo_db(load_settings("live"))

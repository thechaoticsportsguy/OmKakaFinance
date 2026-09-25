"""Loads every dashboard page headlessly (no browser) and checks for errors."""
import pytest
from streamlit.testing.v1 import AppTest

from omkaka import db, demo
from omkaka.config import PROJECT_ROOT
from omkaka.ui.pages import PAGES

APP = str(PROJECT_ROOT / "app.py")


@pytest.mark.parametrize("page", list(PAGES))
def test_demo_pages_render_with_banner(demo_settings, monkeypatch, page):
    demo.reset_demo_db(demo_settings)
    monkeypatch.setenv("OMKAKA_MODE", "demo")
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, at.exception
    assert any("DEMO MODE" in e.value for e in at.error)


def test_live_today_page_empty_state(tmp_path, monkeypatch):
    monkeypatch.setenv("OMKAKA_MODE", "live")
    monkeypatch.setenv("OMKAKA_DB_PATH", str(tmp_path / "live.db"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["view"] = "Today"
    at.run()
    assert not at.exception
    assert not any("DEMO MODE" in e.value for e in at.error)
    assert any("No research result yet" in w.value for w in at.warning)


def test_demo_today_shows_checked_daily_result(demo_settings, monkeypatch):
    demo.reset_demo_db(demo_settings)
    monkeypatch.setenv("OMKAKA_MODE", "demo")
    at = AppTest.from_file(APP, default_timeout=30).run()
    text = " ".join(str(m.value) for m in at.markdown)
    assert "Research candidate: DEMOA" in text and "PASSED" in text
    assert "not neutral" in text  # Reddit shown as unknown, never neutral
    assert "[CONFIRMED]" in text and "RISK:" in text


def test_demo_screening_page_builds_packet(demo_settings, monkeypatch):
    demo.reset_demo_db(demo_settings)
    monkeypatch.setenv("OMKAKA_MODE", "demo")
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.sidebar.radio[0].set_value("Watchlist & screening").run()
    assert not at.exception
    next(b for b in at.button if b.label == "Build research packet").click().run()
    assert not at.exception
    assert any("recorded it in the journal" in s.value for s in at.success)
    assert (demo_settings.db_path.parent / "packets" / "packet-demo-screen-001.md").exists()

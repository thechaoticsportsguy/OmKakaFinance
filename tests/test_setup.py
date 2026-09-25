"""Regression checks for first-run setup, private credentials and real readiness."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values

from omkaka import config, db
from omkaka.models import Status
from omkaka.setup import check_connections, readiness, save_credentials
from omkaka.sources.http import FetchResult


def test_saving_preserves_other_settings_and_blank_inputs(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# private settings\nNEWS_API_KEY='old-news'\nOTHER=keep\n", encoding="utf-8")
    save_credentials({"NEWS_API_KEY": "", "MARKET_DATA_API_KEY": "key-with-$-and-'quote"}, path)
    values = dotenv_values(path, interpolate=False)
    assert values["NEWS_API_KEY"] == "old-news" and values["OTHER"] == "keep"
    assert values["MARKET_DATA_API_KEY"] == "key-with-$-and-'quote"
    assert "# private settings" in path.read_text()


def test_invalid_contact_or_newline_cannot_modify_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("NEWS_API_KEY=keep\n")
    for values in ({"SEC_USER_AGENT": "not-an-email"}, {"NEWS_API_KEY": "abc\nOTHER=oops"}):
        with pytest.raises(ValueError):
            save_credentials(values, path)
        assert path.read_text() == "NEWS_API_KEY=keep\n"


def test_external_env_edits_reload_without_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("NEWS_API_KEY=first\n")
    assert config.get_secret("NEWS_API_KEY").reveal() == "first"
    path.write_text("NEWS_API_KEY=second\n")
    assert config.get_secret("NEWS_API_KEY").reveal() == "second"
    path.write_text("NEWS_API_KEY=\n")
    assert config.get_secret("NEWS_API_KEY") is None


def clients_at(now, market_data=None):
    requested = []
    def result(provider, data):
        return FetchResult(provider, "https://example.com/test", Status.OK, None, data, now)
    def market(day):
        requested.append(day)
        return result("massive", market_data if market_data is not None else {"results": [{"T": "SPY", "c": 100, "v": 10}]})
    clients = SimpleNamespace(
        http=SimpleNamespace(now=lambda: now, bypass_cache=False),
        sec=SimpleNamespace(company_tickers=lambda: result("sec", {"fields": ["cik", "name", "ticker", "exchange"], "data": [[1,"Test","TEST","NYSE"]]})),
        massive=SimpleNamespace(grouped_daily=market),
        finnhub=SimpleNamespace(company_news=lambda *args: result("finnhub", [])),
        reddit=SimpleNamespace(search=lambda *args: FetchResult("reddit", "https://example.com", Status.NOT_CONFIGURED, "Optional", None, now)))
    return clients, requested


def test_check_uses_trading_calendar_and_optional_reddit_does_not_fail(live_conn):
    # Tuesday after Labor Day: Friday's close is the most recent trading day.
    now = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
    clients, requested = clients_at(now)
    rows = check_connections(live_conn, config.load_settings("live"), clients)
    assert requested[0].isoformat() == "2026-09-04"
    assert all(r["passed"] for r in rows if r["required"])
    assert clients.http.bypass_cache
    assert live_conn.execute("SELECT status FROM runs").fetchone()[0] == "completed"


@pytest.mark.parametrize("payload", [{"error": "not entitled"}, ["wrong shape"]])
def test_http_200_without_market_data_is_not_success(live_conn, payload):
    clients, _ = clients_at(datetime(2026, 9, 24, 10, tzinfo=timezone.utc), payload)
    rows = check_connections(live_conn, config.load_settings("live"), clients)
    assert not next(r for r in rows if r["source"] == "Market data (end of day)")["passed"]
    assert live_conn.execute("SELECT status FROM runs").fetchone()[0] == "failed"


def test_schedule_cannot_install_without_verified_sources(live_conn, monkeypatch, tmp_path):
    from omkaka import schedule
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    for name in config.SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(schedule.subprocess, "run", lambda *a, **k: pytest.fail("Must not modify Windows scheduler"))
    assert not readiness(live_conn, config.load_settings("live"))["ready"]
    with pytest.raises(ValueError, match="Schedule is not enabled"):
        schedule.install(tmp_path)


def test_live_setup_form_does_not_display_saved_values(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    app = str(config.PROJECT_ROOT / "app.py")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("OMKAKA_MODE", "live")
    for name in config.SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text("NEWS_API_KEY=private-test-value\n")
    at = AppTest.from_file(app, default_timeout=30)
    at.session_state["view"] = "Setup & connections"
    at.run()
    assert not at.exception
    assert all(t.value == "" for t in at.text_input)
    assert not any("private-test-value" in str(m.value) for m in at.markdown)
    button = next(b for b in at.button if b.label == "Enable 6 a.m. schedule")
    assert button.disabled


def test_changed_brief_requires_a_new_check(demo_settings, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from omkaka import demo
    demo.reset_demo_db(demo_settings)
    monkeypatch.setenv("OMKAKA_MODE", "demo")
    at = AppTest.from_file(str(config.PROJECT_ROOT / "app.py"), default_timeout=30).run()
    at.sidebar.radio[0].set_value("Review a brief").run()
    next(b for b in at.button if b.label == "Start from the automatic data-only brief").click().run()
    next(b for b in at.button if b.label == "Check brief").click().run()
    assert any(b.label == "Save to journal" for b in at.button)
    at.text_area[0].set_value(at.text_area[0].value + "\nPrice target $999999.").run()
    assert not any(b.label.startswith("Save to journal") for b in at.button)
    assert any("has changed" in i.value for i in at.info)


def test_setup_form_saves_privately_and_clears_fields(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    app = str(config.PROJECT_ROOT / "app.py")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("OMKAKA_MODE", "live")
    for name in config.SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
    at = AppTest.from_file(app, default_timeout=30)
    at.session_state["view"] = "Setup & connections"
    at.run()
    at.text_input[0].set_value("Demo Person demo@example.com")
    at.text_input[1].set_value("test-market-private")
    at.text_input[2].set_value("test-news-private")
    next(b for b in at.button if b.label == "Save connections").click().run()
    assert not at.exception
    assert dotenv_values(tmp_path / ".env")["MARKET_DATA_API_KEY"] == "test-market-private"
    assert all(t.value == "" for t in at.text_input)
    assert at.metric[0].value == "3 / 3"
    assert not next(b for b in at.button if b.label == "Check live connections").disabled

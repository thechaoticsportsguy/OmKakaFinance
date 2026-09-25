from datetime import datetime, timezone

import pytest

from omkaka.config import PROJECT_ROOT, load_settings
from omkaka.models import Status
from omkaka.sources.http import FetchResult, HttpClient, TransportResponse
from omkaka.sources.yahoo import YahooClient, parse_chart, parse_news, symbol


def chart_payload():
    return {"chart": {"result": [{"meta": {
        "symbol": "AAPL", "shortName": "Apple Inc.", "currency": "USD", "exchangeTimezoneName": "America/New_York",
        "regularMarketPrice": 110, "regularMarketTime": 1790280000, "chartPreviousClose": 80,
    }, "timestamp": [1790170200, 1790256600], "indicators": {"quote": [{"close": [100, 110], "volume": [10, None]}]}}]}}


def test_daily_change_uses_prior_session_not_start_of_month():
    q = parse_chart(chart_payload())
    assert q["change_pct"] == pytest.approx(10)
    assert q["bars"][-1]["volume"] is None
    assert q["day_high"] is None


def test_missing_quote_remains_missing():
    data = chart_payload()
    data["chart"]["result"][0]["meta"].pop("regularMarketPrice")
    q = parse_chart(data)
    assert q["price"] is None and q["change_pct"] is None


@pytest.mark.parametrize("bad", ["../.env", "AAPL?key=x", "", "AAPL/MSFT"])
def test_invalid_symbols_are_rejected(bad):
    with pytest.raises(ValueError):
        symbol(bad)


def test_news_links_are_deduplicated_and_unsafe_links_are_skipped():
    news = parse_news('''<rss><channel>
    <item><title>One</title><link>https://example.com/article</link><pubDate>Thu, 24 Sep 2026 12:00:00 GMT</pubDate></item>
    <item><title>Repeat</title><link>https://example.com/article</link></item>
    <item><title>Bad</title><link>javascript:alert(1)</link></item>
    </channel></rss>''')
    assert len(news) == 1 and news[0]["published_at"].endswith("+00:00")


def test_bad_source_response_does_not_become_a_quote(live_conn):
    http = HttpClient(live_conn, load_settings("live"),
                      transport=lambda *a, **kw: TransportResponse(200, '{"chart":{"result":null}}'), sleep=lambda _: None)
    r = YahooClient(http).chart("AAPL")
    assert r.status is Status.FAILED and r.data is None


def test_live_market_page_works_without_api_credentials(monkeypatch, live_conn):
    from streamlit.testing.v1 import AppTest
    from omkaka.ui import markets
    class FakeYahoo:
        def __init__(self, http):
            pass
        def chart(self, ticker, period="1mo"):
            q = parse_chart(chart_payload())
            q["symbol"] = ticker
            return FetchResult("yahoo", "https://example.com", Status.OK, None, q, datetime.now(timezone.utc))
        def news(self, ticker):
            return FetchResult("yahoo", "https://example.com", Status.NO_RESULTS, "No news", [], datetime.now(timezone.utc))
    monkeypatch.setattr(markets, "YahooClient", FakeYahoo)
    monkeypatch.setenv("OMKAKA_MODE", "live")
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception and not at.error
    assert at.sidebar.radio[0].value == "Markets"
    assert at.text_input[0].value == "AAPL"
    assert at.metric[3].value == "110.00 USD"


class _WatchYahoo:
    """Fake Yahoo: AAPL has a quote, MSFT has chart history but no quote, BAD fails."""

    def __init__(self, http=None):
        pass

    def chart(self, ticker, period="1mo"):
        now = datetime.now(timezone.utc)
        if ticker == "BAD":
            return FetchResult("yahoo", "https://example.com", Status.FAILED, "HTTP 500 after 3 tries", None, now)
        q = parse_chart(chart_payload())
        q["symbol"] = ticker
        if ticker == "MSFT":
            q["price"], q["change_pct"] = None, None
            return FetchResult("yahoo", "https://example.com", Status.PARTIAL, "The latest quote is unavailable.", q, now)
        return FetchResult("yahoo", "https://example.com", Status.OK, None, q, now)

    def news(self, ticker):
        return FetchResult("yahoo", "https://example.com", Status.NO_RESULTS, "No news", [], datetime.now(timezone.utc))


def test_watchlist_rows_never_turn_missing_quotes_into_numbers():
    from omkaka.ui.markets import watchlist_rows
    rows = {r["Symbol"]: r for r in watchlist_rows(_WatchYahoo(), ["AAPL", "MSFT", "BAD"])}
    assert rows["AAPL"]["Price"] == "110.00 USD" and rows["AAPL"]["Day change"] == "+10.00%"
    assert rows["MSFT"]["Price"] == "Unavailable" and "unavailable" in rows["MSFT"]["Status"]
    assert rows["BAD"]["Price"] == "Unavailable" and rows["BAD"]["Quote time"] == "—"


def test_watchlist_rows_respect_the_limit():
    from omkaka.ui.markets import watchlist_rows
    assert len(watchlist_rows(_WatchYahoo(), [f"T{i}" for i in range(20)], limit=5)) == 5


def test_market_page_watchlist_open_and_remove(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from omkaka import db, store
    from omkaka.ui import markets
    s = load_settings("live")
    db.init_db(s.db_path, "live")
    conn = db.connect(s.db_path, "live")
    for t in ("MSFT", "BAD"):
        store.watchlist_change(conn, t, "add")
    conn.close()
    monkeypatch.setattr(markets, "YahooClient", _WatchYahoo)
    monkeypatch.setenv("OMKAKA_MODE", "live")
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    at.toggle(key="watch_prices").set_value(True).run()
    table = at.dataframe[-1].value
    assert list(table["Symbol"]) == ["BAD", "MSFT"] and set(table["Price"]) == {"Unavailable"}
    at.selectbox(key="watch_pick").set_value("MSFT").run()
    at.button(key="watch_open").click().run()
    assert not at.exception and at.text_input[0].value == "MSFT"
    at.selectbox(key="watch_pick").set_value("BAD").run()
    at.button(key="watch_remove").click().run()
    conn = db.connect(s.db_path, "live")
    assert store.current_watchlist(conn) == ["MSFT"]
    conn.close()

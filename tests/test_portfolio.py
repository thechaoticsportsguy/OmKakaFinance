"""Phase 5: paper portfolio fills, limits, valuation, and SPY comparison."""
from datetime import date, datetime, timedelta, timezone

import pytest

from omkaka import db, journal, portfolio
from omkaka.config import load_settings
from omkaka.market_calendar import trading_days_before
from omkaka.sources.massive import market_close
from omkaka.timeutil import NEW_YORK, to_utc_iso

S = load_settings("live")


def ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NEW_YORK).astimezone(timezone.utc)


def add_bar(conn, ticker, day: str, close: float, fetched_delay_h=4):
    eff = market_close(date.fromisoformat(day))
    conn.execute("""INSERT INTO market_bars(ticker, trade_date, close, volume, provider, source_url, effective_at,
                    fetched_at) VALUES (?, ?, ?, 1000, 'massive', 'test', ?, ?)""",
                 (ticker, day, close, to_utc_iso(eff), to_utc_iso(eff + timedelta(hours=fetched_delay_h))))


@pytest.fixture
def conn(live_conn):
    # Prices for Mon 2026-09-21 .. Fri 2026-09-25.
    for i, d in enumerate(["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]):
        add_bar(live_conn, "ABC", d, 10.0 + i)
        add_bar(live_conn, "SPY", d, 600.0 + i * 6)
    return live_conn


def test_fill_uses_first_close_after_decision_never_earlier(conn):
    portfolio.cash_movement(conn, "deposit", 10_000, now=ny(2026, 9, 22, 9, 0))
    oid = portfolio.place_order(conn, S, "buy", "ABC", 100, now=ny(2026, 9, 22, 17, 0))  # after Tue close
    # Wednesday morning: Wednesday's close does not exist yet -> still pending.
    assert portfolio.fill_pending(conn, S, now=ny(2026, 9, 23, 9, 0)) == []
    fills = portfolio.fill_pending(conn, S, now=ny(2026, 9, 24, 9, 0))
    assert fills[0]["price_date"] == "2026-09-23"                       # Wednesday close, not Tuesday's
    assert fills[0]["price"] == pytest.approx(12.0 * 1.001)             # 10 bps slippage on a buy
    assert portfolio.state(conn, now=ny(2026, 9, 24, 9, 0)).holdings == {"ABC": 100}


def test_close_not_yet_fetched_is_not_used(conn):
    portfolio.cash_movement(conn, "deposit", 10_000, now=ny(2026, 9, 24, 9, 0))
    portfolio.place_order(conn, S, "buy", "ABC", 10, now=ny(2026, 9, 24, 10, 0))
    # 17:00 New York: the 16:00 close happened, but our copy is fetched at 20:00 -> not available yet.
    assert portfolio.fill_pending(conn, S, now=ny(2026, 9, 24, 17, 0)) == []
    assert portfolio.fill_pending(conn, S, now=ny(2026, 9, 24, 21, 0))[0]["price_date"] == "2026-09-24"


def test_insufficient_cash_and_no_short_selling(conn):
    portfolio.cash_movement(conn, "deposit", 100, now=ny(2026, 9, 21, 9, 0))
    portfolio.place_order(conn, S, "buy", "ABC", 100, now=ny(2026, 9, 21, 10, 0))
    r = portfolio.fill_pending(conn, S, now=ny(2026, 9, 25, 20, 0))
    assert r[0]["status"] == "rejected" and "Not enough paper cash" in r[0]["reason"]
    with pytest.raises(ValueError, match="short"):
        portfolio.place_order(conn, S, "sell", "ABC", 1, now=ny(2026, 9, 22, 10, 0))
    with pytest.raises(ValueError, match="more paper cash"):
        portfolio.cash_movement(conn, "withdrawal", 1_000, now=ny(2026, 9, 22, 10, 0))


def test_max_15_holdings(conn):
    portfolio.cash_movement(conn, "deposit", 1_000_000, now=ny(2026, 9, 21, 9, 0))
    for i in range(15):
        portfolio.place_order(conn, S, "buy", f"T{i:02d}", 1, now=ny(2026, 9, 21, 10, 0))
    with pytest.raises(ValueError, match="15 holdings"):
        portfolio.place_order(conn, S, "buy", "ONEMORE", 1, now=ny(2026, 9, 21, 10, 0))


def test_order_without_prices_is_rejected_after_waiting(conn):
    portfolio.cash_movement(conn, "deposit", 1_000, now=ny(2026, 9, 21, 9, 0))
    portfolio.place_order(conn, S, "buy", "NOPRICE", 1, now=ny(2026, 9, 21, 10, 0))
    assert portfolio.fill_pending(conn, S, now=ny(2026, 9, 23, 10, 0)) == []           # still waiting
    r = portfolio.fill_pending(conn, S, now=ny(2026, 9, 29, 10, 0))
    assert r[0]["status"] == "rejected" and "No price" in r[0]["reason"]


def test_missing_price_makes_value_unavailable_not_zero(conn):
    portfolio.cash_movement(conn, "deposit", 1_000, now=ny(2026, 9, 21, 9, 0))
    portfolio.place_order(conn, S, "buy", "ABC", 10, now=ny(2026, 9, 21, 10, 0))
    portfolio.fill_pending(conn, S, now=ny(2026, 9, 22, 9, 0))
    v = portfolio.valuation(conn, S, as_of_date="2026-09-20", now=ny(2026, 9, 25, 20, 0))
    assert v["holdings"] == []  # before the fill: nothing held yet
    v = portfolio.valuation(conn, S, now=ny(2026, 9, 25, 21, 0))
    assert v["total"] == pytest.approx(v["cash"] + 10 * 14.0)
    conn.execute("DROP TRIGGER IF EXISTS market_bars_no_delete")
    conn.execute("DELETE FROM market_bars WHERE ticker='ABC'")
    v = portfolio.valuation(conn, S, now=ny(2026, 9, 25, 21, 0))
    assert v["total"] is None and v["missing_prices"] == ["ABC"]


def test_split_and_dividend_entries_and_split_warning(conn):
    portfolio.cash_movement(conn, "deposit", 1_000, now=ny(2026, 9, 21, 9, 0))
    portfolio.place_order(conn, S, "buy", "ABC", 10, now=ny(2026, 9, 21, 10, 0))
    portfolio.fill_pending(conn, S, now=ny(2026, 9, 22, 9, 0))
    add_bar(conn, "ABC", "2026-09-28", 7.0)  # halves overnight: looks like a 2-for-1 split
    v = portfolio.valuation(conn, S, now=ny(2026, 9, 28, 21, 0))
    assert "possible split" in v["holdings"][0]["flags"][0]
    portfolio.record_corporate_action(conn, "split", "ABC", "2026-09-28", 2.0, "company 8-K", now=ny(2026, 9, 28, 22, 0))
    portfolio.record_corporate_action(conn, "dividend", "ABC", "2026-09-28", 0.10, "company release",
                                      now=ny(2026, 9, 28, 22, 0))
    st = portfolio.state(conn, now=ny(2026, 9, 28, 23, 0))
    assert st.holdings["ABC"] == 20 and st.dividends == pytest.approx(2.0)
    assert portfolio.valuation(conn, S, now=ny(2026, 9, 28, 23, 0))["holdings"][0]["flags"] == []


def test_spy_mirror_gets_identical_money_at_identical_times(conn):
    portfolio.cash_movement(conn, "deposit", 6_000, now=ny(2026, 9, 21, 9, 0))
    portfolio.place_order(conn, S, "buy", "ABC", 100, now=ny(2026, 9, 21, 10, 0))
    portfolio.fill_pending(conn, S, now=ny(2026, 9, 22, 9, 0))
    h = portfolio.history(conn, S, now=ny(2026, 9, 25, 21, 0))
    assert [r["date"] for r in h] == ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
    assert all(r["contributions"] == 6_000 for r in h)
    first, last = h[0], h[-1]
    units = 6_000 / (600.0 * 1.001)
    assert first["spy_mirror"] == pytest.approx(units * 600.0)
    assert last["spy_mirror"] == pytest.approx(units * 624.0)
    assert last["portfolio"] == pytest.approx(6_000 - 100 * 10.0 * 1.001 + 100 * 14.0)


def test_everything_is_append_only_and_journaled(conn):
    portfolio.cash_movement(conn, "deposit", 1_000, now=ny(2026, 9, 21, 9, 0))
    oid = portfolio.place_order(conn, S, "buy", "ABC", 1, now=ny(2026, 9, 21, 10, 0))
    portfolio.fill_pending(conn, S, now=ny(2026, 9, 22, 9, 0))
    import sqlite3
    for sql in ("UPDATE paper_fills SET price = 1", "DELETE FROM paper_orders", "UPDATE paper_cash SET amount = 5"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)
    titles = [e["title"] for e in journal.list_entries(conn) if e["entry_type"] == "paper_trade"]
    assert titles[0].startswith("Paper deposit") and any("filled" in t for t in titles)


def test_candidate_outcomes_vs_spy(conn):
    journal.append_entry(conn, "research_brief", "Research candidate: ABC", "x",
                         payload={"candidate": "ABC", "trading_date": "2026-09-22", "validation": {"status": "passed"}})
    out = portfolio.candidate_outcomes(conn, S, now=ny(2026, 9, 25, 21, 0))
    assert out[0]["from"] == "2026-09-22" and out[0]["to"] == "2026-09-25"
    assert out[0]["return"] == pytest.approx(14 / 11 - 1) and out[0]["spy_return"] == pytest.approx(624 / 606 - 1)


def test_orders_are_never_automatic(conn):
    journal.append_entry(conn, "research_brief", "Research candidate: ABC", "x",
                         payload={"candidate": "ABC", "trading_date": "2026-09-22"})
    portfolio.fill_pending(conn, S, now=ny(2026, 9, 25, 21, 0))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0

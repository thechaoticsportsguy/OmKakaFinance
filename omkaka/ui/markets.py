"""Real public market data for immediate manual research, separate from daily picks."""
from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from .. import store
from ..display import md_escape
from ..models import Status
from ..sources.http import HttpClient
from ..sources.yahoo import YahooClient, symbol
from ..timeutil import format_new_york
from .style import header


def page_markets(conn, settings):
    header("Market desk", "Real stocks. A clearer view.", "Explore prices, charts, and company headlines without setting up API keys.")
    if settings.is_demo:
        st.info("The demo stays offline. Open the real market workspace for current source data.")
        st.link_button("Open real stocks →", "http://localhost:8501/?view=markets", type="primary")
        return
    http = HttpClient(conn, settings)
    client = YahooClient(http)
    st.caption("Yahoo Finance · Latest available prices, which may be delayed · Personal research")
    with st.form("stock_lookup"):
        left, middle, right = st.columns([2, 1.2, 1])
        query = left.text_input("Stock symbol", value=st.session_state.get("market_symbol", "AAPL"), placeholder="e.g. AAPL")
        period = middle.selectbox("Chart period", ["1mo", "3mo", "6mo", "1y"], format_func=lambda v: {"1mo":"1 month", "3mo":"3 months", "6mo":"6 months", "1y":"1 year"}[v])
        right.write("")
        submitted = right.form_submit_button("Look up stock", type="primary")
    try:
        ticker = symbol(query)
    except ValueError as exc:
        st.error(str(exc))
        return
    st.session_state["market_symbol"] = ticker
    if st.button("Refresh market data", key="refresh_market"):
        # This is a disposable response cache, never evidence or journal history.
        conn.execute("DELETE FROM http_cache WHERE provider='yahoo'")
        conn.commit()
    market_cols = st.columns(3)
    for col, (sym, name) in zip(market_cols, [("SPY", "S&P 500 · SPY"), ("QQQ", "Nasdaq 100 · QQQ"), ("IWM", "Small caps · IWM")]):
        result = client.chart(sym)
        quote = result.data if result.status in (Status.OK, Status.PARTIAL) else None
        price = quote.get("price") if quote else None
        change = quote.get("change_pct") if quote else None
        col.metric(name, "Unavailable" if price is None else f"${price:,.2f}", None if change is None else f"{change:+.2f}% vs prior close")
        col.caption(format_new_york(quote.get("as_of")) if quote else "Source unavailable")
    with st.spinner(f"Loading {ticker} from Yahoo Finance…"):
        result = client.chart(ticker, period)
    if result.status not in (Status.OK, Status.PARTIAL) or not result.data:
        st.error(f"{ticker}: Data unavailable. {result.reason or 'Try another stock symbol.'}")
        return
    q = result.data
    st.write("")
    heading, actions = st.columns([2.3, 1], gap="large")
    with heading:
        st.html(f'<div class="ok-hero"><span class="ok-ticker">{escape(q["symbol"])}</span> '
                '<span class="ok-pill green">REAL MARKET DATA</span>'
                f'<h2>{escape(q["name"])}</h2><p>{escape(q["exchange"])} · {escape(q["currency"])}</p></div>')
    with actions:
        price = q["price"]
        st.metric("Latest regular-session price", "Unavailable" if price is None else f"{price:,.2f} {q['currency']}",
                  None if q["change_pct"] is None else f"{q['change_pct']:+.2f}% vs prior close")
        st.caption("Quote as of " + format_new_york(q["as_of"]))
    st.caption("Retrieved " + format_new_york(result.fetched_at) + (" · Cached for up to 5 minutes" if result.from_cache else " · Freshly retrieved"))
    if result.reason:
        st.warning(result.reason)
    chart_col, context = st.columns([2.2, 1], gap="large")
    with chart_col:
        with st.container(border=True):
            st.markdown("#### Daily price chart")
            if q["bars"]:
                frame = pd.DataFrame(q["bars"])
                frame["time"] = pd.to_datetime(frame["time"])
                chart = alt.Chart(frame).mark_line(color="#7060d8", strokeWidth=2.5).encode(
                    x=alt.X("time:T", title=None, axis=alt.Axis(format="%b %d", grid=False, tickCount=5)),
                    y=alt.Y("close:Q", title=q["currency"], scale=alt.Scale(zero=False), axis=alt.Axis(gridColor="#edf0f6")),
                    tooltip=[alt.Tooltip("time:T", title="Session", format="%b %d, %Y"), alt.Tooltip("close:Q", title="Price", format=",.2f")]
                ).properties(height=260).configure_view(stroke=None)
                st.altair_chart(chart, width="stretch")
                st.caption("Daily source prices. The most recent point may be an unfinished session. Not a total-return chart.")
            else:
                st.info("Chart history is unavailable.")
    with context:
        with st.container(border=True):
            st.markdown("#### Session details")
            for label, value in (("High", q["day_high"]), ("Low", q["day_low"]), ("Volume", q["volume"])):
                st.write(f"**{label}** · " + ("Unavailable" if value is None else f"{value:,.0f}" if label == "Volume" else f"{value:,.2f} {q['currency']}"))
            st.link_button("Yahoo Finance ↗", f"https://finance.yahoo.com/quote/{ticker}/", width="stretch")
            st.link_button("Finviz research ↗", f"https://finviz.com/quote.ashx?t={ticker}", width="stretch")
            if ticker in store.current_watchlist(conn):
                st.caption("✓ On your research watchlist")
            elif st.button("Add to my watchlist", key="add_market_watch", width="stretch"):
                store.watchlist_change(conn, ticker, "add", note="Added from real market overview")
                st.rerun()
        st.caption("This is a manual research view, not a checked daily pick. Automated screening still requires the connections in Setup.")
    st.markdown("#### Latest company headlines")
    news = client.news(ticker)
    if news.ok:
        st.caption("Headline links from Yahoo Finance's feed · Third-party reporting, not verified company disclosures")
        for item in news.data[:10]:
            with st.container(border=True):
                st.caption(format_new_york(item["published_at"]))
                st.markdown("**" + md_escape(item["title"]) + "**")
                st.link_button("Read article ↗", item["url"])
    else:
        st.info(news.reason or "News is unavailable for this symbol.")
    watch = store.current_watchlist(conn)
    if watch:
        st.markdown("#### Your watchlist")
        st.write(" · ".join(watch))
        st.caption("Enter any saved symbol above to open its chart and headlines.")

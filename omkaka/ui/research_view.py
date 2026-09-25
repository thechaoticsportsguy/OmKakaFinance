"""Readable research summary backed by the recorded run, with the full audit trail."""
import json
from html import escape
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from .. import journal, store
from ..display import describe_metric, md_escape, staleness_category
from ..timeutil import format_new_york
from .style import card


def render_research(conn, settings, entry, expected):
    p = json.loads(entry["payload_json"])
    ticker = p.get("candidate")
    validation = p.get("validation", {})
    trading_date = p.get("trading_date")
    if trading_date and trading_date < expected.isoformat():
        st.warning(f"STALE · This brief covers {trading_date}. No result is available yet for {expected.isoformat()}.")
    if p.get("late"):
        st.warning(f"LATE · Available {format_new_york(p.get('available_at'))}, after the 6:00 a.m. target.")
    row = conn.execute("SELECT * FROM screening_results WHERE run_id=? AND stage='shortlist' AND ticker=?",
                       (entry["run_id"], ticker)).fetchone() if ticker else None
    inputs = json.loads(row["inputs_json"]) if row else {}
    flags = json.loads(row["flags_json"]) if row else []
    company = row["company_name"] if row else "No qualifying candidate"
    if settings.is_demo:
        company = company.replace(" (FICTIONAL)", "")
    checked = validation.get("status", "unknown").upper()
    hero, summary = st.columns([2.1, 1], gap="large")
    with hero:
        st.html(f'<div class="ok-hero"><div class="ok-eyebrow">TODAY’S RESEARCH CANDIDATE</div>'
                f'<span class="ok-ticker">{escape(ticker or "—")}</span> '
                f'<span class="ok-pill {"green" if checked == "PASSED" else "amber"}">{escape(checked)} · EVIDENCE CHECK</span>'
                f'<h2>{escape(company)}</h2><p>{escape(row["exchange"] if row else "Selection rules applied")} '
                f'· {"Fictional demo company" if settings.is_demo else "For your independent review"}</p></div>')
        st.caption(f"Recorded {format_new_york(entry['fetched_at'])}" + (f" · Trading date {trading_date}" if trading_date else ""))
    with summary:
        card("Start with the evidence", "Read the catalyst, inspect the numbers, then record your own view.")
        packet = Path(p["packet_path"]) if p.get("packet_path") else None
        if packet and packet.exists():
            st.download_button("Research packet ↓", packet.read_text(encoding="utf-8"),
                               file_name=packet.name, mime="text/markdown", width="stretch")
        if ticker and not settings.is_demo:
            st.link_button("Open company on Finviz ↗", f"https://finviz.com/quote.ashx?t={ticker}", width="stretch")

    def money(v):
        return "Unavailable" if v is None else f"${v:,.2f}"
    metrics = st.columns(4)
    metrics[0].metric("Priority score", f"{row['score']:.0f} / 100" if row and row["score"] is not None else "—")
    metrics[1].metric("Latest close", money(inputs.get("last_close")))
    cap = inputs.get("market_cap")
    cap_text = "Unavailable" if cap is None else f"${cap / 1e9:,.2f}B" if cap >= 1e9 else f"${cap / 1e6:,.1f}M"
    metrics[2].metric("Market cap", cap_text)
    metrics[3].metric("Risk flags", str(len(flags)) if row else "—")
    st.caption(f"Prices through {inputs.get('last_date', 'unavailable')}. Score is not a probability. Market cap is calculated using price and SEC share count from different dates.")

    for correction in journal.corrections_for(conn, entry["entry_id"]):
        st.warning("Correction: " + md_escape(correction["body"]))
    for warning in validation.get("warnings", []):
        st.warning(md_escape(warning))
    overview, evidence_tab, brief_tab = st.tabs(["Research overview", "Numbers & sources", "Full checked brief"])
    evidence = [e for e in store.evidence_for_run(conn, entry["run_id"]) if e["ticker"] == ticker] if entry["run_id"] else []
    with overview:
        left, right = st.columns([1.85, 1], gap="large")
        with left:
            bars = conn.execute("SELECT trade_date, close FROM market_bars WHERE ticker=? AND run_id=? ORDER BY trade_date",
                                (ticker, entry["run_id"])).fetchall() if ticker else []
            # Subsequent runs may reuse earlier bars; never show data fetched after this run's cutoff.
            run = store.run_row(conn, entry["run_id"])
            if not bars and ticker and run:
                bars = conn.execute("SELECT trade_date, close FROM market_bars WHERE ticker=? AND fetched_at<=? "
                                    "AND trade_date<=? ORDER BY trade_date DESC LIMIT 22",
                                    (ticker, run["decision_cutoff_at"], inputs.get("last_date", ""))).fetchall()
            if bars:
                with st.container(border=True):
                    st.markdown("#### Price context")
                    st.caption("Recorded daily closes · USD per share")
                    frame = pd.DataFrame([{"Date": b["trade_date"], "Close": b["close"]} for b in bars])
                    frame["Date"] = pd.to_datetime(frame["Date"])
                    chart = alt.Chart(frame).mark_line(color="#7060d8", strokeWidth=2.5, point=False).encode(
                        x=alt.X("Date:T", axis=alt.Axis(title=None, format="%b %d", grid=False, tickCount=5)),
                        y=alt.Y("Close:Q", scale=alt.Scale(zero=False), axis=alt.Axis(title=None, format="$,.2f", gridColor="#edf0f6")),
                        tooltip=[alt.Tooltip("Date:T", format="%b %d, %Y"), alt.Tooltip("Close:Q", format="$,.2f")],
                    ).properties(height=210).configure_view(stroke=None)
                    st.altair_chart(chart, width="stretch")
            st.markdown("#### Catalyst & news timeline")
            timeline = sorted([e for e in evidence if e["source_type"] in ("filing", "news")],
                              key=lambda e: e["published_at"] or "", reverse=True)
            if not timeline:
                st.info("No source-backed catalyst is available for this result.")
            for e in timeline[:8]:
                with st.container(border=True):
                    st.caption(("SEC FILING" if e["source_type"] == "filing" else "THIRD-PARTY NEWS") +
                               " · " + format_new_york(e["published_at"]))
                    st.markdown(f"**{md_escape(e['title'] or 'Source document')}**")
                    if e["excerpt"]:
                        st.write(md_escape(e["excerpt"][:300]))
                    if e["url"]:
                        st.link_button("Read source ↗", e["url"])
        with right:
            with st.container(border=True):
                st.markdown("#### Why it surfaced")
                st.caption("Transparent screening factors")
                components = json.loads(row["components_json"]) if row else {}
                for name, c in components.items():
                    label = name.replace("_", " ").title()
                    st.progress(min(1.0, max(0.0, c["points"] / c["max"])) if c["max"] else 0.0,
                                text=f"{label} · {c['points']:g} / {c['max']:g}")
                if not components:
                    st.write("No company passed every selection gate.")
            with st.container(border=True):
                st.markdown("#### Risks to investigate")
                for flag in flags:
                    st.warning(md_escape(flag))
                if not flags:
                    st.caption("No automated flag was raised. This does not establish that a company is low risk.")
            with st.container(border=True):
                st.markdown("#### Your review checklist")
                for i, label in enumerate(("Read the catalyst filing", "Check cash, debt and dilution", "Review the chart and liquidity", "Record your thesis in the journal")):
                    st.checkbox(label, key=f"review-step-{entry['entry_id']}-{i}")
    with evidence_tab:
        st.markdown("#### Every number has a source")
        rows = conn.execute("SELECT * FROM metric_values WHERE run_id=? AND ticker=?", (entry["run_id"], ticker)).fetchall()
        if rows:
            st.dataframe([describe_metric(m, settings.staleness_hours(staleness_category(m))) for m in rows], hide_index=True, width="stretch")
        checks = conn.execute("SELECT * FROM source_checks WHERE run_id=? AND (ticker IS NULL OR ticker=?)",
                              (entry["run_id"], ticker)).fetchall()
        from ..packet import group_checks
        st.markdown("#### Source coverage")
        st.dataframe(group_checks(checks), hide_index=True, width="stretch")
        st.caption("Missing information stays unavailable. It is never treated as neutral or zero.")
    with brief_tab:
        st.caption("The original checked text, including labels and citations. Download the packet for independent review.")
        body = [line for line in p["brief_markdown"].splitlines() if not line.strip().upper().startswith(("CANDIDATE:", "RUN:"))]
        body = [("###" + line[1:]) if line.startswith("# ") else ("####" + line[2:]) if line.startswith("## ") else line for line in body]
        st.markdown(md_escape("\n".join(body)))
        st.markdown(f"Research candidate: {ticker or 'none'} · {checked} · journal entry {entry['entry_id']}")

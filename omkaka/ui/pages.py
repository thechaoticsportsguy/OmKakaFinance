"""Dashboard pages. Kept deliberately plain: tables and clear labels."""
from __future__ import annotations

import json

import streamlit as st

from .. import db, journal, store
from ..budget import budget_summary
from ..config import load_settings, secret_status
from ..packet import format_input
from ..display import coverage_summary, describe_metric, md_escape as esc, reason_category, staleness_category, status_text
from ..timeutil import format_new_york, parse_utc_iso

LABEL_COLORS = {"CONFIRMED": "green", "THIRD_PARTY": "orange", "AI_ESTIMATE": "violet"}


def _banner(settings) -> None:
    if settings.is_demo:
        st.error(
            "🧪 **DEMO MODE — OFFLINE FICTIONAL DATA.** Every company, number, and link here is "
            "made up to show how the app works. Nothing here is research or advice.",
        )
    else:
        st.info("🔎 Research tool only. No broker connection, no real orders, no automatic trading.")


def _connect(settings):
    if settings.is_demo:
        return db.connect(settings.db_path, "demo")
    db.init_db(settings.db_path, "live")
    return db.connect(settings.db_path, "live")


def _latest_decision(conn):
    return conn.execute(
        """SELECT * FROM journal_entries WHERE entry_type IN ('research_brief', 'no_candidate')
           ORDER BY entry_seq DESC LIMIT 1"""
    ).fetchone()


# ------------------------------------------------------------------ pages

def _day_status(settings) -> tuple[str | None, object]:
    """(closed reason or None, the trading date today's brief should cover)."""
    from datetime import datetime, time as dtime

    from ..market_calendar import closed_reason, previous_trading_day
    from ..timeutil import NEW_YORK, utc_now

    sched = settings.raw["schedule"]
    closed = tuple(sched.get("extra_closed_dates", []))
    now_ny = utc_now().astimezone(NEW_YORK)
    today = now_ny.date()
    reason = closed_reason(today, closed)
    start = dtime.fromisoformat(sched["window_start"])
    if reason or now_ny.time() < start:
        return reason, previous_trading_day(today, closed)
    return None, today


def page_today(conn, settings) -> None:
    st.header("Today's research result")
    closed, expected = _day_status(settings)
    if closed:
        st.info(f"Market closed today ({closed}). No new brief is produced; showing the latest one.")
    entry = _latest_decision(conn)
    if entry is not None and "brief_markdown" in json.loads(entry["payload_json"]):
        return _render_brief_entry(conn, settings, entry, expected)
    if entry is None:
        st.warning("No research result yet. For now: run a screen, then build a research packet on the "
                   "Screening page and review it with your own Claude access. (Try the demo: `python -m omkaka demo`.)")
        return
    payload = json.loads(entry["payload_json"])
    st.caption(f"Written {format_new_york(entry['fetched_at'])} · journal entry {entry['entry_id']}")
    if entry["entry_type"] == "no_candidate":
        st.subheader("No qualifying candidate")
        st.write(esc(entry["body"]))
        return

    st.subheader(f"{payload.get('ticker')} — {payload.get('company')}")
    st.write(f"**Exchange:** {payload.get('exchange', 'Data unavailable')}")
    for c in journal.corrections_for(conn, entry["entry_id"]):
        st.warning(f"**Correction ({format_new_york(c['fetched_at'])}):** {esc(c['body'])}")
    st.write(esc(entry["body"]))

    run = store.run_row(conn, entry["run_id"]) if entry["run_id"] else None
    if run:
        st.write(f"**Decision cutoff:** {format_new_york(run['decision_cutoff_at'])} "
                 "(only evidence available by then may be used)")
        checks = conn.execute(
            "SELECT * FROM source_checks WHERE run_id = ? AND (ticker = ? OR ticker IS NULL)",
            (run["run_id"], payload.get("ticker")),
        ).fetchall()
        cov = coverage_summary(checks)
        st.markdown("#### Source coverage")
        cols = st.columns(4)
        cols[0].metric("Sources OK", cov["ok"])
        cols[1].metric("No results", cov["no_results"])
        cols[2].metric("Partial", cov["partial"])
        cols[3].metric("Unavailable", cov["unavailable"])
        st.dataframe([{"source": c["source"], "status": status_text(c["status"], c["status_reason"]),
                       "checked": format_new_york(c["fetched_at"])} for c in checks],
                     hide_index=True, width="stretch")

    st.markdown("#### Why it deserves attention today")
    st.write(esc(payload.get("why_today", "Data unavailable")))
    st.write(f"**Catalyst / horizon:** {esc(payload.get('catalyst', 'Data unavailable'))}")

    ids = payload.get("metric_ids", [])
    if ids:
        st.markdown("#### Key numbers (each with source and timestamps)")
        rows = conn.execute(
            f"SELECT * FROM metric_values WHERE metric_id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
        st.dataframe([describe_metric(r, settings.staleness_hours(staleness_category(r))) for r in rows],
                     hide_index=True, width="stretch")
        for r in rows:
            if r["calculation_json"]:
                st.caption(f"How {r['metric']} was calculated: `{r['calculation_json']}`")

    st.markdown("#### Claims and their evidence")
    for claim in payload.get("claims", []):
        color = LABEL_COLORS.get(claim["label"], "gray")
        src = f" · evidence `{claim['evidence_id']}`" if claim.get("evidence_id") else " · no source (interpretation)"
        st.markdown(f":{color}[**{claim['label']}**] {esc(claim['text'])}{src}")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Bull case")
        for b in payload.get("bull_case", []):
            st.write(f"- {esc(b)}")
    with c2:
        st.markdown("#### Strongest bear case")
        for b in payload.get("bear_case", []):
            st.write(f"- {esc(b)}")
    st.markdown("#### Risks")
    for k, v in payload.get("risks", {}).items():
        st.write(f"- **{k.title()}:** {esc(v)}")
    st.markdown("#### Missing or stale information")
    for m in payload.get("missing_information", []):
        st.write(f"- ⚠️ {esc(m)}")
    st.markdown("#### What would invalidate this")
    for m in payload.get("would_invalidate", []):
        st.write(f"- {esc(m)}")
    st.markdown("#### Your own research checklist")
    for m in payload.get("research_checklist", []):
        st.checkbox(esc(m), key=f"chk-{m}")
    if run:
        st.markdown("#### Sources used (available by the cutoff)")
        for e in store.evidence_for_run(conn, run["run_id"]):
            if e["ticker"] == payload.get("ticker"):
                st.write(f"- [{esc(e['title'])}]({e['url']}) · {e['source_type']} · "
                         f"published {format_new_york(e['published_at'])} · fetched {format_new_york(e['fetched_at'])}")
        excluded = payload.get("excluded_after_cutoff", [])
        if excluded:
            st.caption(f"Excluded because fetched after the cutoff: {', '.join(excluded)}")
    if settings.is_demo:
        st.caption("Finviz links are shown only for real tickers (not in demo mode).")
    elif payload.get("ticker"):
        st.markdown(f"[Open {payload['ticker']} on Finviz](https://finviz.com/quote.ashx?t={payload['ticker']})")


def _screen_runs(conn):
    return conn.execute(
        """SELECT r.* FROM runs r WHERE EXISTS (SELECT 1 FROM screening_results s WHERE s.run_id = r.run_id)
           ORDER BY r.started_at DESC""").fetchall()


def _fmt_money(v):
    return "Data unavailable" if v is None else f"${v / 1e6:,.1f}M"


def _render_brief_entry(conn, settings, entry, expected) -> None:
    p = json.loads(entry["payload_json"])
    v = p.get("validation", {})
    trading_date = p.get("trading_date")
    if trading_date and trading_date < expected.isoformat():
        st.error(f"STALE: this result is for {trading_date}. There is no result yet for {expected.isoformat()} "
                 "(see Health page for the last run and any missed runs).")
    if p.get("late"):
        st.warning(f"LATE: this result became available at {format_new_york(p.get('available_at'))}, "
                   f"after the {settings.raw['schedule']['deadline']} New York deadline.")
    status_color = {"passed": "green", "flagged": "orange", "rejected": "red"}.get(v.get("status"), "gray")
    st.markdown(f"**{esc(entry['title'])}** · check result :{status_color}[**{v.get('status', 'unknown').upper()}**] · "
                f"{esc(p.get('origin', ''))}")
    st.caption(f"Recorded {format_new_york(entry['fetched_at'])} · journal entry {entry['entry_id']}"
               + (f" · covers trading date {trading_date}" if trading_date else ""))
    for c in journal.corrections_for(conn, entry["entry_id"]):
        st.warning(f"**Correction ({format_new_york(c['fetched_at'])}):** {esc(c['body'])}")
    for w in v.get("warnings", []):
        st.warning(esc(w))
    if entry["run_id"]:
        from ..packet import group_checks
        checks = conn.execute("SELECT * FROM source_checks WHERE run_id=? AND (ticker IS NULL OR ticker=?)",
                              (entry["run_id"], p.get("candidate"))).fetchall()
        cov = coverage_summary(checks)
        cols = st.columns(4)
        cols[0].metric("Source calls OK", cov["ok"])
        cols[1].metric("No results", cov["no_results"])
        cols[2].metric("Partial", cov["partial"])
        cols[3].metric("Unavailable", cov["unavailable"])
        with st.expander("Source coverage details"):
            st.dataframe(group_checks(checks), hide_index=True, width="stretch")
    body = [l for l in p["brief_markdown"].splitlines() if not l.strip().upper().startswith(("CANDIDATE:", "RUN:"))]
    body = [("###" + l[1:]) if l.startswith("# ") else ("####" + l[2:]) if l.startswith("## ") else l for l in body]
    st.markdown(esc("\n".join(body)))
    if p.get("packet_path"):
        from pathlib import Path
        path = Path(p["packet_path"])
        if path.exists():
            st.download_button("Download the research packet for this run", path.read_text(encoding="utf-8"),
                               file_name=path.name, mime="text/markdown")


def page_review(conn, settings) -> None:
    from .. import review
    from ..brief import candidate_brief, no_candidate_brief
    from ..selection import select_candidate

    st.header("Review a brief")
    st.write("Paste a brief (yours, or one written by your own Claude from the research packet). The app checks "
             "every number against the right company, metric, period and source, and every citation against the "
             "evidence available at the run's cutoff. Nothing is sent anywhere.")
    runs = _screen_runs(conn)
    if not runs:
        st.warning("No screening run yet.")
        return
    labels = {f"{r['run_id']} · {format_new_york(r['started_at'])}": r for r in runs}
    run = labels[st.selectbox("Run", list(labels))]
    if st.button("Start from the automatic data-only brief"):
        chosen, table = select_candidate(conn, run["run_id"], settings)
        st.session_state["brief_text"] = (candidate_brief(conn, run["run_id"], chosen, settings.is_demo) if chosen
                                          else no_candidate_brief(run["run_id"], table))
    text = st.text_area("Brief", key="brief_text", height=350,
                        placeholder=f"CANDIDATE: TICKER\nRUN: {run['run_id']}\n- [CONFIRMED] ... [ev: ...]")
    if st.button("Check brief") and text.strip():
        st.session_state["brief_report"] = review.validate(conn, text, run["run_id"])
    report = st.session_state.get("brief_report")
    if report:
        color = {"passed": "green", "flagged": "orange", "rejected": "red"}[report.status]
        st.markdown(f"### Result: :{color}[{report.status.upper()}]")
        for e in report.errors:
            st.error(esc(e))
        for w in report.warnings:
            st.warning(esc(w))
        if report.claims:
            st.dataframe([{"line": c["line"], "label": c["label"], "claim": c["text"][:120],
                           "problems": "; ".join(c["problems"]) or "none"} for c in report.claims],
                         hide_index=True, width="stretch")
        if st.button("Save to journal" + (" (as a rejected note)" if report.status == "rejected" else "")):
            entry = review.save_brief(conn, text, report, "Brief checked in the dashboard")
            st.success(f"Saved as journal entry {entry}.")
            del st.session_state["brief_report"]


def page_watchlist(conn, settings) -> None:
    st.header("Screening, shortlist & watchlist")
    st.caption("Scores are for prioritizing what to read first. They are NOT probabilities of success. "
               "Reddit/social activity never adds points.")
    runs = _screen_runs(conn)
    if not runs:
        st.warning("No screening run yet. Run `python -m omkaka screen` (free sources; takes several minutes).")
    else:
        labels = {f"{r['run_id']} · {format_new_york(r['started_at'])} · {r['status']}": r for r in runs}
        run = labels[st.selectbox("Screening run", list(labels))]
        rid = run["run_id"]
        pre = {r["outcome"]: r["n"] for r in conn.execute(
            "SELECT outcome, COUNT(*) n FROM screening_results WHERE run_id=? AND stage='prefilter' GROUP BY outcome",
            (rid,))}
        cols = st.columns(4)
        for col, (key, label) in zip(cols, [("passed", "Passed"), ("failed_threshold", "Failed a rule"),
                                            ("insufficient_data", "Insufficient data"), (None, "Companies screened")]):
            col.metric(label, sum(pre.values()) if key is None else pre.get(key, 0))

        st.markdown("#### Shortlist (plus your watchlist)")
        short = conn.execute("SELECT * FROM screening_results WHERE run_id=? AND stage='shortlist' "
                             "ORDER BY rank IS NULL, rank, ticker", (rid,)).fetchall()
        outcome_text = {"passed": "passed", "insufficient_data": "WITHHELD: insufficient data",
                        "watchlist_only": "watchlist only (failed screen)", "failed_threshold": "failed"}
        st.dataframe([{
            "rank": r["rank"] or "—", "ticker": r["ticker"], "company": r["company_name"],
            "outcome": outcome_text[r["outcome"]],
            "priority score": "—" if r["score"] is None else f"{r['score']:.1f} / 100",
            "market cap": _fmt_money(json.loads(r["inputs_json"]).get("market_cap")),
            "avg $ volume/day": _fmt_money(json.loads(r["inputs_json"]).get("avg_dollar_volume")),
            "risk flags": len(json.loads(r["flags_json"])),
        } for r in short], hide_index=True, width="stretch")

        for r in short:
            flags, reasons = json.loads(r["flags_json"]), json.loads(r["reasons_json"])
            inputs, comps = json.loads(r["inputs_json"]), json.loads(r["components_json"])
            with st.expander(f"{r['ticker']} — {r['company_name']} · {outcome_text[r['outcome']]}"):
                for reason in reasons:
                    st.error(esc(reason))
                for f in flags:
                    st.warning(esc(f))
                if comps:
                    st.dataframe([{"component": k, "points": v["points"], "max": v["max"],
                                   "input": format_input(k, v["input"]), "how scored": v["note"]} for k, v in comps.items()],
                                 hide_index=True, width="stretch")
                for c in inputs.get("catalyst_8k") or []:
                    st.write(f"- 8-K filed {c['filed']}: {', '.join(c['items']) or 'items not listed'}")
                metrics = [m for m in store.metrics_as_of(conn, parse_utc_iso(run["decision_cutoff_at"]),
                                                          ticker=r["ticker"]) if m["run_id"] == rid]
                if metrics:
                    st.dataframe([describe_metric(m, settings.staleness_hours(staleness_category(m)))
                                  for m in metrics], hide_index=True, width="stretch")
                ev = [e for e in store.evidence_for_run(conn, rid) if e["ticker"] == r["ticker"]]
                for e in ev:
                    st.write(f"- [{esc(e['title'])}]({e['url']}) · {e['source_type']} · published "
                             f"{format_new_york(e['published_at'])}")
                checks = conn.execute("SELECT * FROM source_checks WHERE run_id=? AND ticker=?",
                                      (rid, r["ticker"])).fetchall()
                st.caption("Coverage: " + " · ".join(
                    f"{c['source']}: {status_text(c['status'], c['status_reason'])}" for c in checks))
                if not settings.is_demo:
                    st.markdown(f"[Finviz](https://finviz.com/quote.ashx?t={r['ticker']})")

        st.markdown("#### Why companies were excluded")
        reasons = {}
        for row in conn.execute("SELECT outcome, reasons_json FROM screening_results WHERE run_id=? "
                                "AND stage='prefilter' AND outcome != 'passed'", (rid,)):
            for reason in json.loads(row["reasons_json"]):
                key = (row["outcome"], reason_category(reason))
                reasons[key] = reasons.get(key, 0) + 1
        st.dataframe([{"outcome": k[0], "reason": k[1], "companies": n}
                      for k, n in sorted(reasons.items(), key=lambda kv: -kv[1])], hide_index=True, width="stretch")

        st.markdown("#### Research packet")
        st.write("A single document with this run's shortlist, numbers, sources, and review instructions. "
                 "Read it yourself or attach/paste it into your existing Claude. No paid AI calls are made.")
        if st.button("Build research packet"):
            from ..packet import write_packet
            path, meta = write_packet(conn, settings, settings.db_path.parent / "packets", rid)
            st.session_state["packet"] = (path.name, path.read_text(encoding="utf-8"))
            st.success(f"Saved {path} and recorded it in the journal.")
        if "packet" in st.session_state:
            name, text = st.session_state["packet"]
            st.download_button("Download packet (.md)", text, file_name=name, mime="text/markdown")

    st.markdown("#### Your watchlist")
    st.caption("Watchlist companies are researched every run even if they fail the screen.")
    wl = store.current_watchlist(conn)
    st.write(", ".join(wl) if wl else "(empty)")
    with st.form("watch", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 3, 1])
        ticker = c1.text_input("Ticker")
        note = c2.text_input("Note (optional)")
        action = c3.radio("Action", ["add", "remove"])
        if st.form_submit_button("Save") and ticker.strip():
            try:
                store.watchlist_change(conn, ticker, action, note or None)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def page_evidence(conn, settings) -> None:
    st.header("Evidence & research history")
    runs = conn.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()
    if not runs:
        st.write("No research runs yet.")
        return
    labels = {f"{r['run_id']} · {format_new_york(r['started_at'])} · {r['status']}": r for r in runs}
    run = labels[st.selectbox("Run", list(labels))]
    cutoff = run["decision_cutoff_at"]
    only_known = st.checkbox("Show only what was known at the decision cutoff", value=True)
    rows = conn.execute("SELECT * FROM evidence WHERE run_id = ? ORDER BY fetched_at", (run["run_id"],)).fetchall()
    usable = {e["evidence_id"] for e in store.evidence_for_run(conn, run["run_id"])}
    table = []
    for e in rows:
        if only_known and e["evidence_id"] not in usable:
            continue
        table.append({
            "evidence_id": e["evidence_id"], "ticker": e["ticker"], "type": e["source_type"],
            "document": e["doc_type"],
            "primary source": "yes" if e["is_primary_source"] else "no", "title": e["title"],
            "published": format_new_york(e["published_at"]), "market time": format_new_york(e["effective_at"]),
            "fetched": format_new_york(e["fetched_at"]),
            "usable at cutoff": "yes" if e["evidence_id"] in usable else "NO (after cutoff)",
            "status": status_text(e["status"], e["status_reason"]), "url": e["url"],
        })
    st.caption(f"Decision cutoff: {format_new_york(cutoff)}")
    st.dataframe(table, hide_index=True, width="stretch")


def page_portfolio(conn, settings) -> None:
    from .. import portfolio as pf

    st.header("Paper portfolio vs SPY")
    st.caption("Paper money only: no broker, no real orders. Nothing is bought automatically; every order is yours.")
    filled = pf.fill_pending(conn, settings)
    for f in filled:
        (st.success if f["status"] == "filled" else st.warning)(f"Order {f['order_id']}: {f['status']} "
                                                                  f"{esc(f.get('reason') or '')}")
    v = pf.valuation(conn, settings)
    bm = settings.raw.get("benchmark", {}).get("ticker", "SPY")
    hist = pf.history(conn, settings)
    last = hist[-1] if hist else {}
    cols = st.columns(4)
    cols[0].metric("Paper cash", f"${v['cash']:,.2f}")
    cols[1].metric("Total value", "Data unavailable" if v["total"] is None else f"${v['total']:,.2f}")
    cols[2].metric("Net contributions", f"${v['contributions']:,.2f}")
    pr, sr = last.get("portfolio_return"), last.get("spy_return")
    cols[3].metric(f"Return vs {bm} (price-only)", "—" if pr is None else f"{pr:+.2%}",
                   None if pr is None or sr is None else f"{pr - sr:+.2%} vs {bm} {sr:+.2%}")
    if v["missing_prices"]:
        st.error("Data unavailable: no price for " + ", ".join(v["missing_prices"]) +
                 ". The total is shown as unavailable instead of guessing.")

    st.markdown("#### Holdings")
    if v["holdings"]:
        st.dataframe([{"ticker": h["ticker"], "shares": h["shares"],
                       "last close": "Data unavailable" if h["close"] is None else f"${h['close']:,.2f}",
                       "close date": h["close_date"] or "—",
                       "value": "Data unavailable" if h["value"] is None else f"${h['value']:,.2f}",
                       "cost basis": "—" if h["cost_basis"] is None else f"${h['cost_basis']:,.2f}",
                       "flags": "; ".join(h["flags"]) or "—"} for h in v["holdings"]], hide_index=True, width="stretch")
    else:
        st.write("No holdings.")
    pending = pf.pending_orders(conn)
    if pending:
        st.markdown("#### Waiting orders (fill at the next close after you placed them)")
        st.dataframe([{"order": o["order_id"], "side": o["side"], "ticker": o["ticker"], "quantity": o["quantity"],
                       "placed": format_new_york(o["decided_at"])} for o in pending], hide_index=True, width="stretch")

    if hist:
        st.markdown(f"#### Return on contributions: paper portfolio vs {bm} mirror (same money, same timing, price-only)")
        pct = lambda x: None if x is None else round(x * 100, 3)
        chart = [{"date": h["date"], "Paper portfolio": pct(h["portfolio_return"]),
                  f"{bm} mirror": pct(h["spy_return"])} for h in hist]
        st.line_chart(chart, x="date", y=["Paper portfolio", f"{bm} mirror"], color=["#2a78d6", "#eb6834"],
                      y_label="Return (%)")
        st.caption("Gaps in the portfolio line mean a holding had no price that day (unknown, not zero).")
        with st.expander("Table view"):
            st.dataframe(hist, hide_index=True, width="stretch")

    c1, c2 = st.columns(2)
    with c1.form("cash", clear_on_submit=True):
        st.markdown("**Add or withdraw paper cash**")
        kind = st.radio("Type", ["deposit", "withdrawal"], horizontal=True)
        amount = st.number_input("Amount (USD)", min_value=0.0, step=100.0)
        if st.form_submit_button("Save") and amount > 0:
            try:
                pf.cash_movement(conn, kind, amount)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with c2.form("order", clear_on_submit=True):
        st.markdown("**Place a paper order**")
        side = st.radio("Side", ["buy", "sell"], horizontal=True)
        ticker = st.text_input("Ticker")
        qty = st.number_input("Shares", min_value=0.0, step=1.0)
        briefs = conn.execute("SELECT entry_id, title FROM journal_entries WHERE entry_type='research_brief' "
                              "ORDER BY entry_seq DESC LIMIT 30").fetchall()
        link = st.selectbox("Link to a research brief (optional)", ["(none)"] + [f"{b['entry_id']} · {b['title']}"
                                                                                for b in briefs])
        note = st.text_input("Why? (saved in the journal)")
        if st.form_submit_button("Place order") and ticker.strip() and qty > 0:
            try:
                oid = pf.place_order(conn, settings, side, ticker, qty, note or None,
                                     None if link == "(none)" else link.split(" · ")[0])
                st.success(f"Order {oid} placed. It fills at the close of the first trading day that ends after now.")
            except ValueError as exc:
                st.error(str(exc))

    with st.expander("Record a dividend or split (with its source)"):
        with st.form("action", clear_on_submit=True):
            kind = st.radio("Kind", ["dividend", "split"], horizontal=True)
            t = st.text_input("Ticker ")
            ex = st.date_input("Ex-date")
            val = st.number_input("Dividend per share (USD) or split ratio (e.g. 2 for 2-for-1)", min_value=0.0)
            src = st.text_input("Source (e.g. company press release link)")
            if st.form_submit_button("Save") and t.strip() and val > 0 and src.strip():
                pf.record_corporate_action(conn, kind, t, ex.isoformat(), val, src)
                st.rerun()

    st.markdown("#### What happened after each research candidate (hypothetical, not trades)")
    out = pf.candidate_outcomes(conn, settings)
    if out:
        st.dataframe([{"brief date": o["brief_date"], "ticker": o["ticker"], "check": o["check"],
                       "from": o.get("from", "—"), "to": o.get("to", "—"),
                       "candidate": "—" if o.get("return") is None else f"{o['return']:+.2%}",
                       bm: "—" if o.get("spy_return") is None else f"{o['spy_return']:+.2%}",
                       "status": o["status"]} for o in out], hide_index=True, width="stretch")
        st.caption("From the close on the brief's date to the latest close; price-only. Past moves say nothing "
                   "certain about the future.")
    else:
        st.write("No research candidates yet.")

    with st.expander("Assumptions (read these)"):
        c = settings.raw["portfolio"]
        st.markdown(f"""
- **Fill price:** the close of the first trading day that ends after you place the order, {c['slippage_bps']} bps
  worse (buys pay more, sells receive less), fee ${c['fee_per_trade_usd']:.2f} per trade. Only closes we had
  actually downloaded are used; nothing is back-dated.
- **No price for {c['unfilled_after_trading_days']} trading days:** the order is rejected.
- **Limits:** at most {c['max_holdings']} holdings, no margin, no short selling.
- **Dividends and splits:** only what you record (with a source). Big one-day moves are flagged as possible splits.
- **{bm} comparison:** every deposit and withdrawal is mirrored into {bm} at the same fill rule. Both sides are
  price-only (dividends excluded) so the comparison is like-for-like.
""".replace("$", "\\$"))


def page_journal(conn, settings) -> None:
    st.header("Research journal (append-only)")
    ok, problems = journal.verify_chain(conn)
    if ok:
        st.success("Integrity check passed: no entry has been altered or removed.")
    else:
        st.error("Integrity problems found:\n\n" + "\n".join(f"- {p}" for p in problems))
    with st.expander("Add a note (notes can't be edited later; add a correction instead)"):
        with st.form("note", clear_on_submit=True):
            title = st.text_input("Title")
            body = st.text_area("Note")
            if st.form_submit_button("Save note") and title.strip() and body.strip():
                journal.append_entry(conn, "note", title, body)
                st.rerun()
    entries = journal.list_entries(conn)
    for e in reversed(entries):
        tag = f" · corrects {e['corrects_entry_id']}" if e["corrects_entry_id"] else ""
        with st.expander(f"#{e['entry_seq']} [{e['entry_type']}] {e['title']} — {format_new_york(e['fetched_at'])}{tag}"):
            st.write(esc(e["body"]))
            st.caption(f"id {e['entry_id']} · hash {e['entry_hash'][:16]}…")
            if e["payload_json"] != "{}":
                st.json(json.loads(e["payload_json"]), expanded=False)
    if entries:
        with st.expander("Add a correction to an earlier entry"):
            with st.form("correction", clear_on_submit=True):
                target = st.selectbox("Entry to correct", [e["entry_id"] for e in entries])
                title = st.text_input("Correction title")
                body = st.text_area("What was wrong and what is right")
                if st.form_submit_button("Save correction") and title.strip() and body.strip():
                    journal.add_correction(conn, target, title, body)
                    st.rerun()


def page_health(conn, settings) -> None:
    from ..daily import latest_daily_status
    from ..maintenance import backup_database, doctor

    st.header("Health: sources, schedule, spending, backups")
    st.write(f"**Mode:** {settings.mode.upper()} · **Database:** `{settings.db_path}`")
    last = store.last_successful_run(conn)
    st.write(f"**Last successful run:** {format_new_york(last['finished_at']) if last else 'none yet'}")
    daily = latest_daily_status(conn)
    if daily:
        st.write(f"**Latest daily result:** {esc(daily['title'])} for {daily['trading_date']}, available "
                 f"{format_new_york(daily['available_at'])}" + (" — **LATE**" if daily.get("late") else ""))
    missed = conn.execute("SELECT title FROM journal_entries WHERE dedupe_key LIKE 'missed-%' "
                          "ORDER BY entry_seq DESC LIMIT 5").fetchall()
    for m in missed:
        st.warning(esc(m["title"]))
    sched = settings.raw["schedule"]
    st.caption(f"Schedule: research window opens {sched['window_start']}, target {sched['deadline']} New York time, "
               "weekdays except NYSE holidays. The computer must be on (and awake) for it to run; missed days are "
               "recorded, never back-filled.")

    st.markdown("#### Setup check (doctor)")
    st.dataframe([{"status": r[0], "check": r[1], "detail": r[2]} for r in doctor(settings)],
                 hide_index=True, width="stretch")
    if not settings.is_demo and st.button("Back up the database now"):
        st.success(f"Backup saved: {backup_database(settings)}")

    st.markdown("#### Spending")
    b = budget_summary(settings)
    st.write(f"Ceiling **\\${b['ceiling_usd']:.2f}** total (no reset), covering {', '.join(b['covers'])}. "
             f"Safety margin \\${b['safety_margin_usd']:.2f}. Spent so far: **\\${b['spent_usd']:.2f}**.")
    st.info("Plan: $0 additional spending. Only free data sources are used, and no paid AI calls are made. "
            "Any provider marked paid is refused by the app.")
    st.markdown("#### Data providers")
    st.dataframe([{"provider": name, "cost": "PAID (blocked)" if cfg.get("paid", True) else "free",
                   "min seconds between calls": cfg.get("min_interval_seconds"),
                   "max calls per run": cfg.get("max_calls_per_run")}
                  for name, cfg in settings.raw.get("providers", {}).items()], hide_index=True, width="stretch")

    st.markdown("#### Latest status of each source")
    checks = conn.execute(
        """SELECT * FROM source_checks WHERE check_id IN
           (SELECT MAX(check_id) FROM source_checks GROUP BY source, coalesce(ticker, ''))
           ORDER BY source, ticker"""
    ).fetchall()
    if checks:
        st.dataframe([{"source": c["source"], "ticker": c["ticker"], "provider": c["provider"],
                       "status": status_text(c["status"], c["status_reason"]),
                       "checked": format_new_york(c["fetched_at"])} for c in checks],
                     hide_index=True, width="stretch")
    else:
        st.write("No source has been checked yet. Try `python -m omkaka sources-check`.")

    st.markdown("#### Secrets (set / not set only — values are never shown)")
    st.dataframe([{"name": k, "status": "set" if v else "not set"} for k, v in secret_status().items()],
                 hide_index=True, width="stretch")


PAGES = {
    "Today": page_today,
    "Watchlist & screening": page_watchlist,
    "Review a brief": page_review,
    "Evidence & history": page_evidence,
    "Paper portfolio": page_portfolio,
    "Journal": page_journal,
    "Health & spending": page_health,
}


def main() -> None:
    settings = load_settings()
    st.sidebar.title("OmKakaFinance")
    st.sidebar.caption("DEMO MODE" if settings.is_demo else "Live mode")
    choice = st.sidebar.radio("View", list(PAGES))
    _banner(settings)
    try:
        conn = _connect(settings)
    except FileNotFoundError:
        st.error("Demo database not found. Run: `python -m omkaka demo-reset`")
        return
    try:
        PAGES[choice](conn, settings)
    finally:
        conn.close()

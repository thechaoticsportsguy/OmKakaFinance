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

def page_today(conn, settings) -> None:
    st.header("Today's candidate")
    entry = _latest_decision(conn)
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
    st.header("Paper portfolio vs SPY")
    st.info("Arrives in Phase 5: up to 15 paper holdings, cash, explicit paper trades only, SPY comparison. "
            "No real money, no broker.")


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
    st.header("Source health, spending & runs")
    st.write(f"**Mode:** {settings.mode.upper()} · **Database:** `{settings.db_path}`")
    last = store.last_successful_run(conn)
    st.write(f"**Last successful run:** {format_new_york(last['finished_at']) if last else 'none yet'}")

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

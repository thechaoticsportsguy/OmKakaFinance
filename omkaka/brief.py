"""Automatic, DATA-ONLY research brief (no AI).

Every line is either a labeled claim that cites its metric/evidence IDs, or a
SCORE / RISK / MISSING line copied from what the app recorded. It is checked
by review.validate() like any other brief. Interpretation (a narrative bull
case, time horizon, judgment) is left for your own review with your Claude.
"""
from __future__ import annotations

import json

from . import store
from .display import format_number
from .timeutil import format_new_york, parse_utc_iso


def _metrics(conn, run, ticker) -> dict:
    rows = store.metrics_as_of(conn, parse_utc_iso(run["decision_cutoff_at"]), ticker=ticker)
    return {m["metric"]: m for m in rows if m["run_id"] == run["run_id"]}


def _v(m) -> str:
    return format_number(m["value"], m["unit"])


def candidate_brief(conn, run_id: str, row, demo: bool = False) -> str:
    run = store.run_row(conn, run_id)
    t = row["ticker"]
    inputs, comps = json.loads(row["inputs_json"]), json.loads(row["components_json"])
    flags = json.loads(row["flags_json"])
    M = _metrics(conn, run, t)
    ok = {k: m for k, m in M.items() if m["status"] == "OK"}
    ev = [e for e in store.evidence_for_run(conn, run_id) if e["ticker"] == t]
    L = [f"CANDIDATE: {t}", f"RUN: {run_id}", "",
         f"# Research candidate: {t} — {row['company_name']} ({row['exchange']})" + (" — DEMO, FICTIONAL" if demo else ""),
         "", f"Automated data-only brief (no AI). Research cutoff {format_new_york(run['decision_cutoff_at'])}. "
         "This is research to review, not advice or a recommendation.", ""]

    L += ["## Why it deserves attention today", ""]
    L.append(f"- SCORE: prioritization score {row['score']} of 100, rank {row['rank']} (not a probability)")
    for name, c in comps.items():
        L.append(f"- SCORE: {name} {c['points']} of {c['max']} points")
    for c in inputs.get("catalyst_8k") or []:
        e = next((x for x in ev if x["source_identifier"] == c["accession"]), None)
        if e:
            L.append(f"- [CONFIRMED] {c['form']} filed {c['filed']}: {', '.join(c['items']) or 'items not listed'}. "
                     f"[ev: {e['evidence_id']}]")
    L += ["", "Catalyst time horizon: not estimated (no supported basis in the data).", ""]

    L += ["## Key numbers", ""]
    if "close_price" in ok:
        m = ok["close_price"]
        L.append(f"- [THIRD_PARTY] Closing price {_v(m)} ({m['period_label']}, market data). [m: {m['metric_id']}]")
    if "market_cap" in ok and "shares_outstanding" in ok and "close_price" in ok:
        m = ok["market_cap"]
        L.append(f"- [THIRD_PARTY] Market cap about {_v(m)} (calculated: price × SEC share count; the two dates "
                 f"differ). [m: {m['metric_id']}] [m: {ok['shares_outstanding']['metric_id']}]")
    if "avg_daily_dollar_volume" in ok:
        m = ok["avg_daily_dollar_volume"]
        L.append(f"- [THIRD_PARTY] Average daily dollar volume {_v(m)} ({m['period_label']}). [m: {m['metric_id']}]")
    for key, words in (("revenue_quarter", "Quarterly revenue"), ("revenue_quarter_prior_year", "Revenue a year earlier"),
                       ("net_income_quarter", "Quarterly net income"), ("cash", "Cash and equivalents"),
                       ("operating_cash_flow", "Operating cash flow"), ("shares_outstanding", "Shares outstanding")):
        if key in ok:
            m = ok[key]
            L.append(f"- [CONFIRMED] {words} {_v(m)} ({m['period_label']}, reported to the SEC). [m: {m['metric_id']}]")
    for key, words, a, b in (("revenue_growth_yoy", "Revenue change vs the same quarter a year earlier",
                              "revenue_quarter", "revenue_quarter_prior_year"),
                             ("share_count_change_yoy", "Share count change over about a year",
                              "shares_outstanding", "shares_outstanding_year_ago")):
        if key in ok and a in ok and b in ok:
            L.append(f"- [CONFIRMED] {words}: {_v(ok[key])} (calculated from two SEC-reported numbers). "
                     f"[m: {ok[key]['metric_id']}] [m: {ok[a]['metric_id']}] [m: {ok[b]['metric_id']}]")
    L.append("")

    L += ["## News and filing timeline", ""]
    seen_groups = set()
    for e in sorted(ev, key=lambda x: x["published_at"] or ""):
        when = format_new_york(e["published_at"])
        if e["source_type"] == "filing":
            L.append(f"- [CONFIRMED] {e['title']} (accepted {when}). [ev: {e['evidence_id']}]")
        elif e["source_type"] == "news":
            repeat = e["dedupe_group"] in seen_groups
            seen_groups.add(e["dedupe_group"])
            L.append(f"- [THIRD_PARTY] {e['title']} (published {when})"
                     f"{' — repeats an earlier story, not independent' if repeat else ''}. [ev: {e['evidence_id']}]")
    L.append("")

    L += ["## Bull points (from data only)", ""]
    g = ok.get("revenue_growth_yoy")
    if g and g["value"] > 0:
        L.append(f"- [CONFIRMED] Revenue grew {_v(g)} year over year. [m: {g['metric_id']}] "
                 f"[m: {ok['revenue_quarter']['metric_id']}] [m: {ok['revenue_quarter_prior_year']['metric_id']}]")
    if inputs.get("catalyst_8k"):
        L.append("- [AI_ESTIMATE] A recent 8-K filing may be a catalyst; read it to judge its importance.")
    L += ["", "## Bear points and risks (from data only)", ""]
    if g and g["value"] < 0:
        L.append(f"- [CONFIRMED] Revenue fell {_v(g)} year over year. [m: {g['metric_id']}] "
                 f"[m: {ok['revenue_quarter']['metric_id']}] [m: {ok['revenue_quarter_prior_year']['metric_id']}]")
    for f in flags:
        L.append(f"- RISK: {f}")
    if not flags:
        L.append("- No automated risk flag was raised. That is not the same as no risk.")
    L += ["", "## Missing or stale information", ""]
    missing = [m for m in M.values() if m["status"] != "OK"]
    for m in missing:
        L.append(f"- MISSING: {m['metric']}: {m['status_reason']}")
    for c in conn.execute("SELECT * FROM source_checks WHERE run_id=? AND ticker=? AND status NOT IN ('OK','NO_RESULTS')",
                          (run_id, t)):
        L.append(f"- MISSING: {c['source']}: {c['status_reason']}")
    if not missing:
        L.append("- No numbers were missing for this company.")
    L += ["", "## What would invalidate this", "",
          "- [AI_ESTIMATE] A new financing or share-registration filing (S-1, S-3, 424B), which would signal dilution.",
          "- [AI_ESTIMATE] The next quarterly filing showing revenue decline or a larger cash burn.",
          "- [AI_ESTIMATE] The catalyst filing being amended, withdrawn, or contradicted by the company.", "",
          "## Your own research checklist", ""]
    if not demo:
        L.append(f"- [ ] Finviz: https://finviz.com/quote.ashx?t={t} (chart, float, short interest, insider trades)")
        L.append(f"- [ ] SEC filings: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={row['cik']}")
    L += ["- [ ] Read the recent 8-K and the latest 10-Q/10-K risk factors.",
          "- [ ] Check for going-concern language, debt terms, and share issuance plans.",
          "- [ ] Check average volume and bid/ask spread before assuming you could trade it.",
          "- [ ] Paste this brief and the research packet into your Claude for interpretation."]
    return "\n".join(L) + "\n"


def no_candidate_brief(run_id: str, table: list[dict], reason: str | None = None) -> str:
    L = ["CANDIDATE: NONE", f"RUN: {run_id}", "", "# No qualifying candidate", ""]
    if reason:
        L.append(f"Reason: {reason}")
    else:
        L.append("No shortlisted company passed every selection gate. Reasons by company:")
        L.append("")
        for t in [t for t in table if not t["eligible"]]:
            L.append(f"- GATE: {t['ticker']}: " + "; ".join(t["reasons"]))
    return "\n".join(L) + "\n"

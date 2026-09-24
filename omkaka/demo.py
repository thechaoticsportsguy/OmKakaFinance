"""Builds the OFFLINE DEMO database from fixtures/demo/demo_dataset.json.

Safety rules:
  * Demo data only ever goes into the demo database file (stamped 'demo').
  * The demo database can be wiped and rebuilt; the live one never is.
  * All demo companies, numbers, and links are fictional.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import db, journal, store
from .config import PROJECT_ROOT, Settings
from .timeutil import to_utc_iso, utc_now

FIXTURE_FILE = PROJECT_ROOT / "fixtures" / "demo" / "demo_dataset.json"
DEMO_PROVIDER = "DEMO FIXTURE"


class NotADemoDatabase(RuntimeError):
    pass


def _ago(now: datetime, minutes) -> datetime | None:
    return None if minutes is None else now - timedelta(minutes=minutes)


def reset_demo_db(settings: Settings, now: datetime | None = None) -> Path:
    """Delete (only if it is a demo DB) and rebuild the demo database."""
    if not settings.is_demo:
        raise NotADemoDatabase("reset_demo_db must be called with demo settings.")
    path = settings.db_path
    live_path = (PROJECT_ROOT / settings.raw["storage"]["live_db_path"]).resolve()
    if path.resolve() == live_path:
        raise NotADemoDatabase("Demo path points at the live database. Refusing.")
    if path.exists():
        # connect() raises WrongDatabaseMode if this file is not a demo DB.
        db.connect(path, "demo").close()
        path.unlink()
    db.init_db(path, "demo")
    conn = db.connect(path, "demo")
    try:
        seed_demo(conn, now=now)
        seed_demo_screen(conn, settings, now=now)
    finally:
        conn.close()
    return path


def seed_demo_screen(conn, settings: Settings, now: datetime | None = None) -> dict:
    """Run the REAL screening pipeline against the offline fixture market."""
    import itertools

    from .pipeline import build_clients, run_screen
    from .sources.fixtures import FixtureTransport
    from .timeutil import NEW_YORK

    if db.db_mode(conn) != "demo":
        raise NotADemoDatabase("Refusing to run the demo screen against a non-demo database.")
    base = (now or utc_now()) - timedelta(minutes=40)
    tick = itertools.count()
    clock = lambda: base + timedelta(seconds=next(tick))
    spec = json.loads((PROJECT_ROOT / "fixtures" / "demo" / "phase2_market.json").read_text(encoding="utf-8"))
    for t in spec.get("demo_watchlist", []):
        store.watchlist_change(conn, t, "add", note="demo watchlist entry", now=base)
    clients = build_clients(conn, settings, transport=FixtureTransport(base.astimezone(NEW_YORK).date()),
                            sleep=lambda s: None, now=clock, demo_credentials=True)
    summary = run_screen(conn, settings, clients, run_kind="demo", run_id="demo-screen-001", log=lambda m: None)
    # Publish the day's result exactly as the daily job would (without its time-of-day checks).
    from .daily import publish_result
    done = clock()
    publish_result(conn, settings, "demo-screen-001",
                   {"trading_date": done.astimezone(NEW_YORK).date().isoformat(), "available_at": to_utc_iso(done),
                    "late": False, "demo": True}, dedupe_key=None)
    seed_demo_portfolio(conn, settings, done)
    return summary


def seed_demo_portfolio(conn, settings: Settings, now: datetime) -> None:
    """A small FICTIONAL paper portfolio so the Portfolio page has something to show."""
    from . import portfolio as pf
    from .market_calendar import trading_days_before
    from .timeutil import NEW_YORK

    if db.db_mode(conn) != "demo":
        raise NotADemoDatabase("Refusing to create demo paper trades in a non-demo database.")
    days = trading_days_before(now.astimezone(NEW_YORK).date(), 15)
    at = lambda d, hh: datetime.combine(d, datetime.min.time(), tzinfo=NEW_YORK).replace(hour=hh)
    pf.cash_movement(conn, "deposit", 25_000, "DEMO: fictional starting paper cash", now=at(days[0], 9))
    brief = conn.execute("SELECT entry_id FROM journal_entries WHERE entry_type='research_brief' "
                         "ORDER BY entry_seq DESC LIMIT 1").fetchone()
    pf.place_order(conn, settings, "buy", "DEMOX", 600, "DEMO: fictional order", now=at(days[2], 10))
    pf.place_order(conn, settings, "buy", "DEMOA", 200, "DEMO: fictional order linked to the demo brief",
                   linked_entry_id=brief["entry_id"] if brief else None, now=at(days[5], 10))
    pf.fill_pending(conn, settings, now=now)


def seed_demo(conn, now: datetime | None = None) -> str:
    if db.db_mode(conn) != "demo":
        raise NotADemoDatabase("Refusing to load demo fixtures into a non-demo database.")
    now = now or utc_now()
    data = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))
    r = data["run"]
    run_id = store.start_run(
        conn, "demo", decision_cutoff=_ago(now, r["cutoff_minutes_ago"]),
        settings_snapshot={"demo": True, "fixture": FIXTURE_FILE.name},
        now=_ago(now, r["started_minutes_ago"]), run_id=r["run_id"],
    )
    for c in data["source_checks"]:
        store.record_source_check(
            conn, run_id, c["source"], c["provider"], c["status"], reason=c.get("reason"),
            ticker=c.get("ticker"), result_count=c.get("result_count"),
            fetched_at=_ago(now, c["minutes_ago"]),
        )
    for e in data["evidence"]:
        store.record_evidence(
            conn, run_id, e["source_type"], DEMO_PROVIDER, e["source_identifier"],
            fetched_at=_ago(now, e["fetched_minutes_ago"]), ticker=e["ticker"],
            company_name=e["company_name"], url=e["url"], title=e["title"],
            excerpt=e.get("excerpt"), storage_note="Fictional demo text.",
            published_at=_ago(now, e.get("published_minutes_ago")),
            effective_at=_ago(now, e.get("effective_minutes_ago")),
            evidence_id=e["evidence_id"],
        )
    urls = {e["evidence_id"]: e["url"] for e in data["evidence"]}
    values = {}
    for m in data["metrics"]:
        store.record_metric(
            conn, run_id, m["ticker"], m["metric"], m["period_label"], m["unit"], m["value_kind"],
            DEMO_PROVIDER, fetched_at=_ago(now, m["fetched_minutes_ago"]), value=m["value"],
            status=m.get("status", "OK"), reason=m.get("reason"), evidence_id=m.get("evidence_id"),
            period_end=m.get("period_end"), source_url=urls.get(m.get("evidence_id")),
            source_identifier=m.get("evidence_id"),
            published_at=_ago(now, m.get("published_minutes_ago")),
            effective_at=_ago(now, m.get("effective_minutes_ago")), metric_id=m["metric_id"],
        )
        values[m["metric_id"]] = m

    # Market cap is CALCULATED by code (not typed in), with inputs stored.
    price, shares = values["demo-m-price-x"], values["demo-m-shares-x"]
    store.record_metric(
        conn, run_id, "DEMOX", "market_cap", "as_of", "USD", "CALCULATED", "OmKakaFinance calculation",
        fetched_at=_ago(now, 165), value=round(price["value"] * shares["value"], 2),
        calculation={
            "formula": "close_price * shares_outstanding",
            "inputs": {"close_price": {"metric_id": "demo-m-price-x", "value": price["value"]},
                       "shares_outstanding": {"metric_id": "demo-m-shares-x", "value": shares["value"]}},
            "note": "Share count date and price date differ; see each input's timestamps.",
        },
        effective_at=_ago(now, price["effective_minutes_ago"]), metric_id="demo-m-mcap-x",
    )
    store.finish_run(conn, run_id, "completed", now=_ago(now, 60))
    _seed_journal(conn, run_id, now)
    return run_id


def _seed_journal(conn, run_id: str, now: datetime) -> None:
    journal.append_entry(
        conn, "no_candidate", "DEMO — No qualifying candidate (yesterday)",
        "FICTIONAL ILLUSTRATION. No candidate was published because required filing data "
        "was unavailable for the top-ranked name. Missing critical evidence withholds a pick.",
        payload={"demo": True, "reason": "required_evidence_unavailable"},
        now=now - timedelta(days=1),
    )
    brief_id = journal.append_entry(
        conn, "research_brief", "DEMO — Research candidate: DEMOX (FICTIONAL)",
        "FICTIONAL ILLUSTRATION of the brief format. Not a real company. Not advice.",
        run_id=run_id, ticker="DEMOX", now=now - timedelta(minutes=55),
        payload={
            "demo": True,
            "ticker": "DEMOX",
            "company": "Example Harbor Logistics Inc. (FICTIONAL)",
            "exchange": "Fictional exchange (demo)",
            "why_today": "A new contract announcement plus a recent quarterly filing make this worth a closer look.",
            "catalyst": "Port-services contract (initial 12-month term). Horizon: unclear; financial terms not disclosed.",
            "metric_ids": ["demo-m-price-x", "demo-m-mcap-x", "demo-m-rev-x", "demo-m-shares-x"],
            "claims": [
                {"text": "Quarterly revenue was $48.2 million (FY2026 Q2).", "label": "CONFIRMED",
                 "evidence_id": "demo-ev-10q", "metric_id": "demo-m-rev-x"},
                {"text": "The company signed a port-services contract with an initial 12-month term.",
                 "label": "CONFIRMED", "evidence_id": "demo-ev-pr"},
                {"text": "Regional port volumes declined last quarter.", "label": "THIRD_PARTY",
                 "evidence_id": "demo-ev-news2"},
                {"text": "The contract may matter more to a company of this size than to a larger peer; "
                         "its dollar value is unknown, so impact cannot be estimated.",
                 "label": "AI_ESTIMATE", "evidence_id": None},
            ],
            "bull_case": ["New contract adds a potential revenue source.", "Recent filing available for review."],
            "bear_case": ["Contract value undisclosed; could be immaterial.",
                          "Third-party commentary points to softening port volumes.",
                          "News coverage just repeats the press release: not independent confirmation."],
            "risks": {"financial": "Not assessed in demo.", "dilution": "Share count change not assessed in demo.",
                      "liquidity": "Not assessed in demo.", "event": "Renewal of 12-month contract is uncertain."},
            "missing_information": ["Reddit discussion: Data unavailable (no approved API access).",
                                    "Contract financial terms not disclosed."],
            "would_invalidate": ["Contract terminated or not renewed.", "Next quarter revenue declines."],
            "research_checklist": ["Read the 10-Q risk factors.", "Look for share issuance / dilution in the filing.",
                                   "Check average daily trading volume on Finviz.", "Compare with peers."],
            "excluded_after_cutoff": ["demo-ev-late"],
        },
        dedupe_key=f"demo-brief-{run_id}",
    )
    journal.add_correction(
        conn, brief_id, "DEMO — Correction to DEMOX brief",
        "FICTIONAL ILLUSTRATION. An earlier draft called the contract 'multi-year'. The press release "
        "says 'initial 12-month term with renewal options'. The original entry is kept unchanged.",
        now=now - timedelta(minutes=50),
    )
    journal.append_entry(conn, "system", "DEMO database created",
                         "Loaded fictional fixtures for the offline demo.", now=now - timedelta(minutes=45))

"""The screening run: gather free data, screen the market, research a shortlist.

    python -m omkaka screen

Every step records what it asked for, when, and whether it worked. Nothing
here calls a paid service or an AI model.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from . import journal, store
from .config import Settings, get_secret
from .db import transaction
from .market_calendar import previous_trading_day, trading_days_before
from .models import SourceType, Status, ValueKind
from .screening import catalyst_filings, prefilter, shortlist_flags
from .sources import finnhub as fh
from .sources import massive as ms
from .sources import reddit as rd
from .sources import sec
from .sources.http import FetchResult, HttpClient
from .timeutil import NEW_YORK, to_utc_iso, utc_now


@dataclass
class Clients:
    http: HttpClient
    sec: sec.SecClient
    massive: ms.MassiveClient
    finnhub: fh.FinnhubClient
    reddit: rd.RedditClient


def build_clients(conn, settings: Settings, transport=None, sleep=time.sleep,
                  now: Callable[[], datetime] = utc_now, demo_credentials: bool = False) -> Clients:
    def secret(name):
        s = get_secret(name)
        return s.reveal() if s else None

    if demo_credentials:  # offline demo: fixture transport, fake keys, Reddit left unconfigured
        creds = {"SEC_USER_AGENT": "OmKakaFinance DEMO demo@example.com", "MARKET_DATA_API_KEY": "demo-key",
                 "NEWS_API_KEY": "demo-key"}
    else:
        creds = {n: secret(n) for n in ("SEC_USER_AGENT", "MARKET_DATA_API_KEY", "NEWS_API_KEY",
                                        "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")}
    http = HttpClient(conn, settings, transport=transport, sleep=sleep, now=now,
                      secrets=[v for k, v in creds.items() if v and k != "SEC_USER_AGENT"])
    clients = Clients(
        http=http,
        sec=sec.SecClient(http, creds.get("SEC_USER_AGENT")),
        massive=ms.MassiveClient(http, creds.get("MARKET_DATA_API_KEY")),
        finnhub=fh.FinnhubClient(http, creds.get("NEWS_API_KEY")),
        reddit=rd.RedditClient(http, creds.get("REDDIT_CLIENT_ID"), creds.get("REDDIT_CLIENT_SECRET"),
                               creds.get("REDDIT_USER_AGENT")),
    )
    if demo_credentials:
        # Demo links must never point at real sites (a fictional CIK could match a real company).
        clients.sec.www = "https://example.com/demo-sec"
        clients.sec.data = "https://example.com/demo-sec-data"
        clients.massive.base = "https://example.com/demo-market"
        clients.finnhub.base = "https://example.com/demo-news"
        clients.reddit.api_base = "https://example.com/demo-reddit"
        clients.reddit.auth_url = "https://example.com/demo-reddit/token"
    return clients


def extra_closed(settings: Settings) -> tuple[str, ...]:
    return tuple(settings.raw.get("schedule", {}).get("extra_closed_dates", []))


def recent_weekdays(before: date, count: int) -> list[date]:
    """`count` weekdays strictly before `before`, oldest first."""
    out, d = [], before
    while len(out) < count:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return list(reversed(out))


class ScreenRun:
    def __init__(self, conn, settings: Settings, clients: Clients, log: Callable[[str], None] = print):
        self.conn, self.settings, self.c, self.log = conn, settings, clients, log
        self.cfg = settings.raw["screening"]
        self.summary: dict = {"source_problems": []}

    # ------------------------------------------------------------ recording helpers
    def check(self, source: str, result: FetchResult, ticker=None, query=None, count=None) -> FetchResult:
        store.record_source_check(self.conn, self.run_id, source, result.provider, result.status,
                                  reason=result.reason, query=query or result.url, ticker=ticker,
                                  result_count=count if result.status is not Status.NO_RESULTS else 0,
                                  fetched_at=result.fetched_at)
        if result.status is Status.NOT_CONFIGURED:
            note = f"{source}: NOT_CONFIGURED - {result.reason}"
            if note not in self.summary["source_problems"]:
                self.summary["source_problems"].append(note)
        elif result.status not in (Status.OK, Status.NO_RESULTS, Status.PARTIAL):
            self.summary["source_problems"].append(f"{source}{' ' + ticker if ticker else ''}: "
                                                   f"{result.status.value} - {result.reason}")
        return result

    # ------------------------------------------------------------ main
    def run(self, run_kind: str = "manual", run_id: str | None = None) -> dict:
        now = self.c.http.now()
        cutoff = now + timedelta(minutes=int(self.settings.raw["collection"]["window_minutes"]))
        snapshot = {"screening": self.cfg, "providers": {k: {kk: vv for kk, vv in v.items() if kk != "subreddits"}
                                                         for k, v in self.settings.raw["providers"].items()}}
        self.run_id = store.start_run(self.conn, run_kind, cutoff, settings_snapshot=snapshot, now=now, run_id=run_id)
        self.summary["run_id"] = self.run_id
        try:
            self._run(now)
        except Exception as exc:  # record honestly, then re-raise
            store.finish_run(self.conn, self.run_id, "failed", reason=f"{type(exc).__name__}: {exc}",
                             now=self.c.http.now())
            raise
        return self.summary

    def _fail(self, reason: str) -> None:
        self.log(f"STOPPED: {reason}")
        self.summary["status"] = "failed"
        self.summary["reason"] = reason
        store.finish_run(self.conn, self.run_id, "failed", reason=reason, now=self.c.http.now())

    def _run(self, now: datetime) -> None:
        today = now.astimezone(NEW_YORK).date()

        # 1. Universe -----------------------------------------------------
        self.log("1/6 Listed companies (SEC)...")
        r = self.check("SEC company list", self.c.sec.company_tickers())
        if not r.ok:
            return self._fail(f"Company list unavailable: {r.reason}")
        members = {m["ticker"]: m for m in sec.parse_company_tickers(r.data)}
        self.summary["universe"] = len(members)

        # 2. Prices -------------------------------------------------------
        self.log("2/6 End-of-day prices (Massive, free plan: ~12 s between calls)...")
        lookback = int(self.cfg["lookback_trading_days"])
        have = {row[0] for row in self.conn.execute("SELECT DISTINCT trade_date FROM market_bars WHERE provider='massive'")}
        closed = extra_closed(self.settings)
        keep = set(members) | {self.settings.raw.get("benchmark", {}).get("ticker", "SPY")}
        for day in trading_days_before(today, lookback + 2, closed):
            if day.isoformat() in have:
                continue
            res = self.c.massive.grouped_daily(day)
            rows = ms.parse_grouped_daily(res.data) if res.ok else []
            if res.ok and not rows:
                res.status = Status.NO_RESULTS  # no trading reported (unscheduled closure?) - not a failure
            self.check("Market data (end of day)", res, query=f"grouped daily {day}", count=len(rows))
            rows = [r for r in rows if r["ticker"] in keep]
            if rows:
                self._store_bars(day, rows, res)
            if res.status is Status.RATE_LIMITED:
                break
        dates = [row[0] for row in self.conn.execute(
            "SELECT DISTINCT trade_date FROM market_bars WHERE provider='massive' AND trade_date < ? "
            "ORDER BY trade_date DESC LIMIT ?", (today.isoformat(), lookback))]
        if not dates:
            return self._fail("No price data available (see source health).")
        latest_trade_date = dates[0]
        expected = previous_trading_day(today, closed).isoformat()
        if latest_trade_date < expected:
            return self._fail(f"Latest price data is stale: newest close is {latest_trade_date}, but the market "
                              f"traded on {expected}. Screening stopped rather than rank companies on old prices.")
        bars: dict[str, list[dict]] = {}
        for row in self.conn.execute(
                f"SELECT ticker, trade_date, close, volume FROM market_bars WHERE provider='massive' "
                f"AND trade_date IN ({','.join('?' * len(dates))}) ORDER BY trade_date", dates):
            bars.setdefault(row["ticker"], []).append(dict(row))
        self.summary["latest_trade_date"] = latest_trade_date

        # 3. Shares outstanding --------------------------------------------
        self.log("3/6 Shares outstanding (SEC)...")
        frames = []
        y, q = today.year, (today.month - 1) // 3 + 1
        for _ in range(3):
            res = self.check("SEC shares outstanding", self.c.sec.shares_frame(y, q), query=f"CY{y}Q{q}I")
            if res.ok:
                frames.append(sec.parse_shares_frame(res.data))
            y, q = (y, q - 1) if q > 1 else (y - 1, 4)
        shares = sec.merge_share_frames(frames)

        # 4. Recent filings across the market (catalysts) --------------------
        self.log("4/6 Recent filings index (SEC)...")
        eightk: dict[str, int] | None = {}
        for day in trading_days_before(today, int(self.cfg["catalyst_lookback_days"]), closed):
            res = self.check("SEC daily filings index", self.c.sec.daily_index(day), query=f"form index {day}")
            if res.ok:
                for f in sec.parse_daily_index(res.data):
                    if f["form"] in ("8-K", "8-K/A"):
                        eightk[f["cik"]] = eightk.get(f["cik"], 0) + 1
            elif res.status is not Status.NO_RESULTS:
                eightk = None  # counts would be incomplete -> unknown, not zero
                break

        # 5. Prefilter -----------------------------------------------------
        self.log("5/6 Screening every listed company...")
        evals = []
        for t, m in members.items():
            n8k = None if eightk is None else eightk.get(m["cik"], 0)
            evals.append((m, prefilter(m, bars.get(t, []), shares.get(m["cik"]), n8k, self.cfg, latest_trade_date)))
        passed = sorted([e for e in evals if e[1].outcome == "passed"], key=lambda e: -e[1].score)
        for rank, (_, ev) in enumerate(passed, 1):
            ev.rank = rank
        self._store_results("prefilter", evals, now)
        counts = {}
        for _, ev in evals:
            counts[ev.outcome] = counts.get(ev.outcome, 0) + 1
        self.summary["prefilter"] = counts

        # 6. Shortlist research -------------------------------------------
        self.log("6/6 Researching the shortlist (filings, fundamentals, news, Reddit)...")
        shortlist = passed[: int(self.cfg["shortlist_size"])]
        chosen = {m["ticker"] for m, _ in shortlist}
        by_ticker = {m["ticker"]: (m, ev) for m, ev in evals}
        for t in store.current_watchlist(self.conn):
            if t not in chosen:
                if t in by_ticker:
                    m, ev = by_ticker[t]
                    ev.outcome = "watchlist_only"
                    shortlist.append((m, ev))
                else:
                    self.summary["source_problems"].append(f"Watchlist {t}: not in SEC listed-company file")
        final = []
        for m, ev in shortlist:
            final.append((m, self._research(m, ev, today)))
        self._store_results("shortlist", final, now)
        self.summary["shortlist"] = [m["ticker"] for m, ev in final]
        self.summary["status"] = "completed"

        journal.append_entry(
            self.conn, "system", f"Screening run {self.run_id}",
            f"Screened {len(members)} listed companies using prices through {latest_trade_date}. "
            f"Outcomes: {counts}. Shortlist: {', '.join(self.summary['shortlist']) or 'none'}. "
            f"Source problems: {len(self.summary['source_problems'])}.",
            payload={"summary": self.summary}, run_id=self.run_id, dedupe_key=f"screen-{self.run_id}",
            now=self.c.http.now(),
        )
        store.finish_run(self.conn, self.run_id, "completed", now=self.c.http.now())

    # ------------------------------------------------------------ storage
    def _store_bars(self, day: date, rows: list[dict], res: FetchResult) -> None:
        eff = to_utc_iso(ms.market_close(day))
        with transaction(self.conn):
            self.conn.executemany(
                """INSERT OR IGNORE INTO market_bars(ticker, trade_date, open, high, low, close, volume, vwap,
                       provider, source_url, effective_at, fetched_at, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'massive', ?, ?, ?, ?)""",
                [(r["ticker"], day.isoformat(), r["open"], r["high"], r["low"], r["close"], r["volume"], r["vwap"],
                  res.url, eff, to_utc_iso(res.fetched_at), self.run_id) for r in rows],
            )

    def _store_results(self, stage: str, evals, now: datetime) -> None:
        rows = []
        for m, ev in evals:
            keep_detail = ev.outcome != "failed_threshold" or stage == "shortlist"
            rows.append((self.run_id, stage, ev.ticker, m.get("cik"), m.get("name"), m.get("exchange"), ev.outcome,
                         ev.score, getattr(ev, "rank", None),
                         json.dumps(ev.components if keep_detail else {}, default=str),
                         json.dumps(ev.flags), json.dumps(ev.reasons),
                         json.dumps(ev.inputs if keep_detail else {}, default=str),
                         to_utc_iso(self.c.http.now())))
        with transaction(self.conn):
            self.conn.executemany(
                """INSERT INTO screening_results(run_id, stage, ticker, cik, company_name, exchange, outcome, score,
                       rank, components_json, flags_json, reasons_json, inputs_json, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)

    # ------------------------------------------------------------ one company
    def _research(self, m: dict, ev, today: date):
        t, cik = m["ticker"], m["cik"]
        rid = self.run_id
        since = today - timedelta(days=int(self.cfg["filings_lookback_days"]))

        # Filings (primary source) — critical evidence.
        res = self.c.sec.submissions(cik)
        filings = sec.parse_submissions(res.data, since) if res.ok else None
        self.check("SEC filings", res, ticker=t, count=len(filings) if filings is not None else None)
        accepted = {}
        for f in filings or []:
            accepted[f["accession"]] = f["accepted_at"]
            store.record_evidence(
                self.conn, rid, SourceType.FILING, "sec", f["accession"], fetched_at=res.fetched_at, ticker=t,
                company_name=m["name"], cik=cik, url=self.c.sec.filing_url(cik, f["accession"], f["primary_doc"]),
                title=f"{f['form']} filed {f['filing_date']}" + (f" (items {f['items']})" if f["items"] else ""),
                excerpt=None, storage_note="Link only; read the filing at the source.",
                published_at=f["accepted_at"], evidence_id=f"{rid}:sec:{f['accession']}", doc_type=f["form"],
                dedupe_group=f["accession"])

        # Fundamentals (primary source, reported numbers).
        res = self.c.sec.companyfacts(cik)
        self.check("SEC financial data", res, ticker=t)
        fundamentals = {}
        facts = sec.extract_fundamentals(res.data, today) if res.ok else [
            {"metric": k, "period_label": "latest", "period_end": None, "value": None, "unit": u,
             "status": res.status if res.status is not Status.NO_RESULTS else Status.NO_COVERAGE,
             "reason": res.reason or "No SEC financial data for this company."}
            for k, u in (("revenue_quarter", "USD"), ("cash", "USD"), ("shares_outstanding", "shares"))]
        for f in facts:
            if f["status"] is Status.OK:
                fundamentals[f["metric"]] = f["value"]
            acc = f.get("accn")
            store.record_metric(
                self.conn, rid, t, f["metric"], f["period_label"], f["unit"], ValueKind.REPORTED, "sec",
                fetched_at=res.fetched_at, value=f["value"], status=f["status"], reason=f["reason"], cik=cik,
                period_end=f.get("period_end"), source_identifier=acc,
                source_url=self.c.sec.filing_url(cik, acc, None) if acc else res.url,
                published_at=accepted.get(acc))
        self._calculated(t, cik, fundamentals, ev.inputs, res.fetched_at)

        # News (third party).
        n_since = today - timedelta(days=int(self.cfg["news_lookback_days"]))
        res = self.c.finnhub.company_news(t, n_since, today)
        items = fh.parse_company_news(res.data) if res.ok else []
        self.check("News", res, ticker=t, count=len(items))
        for it in items:
            store.record_evidence(
                self.conn, rid, SourceType.NEWS, "finnhub", it["id"], fetched_at=res.fetched_at, ticker=t,
                company_name=m["name"], url=it["url"], title=it["headline"], excerpt=it["summary"],
                storage_note=f"Headline and provider summary only (source: {it['source']}).",
                published_at=it["published_at"], evidence_id=f"{rid}:news:{it['id']}", doc_type="news",
                dedupe_group=it["group"])

        # Reddit (noisy third-party discussion; approved API only).
        res = self.c.reddit.search(t, None)
        posts = rd.parse_search(res.data) if res.status in (Status.OK, Status.PARTIAL) else []
        self.check("Reddit", res, ticker=t, count=len(posts) if posts or res.status is Status.NO_RESULTS else None)
        reddit_summary = rd.summarize_discussion(posts) if res.status in (Status.PARTIAL, Status.NO_RESULTS) else None
        for p in posts:
            store.record_evidence(
                self.conn, rid, SourceType.REDDIT, "reddit", p["id"], fetched_at=res.fetched_at, ticker=t,
                url=p["permalink"], title=f"Reddit post in r/{p['subreddit']} ({p['num_comments']} comments)",
                excerpt=None, storage_note="Link and counts only; post text is not kept permanently (Reddit terms).",
                published_at=p["created_at"], evidence_id=f"{rid}:reddit:{p['id']}", doc_type="reddit_post",
                dedupe_group=p["group"])

        cat_since = (today - timedelta(days=int(self.cfg["catalyst_lookback_days"]))).isoformat()
        ev.inputs = {**ev.inputs, "catalyst_8k": [
            {"form": f["form"], "filed": f["filing_date"], "items": f["item_names"], "accession": f["accession"]}
            for f in catalyst_filings(filings, cat_since)],
            "news_items": len(items), "independent_news_stories": len({i["group"] for i in items}),
            "reddit": reddit_summary}
        ev.flags = shortlist_flags(ev.inputs, filings, fundamentals, reddit_summary, self.cfg)
        if filings is None and ev.outcome == "passed":
            ev.outcome = "insufficient_data"
            ev.reasons = ev.reasons + ["SEC filings unavailable: critical evidence missing"]
        return ev

    def _calculated(self, t, cik, fund: dict, inputs: dict, fetched_at: datetime) -> None:
        """Numbers computed by this app, each with its inputs and formula stored."""
        rid = self.run_id
        if inputs.get("last_close") is not None:
            eff = ms.market_close(date.fromisoformat(inputs["last_date"]))
            store.record_metric(self.conn, rid, t, "close_price", f"close {inputs['last_date']}", "USD/share",
                                ValueKind.MARKET_DATA, "massive", fetched_at=fetched_at, value=inputs["last_close"],
                                effective_at=eff, source_identifier=f"grouped-daily:{inputs['last_date']}")
            store.record_metric(self.conn, rid, t, "market_cap", f"as of {inputs['last_date']}", "USD",
                                ValueKind.CALCULATED, "OmKakaFinance calculation", fetched_at=fetched_at,
                                value=round(inputs["market_cap"], 2), effective_at=eff, cik=cik,
                                calculation={"formula": "close_price * shares_outstanding",
                                             "inputs": {"close_price": inputs["last_close"],
                                                        "close_date": inputs["last_date"],
                                                        "shares_outstanding": inputs["shares_outstanding"],
                                                        "shares_as_of": inputs["shares_as_of"],
                                                        "shares_accession": inputs.get("shares_accn")},
                                             "note": "Price date and share-count date differ."})
            store.record_metric(self.conn, rid, t, "avg_daily_dollar_volume", f"{inputs['days']} trading days to "
                                f"{inputs['last_date']}", "USD", ValueKind.CALCULATED, "OmKakaFinance calculation",
                                fetched_at=fetched_at, value=round(inputs["avg_dollar_volume"], 2), effective_at=eff,
                                calculation={"formula": "average of close * volume per day",
                                             "days": inputs["days"], "provider": "massive"})
        pairs = (("revenue_growth_yoy", "revenue_quarter", "revenue_quarter_prior_year"),
                 ("share_count_change_yoy", "shares_outstanding", "shares_outstanding_year_ago"))
        for metric, new, old in pairs:
            if fund.get(new) is not None and fund.get(old):
                store.record_metric(self.conn, rid, t, metric, "about 1 year", "ratio", ValueKind.CALCULATED,
                                    "OmKakaFinance calculation", fetched_at=fetched_at,
                                    value=round(fund[new] / fund[old] - 1, 6), cik=cik,
                                    calculation={"formula": f"{new} / {old} - 1",
                                                 "inputs": {new: fund[new], old: fund[old]}})
            else:
                store.record_metric(self.conn, rid, t, metric, "about 1 year", "ratio", ValueKind.CALCULATED,
                                    "OmKakaFinance calculation", fetched_at=fetched_at, value=None,
                                    status=Status.NO_COVERAGE, cik=cik,
                                    reason=f"Cannot calculate: {new} or {old} unavailable.")


def run_screen(conn, settings: Settings, clients: Clients, run_kind="manual", run_id=None, log=print) -> dict:
    return ScreenRun(conn, settings, clients, log).run(run_kind=run_kind, run_id=run_id)

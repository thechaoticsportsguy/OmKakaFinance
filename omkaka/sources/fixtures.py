"""OFFLINE fixture transport: answers requests like the real services would,
using fictional companies from fixtures/demo/phase2_market.json.

Used by the offline demo and the automated tests. It never touches the network.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..config import PROJECT_ROOT
from .http import TransportResponse

SPEC_FILE = PROJECT_ROOT / "fixtures" / "demo" / "phase2_market.json"


def _weekdays_before(day: date, count: int) -> list[date]:
    out, d = [], day
    while len(out) < count:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return list(reversed(out))


def _quarter_ends_before(day: date, count: int) -> list[date]:
    ends, y, q = [], day.year, (day.month - 1) // 3 + 1
    while len(ends) < count:
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        end = date(y, q * 3, 30 if q in (2, 3) else 31)
        if end < day:
            ends.append(end)
    return ends


class FixtureTransport:
    def __init__(self, today: date, spec_file: Path = SPEC_FILE):
        self.today = today
        self.spec = json.loads(Path(spec_file).read_text(encoding="utf-8"))
        self.companies = self.spec["companies"]
        self.days = _weekdays_before(today, 30)
        self.holiday = self.days[-self.spec["holiday_weekdays_ago"]]
        self.calls: list[str] = []

    # ---------------------------------------------------------------- dispatch
    def __call__(self, method, url, params=None, headers=None, data=None, auth=None, timeout=30):
        self.calls.append(url)
        routes = [
            (r"/files/company_tickers_exchange\.json$", self._tickers),
            (r"/frames/dei/EntityCommonStockSharesOutstanding/shares/CY(\d{4})Q(\d)I\.json$", self._frames),
            (r"/daily-index/\d{4}/QTR\d/form\.(\d{8})\.idx$", self._index),
            (r"/submissions/CIK(\d{10})\.json$", self._submissions),
            (r"/companyfacts/CIK(\d{10})\.json$", self._companyfacts),
            (r"/v2/aggs/grouped/locale/us/market/stocks/(\d{4}-\d{2}-\d{2})$", self._grouped),
            (r"/company-news$", lambda: self._news(params or {})),
        ]
        for pattern, handler in routes:
            m = re.search(pattern, url)
            if m:
                return handler(*m.groups())
        return TransportResponse(404, "not found (fixture)")

    @staticmethod
    def _json(obj, code=200):
        return TransportResponse(code, json.dumps(obj))

    def _by_cik(self, cik10: str):
        return next((c for c in self.companies if c["cik"] == int(cik10)), None)

    # ---------------------------------------------------------------- SEC
    def _tickers(self):
        return self._json({"fields": ["cik", "name", "ticker", "exchange"],
                           "data": [[c["cik"], c["name"], c["ticker"], c["exchange"]] for c in self.companies]})

    def _frames(self, year, quarter):
        prev_end = _quarter_ends_before(self.today, 1)[0]
        if (int(year), int(quarter)) != (prev_end.year, (prev_end.month - 1) // 3 + 1):
            return TransportResponse(404, "frame not found (fixture)")
        cover = (prev_end - timedelta(days=5)).isoformat()
        return self._json({"taxonomy": "dei", "tag": "EntityCommonStockSharesOutstanding", "ccp": f"CY{year}Q{quarter}I",
                           "data": [{"accn": f"000{c['cik']}-26-000001", "cik": c["cik"], "entityName": c["name"],
                                     "end": cover, "val": c["shares"]} for c in self.companies if c.get("shares")]})

    def _index(self, ymd):
        day = datetime.strptime(ymd, "%Y%m%d").date()
        if day == self.holiday or day.weekday() >= 5:
            return TransportResponse(404, "no index (fixture)")
        lines = ["Description:           Daily Index of EDGAR Dissemination Feed by Form Type (FICTIONAL)", "",
                 "Form Type   Company Name                                                  CIK         Date Filed  File Name",
                 "-" * 130]
        if day == self.days[-1]:
            for c in self.companies:
                for _ in range(c.get("eightk_count", 0)):
                    lines.append(f"{'8-K':<12}{c['name'][:60]:<62}{c['cik']:<12}{ymd:<12}edgar/data/{c['cik']}/demo.txt")
        return TransportResponse(200, "\n".join(lines))

    def _submissions(self, cik10):
        c = self._by_cik(cik10)
        if c is None:
            return TransportResponse(404, "")
        if c.get("submissions_http_status"):
            return TransportResponse(c["submissions_http_status"], "Service Unavailable (fixture)")
        recent = {k: [] for k in ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "items",
                                  "primaryDocument", "primaryDocDescription")}
        for i, f in enumerate(c.get("filings", [])):
            filed = self.today - timedelta(days=f["days_ago"])
            recent["accessionNumber"].append(f"000{c['cik']}-26-{i + 10:06d}")
            recent["filingDate"].append(filed.isoformat())
            recent["acceptanceDateTime"].append(f"{filed.isoformat()}T20:15:00.000Z")
            recent["form"].append(f["form"])
            recent["items"].append(f["items"])
            recent["primaryDocument"].append("demo-filing.htm")
            recent["primaryDocDescription"].append(f"{f['form']} (FICTIONAL)")
        return self._json({"cik": c["cik"], "name": c["name"], "filings": {"recent": recent}})

    def _companyfacts(self, cik10):
        c = self._by_cik(cik10)
        if c is None or c.get("companyfacts_http_status"):
            return TransportResponse((c or {}).get("companyfacts_http_status", 404), "")
        q_end = _quarter_ends_before(self.today, 1)[0]
        filed = (self.today - timedelta(days=48)).isoformat()
        accn = f"000{c['cik']}-26-000011"

        def q(end: date, val):
            start = date(end.year, end.month - 2, 1)  # calendar quarter (~91 days)
            return {"start": start.isoformat(), "end": end.isoformat(), "val": val, "accn": accn, "form": "10-Q", "filed": filed}

        gaap, dei = {}, {}
        if c.get("revenue"):
            prior_end = date(q_end.year - 1, q_end.month, q_end.day)
            gaap["Revenues"] = {"units": {"USD": [q(prior_end, c["revenue"][1]), q(q_end, c["revenue"][0])]}}
        if c.get("net_income") is not None:
            gaap["NetIncomeLoss"] = {"units": {"USD": [q(q_end, c["net_income"])]}}
        if c.get("cash") is not None:
            gaap["CashAndCashEquivalentsAtCarryingValue"] = {"units": {"USD": [
                {"end": q_end.isoformat(), "val": c["cash"], "accn": accn, "form": "10-Q", "filed": filed}]}}
        if c.get("ocf") is not None:
            gaap["NetCashProvidedByUsedInOperatingActivities"] = {"units": {"USD": [
                {"start": date(q_end.year, 1, 1).isoformat(), "end": q_end.isoformat(), "val": c["ocf"],
                 "accn": accn, "form": "10-Q", "filed": filed}]}}
        if c.get("shares"):
            now_end = self.today - timedelta(days=45)
            dei["EntityCommonStockSharesOutstanding"] = {"units": {"shares": [
                {"end": (now_end - timedelta(days=365)).isoformat(), "val": c["shares_year_ago"], "accn": accn,
                 "form": "10-Q", "filed": filed},
                {"end": now_end.isoformat(), "val": c["shares"], "accn": accn, "form": "10-Q", "filed": filed}]}}
        return self._json({"cik": c["cik"], "entityName": c["name"], "facts": {"us-gaap": gaap, "dei": dei}})

    # ---------------------------------------------------------------- Massive
    def _grouped(self, iso):
        day = date.fromisoformat(iso)
        if day == self.holiday:
            return self._json({"status": "OK", "queryCount": 0, "resultsCount": 0, "adjusted": False})
        trading = [d for d in self.days if d != self.holiday]
        if day not in trading:
            return self._json({"status": "OK", "resultsCount": 0})
        i, n = trading.index(day), len(trading)
        results = []
        for c in self.companies:
            if c.get("stops_trading_weekdays_ago") and day > self.days[-c["stops_trading_weekdays_ago"]]:
                continue
            close = round(c["price"] * (1 + c["trend"] * (i / (n - 1) - 1)), 4)
            vol = c["volume"] * (c["last_day_volume_x"] if i == n - 1 else 1)
            results.append({"T": c["ticker"], "o": close, "h": close, "l": close, "c": close, "v": vol, "vw": close,
                            "t": int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)})
        return self._json({"status": "OK", "resultsCount": len(results), "adjusted": False, "results": results})

    # ---------------------------------------------------------------- Finnhub
    def _news(self, params):
        c = next((x for x in self.companies if x["ticker"] == params.get("symbol")), None)
        if c and c.get("news_http_status"):
            return TransportResponse(c["news_http_status"], '{"error":"API limit reached (fixture)"}')
        now = datetime.combine(self.today, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=10)
        items = []
        for k, n in enumerate((c or {}).get("news", [])):
            items.append({"id": c["cik"] * 100 + k, "headline": n["headline"], "source": n["source"],
                          "summary": "FICTIONAL demo summary. " + n["headline"],
                          "url": f"https://example.com/demo/{c['ticker'].lower()}-news-{k}",
                          "datetime": int((now - timedelta(hours=n["hours_ago"])).timestamp())})
        return self._json(items)

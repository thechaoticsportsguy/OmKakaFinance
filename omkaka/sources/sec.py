"""SEC EDGAR (free, no key). Requires SEC_USER_AGENT = your name + email.

Endpoints used:
  company_tickers_exchange.json   every listed company: CIK, name, ticker, exchange
  XBRL frames (dei shares)        shares outstanding for all companies, one call per quarter
  daily form index                every filing made on a given day (catalyst detection)
  submissions/CIK##########.json  one company's recent filings with exact acceptance times
  companyfacts/CIK##########.json one company's reported financial numbers
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from ..models import Status
from .http import FetchResult, HttpClient

PROVIDER = "sec"


def cik10(cik) -> str:
    return str(int(cik)).zfill(10)


def quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


class SecClient:
    def __init__(self, http: HttpClient, user_agent: str | None):
        self.http = http
        cfg = http.settings.raw["providers"]["sec"]
        self.www = cfg["www_base"].rstrip("/")
        self.data = cfg["data_base"].rstrip("/")
        self.user_agent = user_agent

    def _get(self, url, ttl_hours, parse="json", not_found=Status.NO_RESULTS) -> FetchResult:
        if not self.user_agent:
            return FetchResult(PROVIDER, url, Status.NOT_CONFIGURED,
                               "SEC_USER_AGENT is not set in .env (SEC requires your name and email).",
                               None, self.http.now())
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        return self.http.get(PROVIDER, url, headers=headers, ttl_hours=ttl_hours, parse=parse, not_found=not_found)

    def company_tickers(self) -> FetchResult:
        return self._get(f"{self.www}/files/company_tickers_exchange.json", ttl_hours=20)

    def shares_frame(self, year: int, quarter: int) -> FetchResult:
        url = f"{self.data}/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{year}Q{quarter}I.json"
        return self._get(url, ttl_hours=20)

    def daily_index(self, day: date) -> FetchResult:
        url = (f"{self.www}/Archives/edgar/daily-index/{day.year}/QTR{quarter_of(day)}/"
               f"form.{day.strftime('%Y%m%d')}.idx")
        # No file for weekends/holidays -> 404 -> NO_RESULTS (nothing filed), not a failure.
        return self._get(url, ttl_hours=24 * 30, parse="text")

    def submissions(self, cik) -> FetchResult:
        return self._get(f"{self.data}/submissions/CIK{cik10(cik)}.json", ttl_hours=6,
                         not_found=Status.NO_COVERAGE)

    def companyfacts(self, cik) -> FetchResult:
        return self._get(f"{self.data}/api/xbrl/companyfacts/CIK{cik10(cik)}.json", ttl_hours=20,
                         not_found=Status.NO_COVERAGE)

    def filing_url(self, cik, accession: str, primary_doc: str | None) -> str:
        acc = accession.replace("-", "")
        base = f"{self.www}/Archives/edgar/data/{int(cik)}/{acc}"
        return f"{base}/{primary_doc}" if primary_doc else f"{base}/"


# ------------------------------------------------------------------ parsers (pure)

def parse_company_tickers(data: dict) -> list[dict]:
    fields = data["fields"]
    idx = {f: fields.index(f) for f in ("cik", "name", "ticker", "exchange")}
    out = []
    for row in data["data"]:
        ticker = row[idx["ticker"]]
        if not ticker:
            continue
        out.append({"cik": cik10(row[idx["cik"]]), "name": row[idx["name"]],
                    "ticker": str(ticker).upper(), "exchange": row[idx["exchange"]]})
    return out


def parse_shares_frame(data: dict) -> dict[str, dict]:
    """{cik10: {"val", "end", "accn"}} from one frames response."""
    out = {}
    for pt in data.get("data", []):
        out[cik10(pt["cik"])] = {"val": float(pt["val"]), "end": pt["end"], "accn": pt.get("accn")}
    return out


def merge_share_frames(frames: list[dict[str, dict]]) -> dict[str, dict]:
    """Keep the most recent share count per company across several quarters."""
    merged: dict[str, dict] = {}
    for frame in frames:
        for cik, pt in frame.items():
            if cik not in merged or pt["end"] > merged[cik]["end"]:
                merged[cik] = pt
    return merged


_IDX_LINE = re.compile(r"^(?P<form>\S.*?)\s{2,}(?P<company>.+?)\s{2,}(?P<cik>\d+)\s+(?P<date>\d{8})\s+(?P<file>\S+)\s*$")


def parse_daily_index(text: str) -> list[dict]:
    rows, started = [], False
    for line in text.splitlines():
        if not started:
            started = line.startswith("-----")
            continue
        m = _IDX_LINE.match(line)
        if m:
            rows.append({"form": m["form"].strip(), "company": m["company"].strip(),
                         "cik": cik10(m["cik"]), "date": m["date"], "file": m["file"]})
    return rows


def parse_submissions(data: dict, since: date) -> list[dict]:
    recent = data.get("filings", {}).get("recent", {})
    n = len(recent.get("accessionNumber", []))
    col = lambda k, i: (recent.get(k) or [None] * n)[i]
    out = []
    for i in range(n):
        filed = col("filingDate", i)
        if not filed or date.fromisoformat(filed) < since:
            continue
        acc_time = col("acceptanceDateTime", i)
        accepted = None
        if acc_time:
            accepted = datetime.fromisoformat(acc_time.replace("Z", "+00:00"))
            if accepted.tzinfo is None:
                accepted = accepted.replace(tzinfo=timezone.utc)
        out.append({"accession": col("accessionNumber", i), "form": col("form", i), "filing_date": filed,
                    "accepted_at": accepted, "items": col("items", i) or "",
                    "primary_doc": col("primaryDocument", i), "description": col("primaryDocDescription", i)})
    return out


REVENUE_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax")


def _duration_days(f: dict) -> int | None:
    if not f.get("start"):
        return None
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days


def _dedupe_latest_filed(facts: list[dict]) -> list[dict]:
    """Same period reported in several filings: keep the most recently filed value."""
    best: dict[tuple, dict] = {}
    for f in facts:
        k = (f.get("start"), f["end"])
        if k not in best or f.get("filed", "") > best[k].get("filed", ""):
            best[k] = f
    return sorted(best.values(), key=lambda f: f["end"])


def _facts(cf: dict, taxonomy: str, tags, unit: str) -> tuple[str | None, list[dict]]:
    for tag in tags:
        units = cf.get("facts", {}).get(taxonomy, {}).get(tag, {}).get("units", {})
        if units.get(unit):
            return tag, units[unit]
    return None, []


def extract_fundamentals(cf: dict, as_of: date) -> list[dict]:
    """Turn companyfacts JSON into labeled metric records.

    Each record: metric, period_label, period_end, value (or None), unit, tag,
    accn, filed, and status/reason when the data is missing. Periods are
    labeled by their actual dates (fiscal labels in companyfacts refer to the
    filing, not always to the number).
    """
    out: list[dict] = []

    def missing(metric, unit, reason):
        out.append({"metric": metric, "period_label": "latest", "period_end": None, "value": None,
                    "unit": unit, "status": Status.NO_COVERAGE, "reason": reason})

    def record(metric, f, unit, tag):
        label = f"{f['start']}..{f['end']}" if f.get("start") else f"as of {f['end']}"
        out.append({"metric": metric, "period_label": label, "period_end": f["end"], "value": float(f["val"]),
                    "unit": unit, "tag": tag, "accn": f.get("accn"), "filed": f.get("filed"),
                    "form": f.get("form"), "status": Status.OK, "reason": None})

    # Quarterly revenue (latest) and the same quarter a year earlier.
    tag, facts = _facts(cf, "us-gaap", REVENUE_TAGS, "USD")
    quarters = [f for f in _dedupe_latest_filed(facts) if (_duration_days(f) or 0) in range(80, 101)]
    if quarters:
        latest = quarters[-1]
        record("revenue_quarter", latest, "USD", tag)
        target = date.fromisoformat(latest["end"])
        prior = [f for f in quarters if abs((target - date.fromisoformat(f["end"])).days - 365) <= 15]
        if prior:
            record("revenue_quarter_prior_year", prior[-1], "USD", tag)
        else:
            missing("revenue_quarter_prior_year", "USD", "No same-quarter-last-year revenue in SEC data.")
    else:
        missing("revenue_quarter", "USD", "No quarterly revenue facts in SEC companyfacts (tags tried: "
                + ", ".join(REVENUE_TAGS) + ").")

    for metric, tags in (("net_income_quarter", ("NetIncomeLoss",)),):
        tag, facts = _facts(cf, "us-gaap", tags, "USD")
        q = [f for f in _dedupe_latest_filed(facts) if (_duration_days(f) or 0) in range(80, 101)]
        record(metric, q[-1], "USD", tag) if q else missing(metric, "USD", f"No quarterly {tags[0]} in SEC data.")

    tag, facts = _facts(cf, "us-gaap", ("CashAndCashEquivalentsAtCarryingValue",), "USD")
    inst = [f for f in _dedupe_latest_filed(facts) if not f.get("start")]
    record("cash", inst[-1], "USD", tag) if inst else missing("cash", "USD", "No cash balance in SEC data.")

    # Operating cash flow is usually reported year-to-date; the label shows the exact span.
    tag, facts = _facts(cf, "us-gaap", ("NetCashProvidedByUsedInOperatingActivities",), "USD")
    ocf = [f for f in _dedupe_latest_filed(facts) if f.get("start")]
    record("operating_cash_flow", ocf[-1], "USD", tag) if ocf else missing(
        "operating_cash_flow", "USD", "No operating cash flow in SEC data.")

    # Shares outstanding now and about a year ago (dilution check).
    tag, facts = _facts(cf, "dei", ("EntityCommonStockSharesOutstanding",), "shares")
    shares = _dedupe_latest_filed(facts)
    if shares:
        latest = shares[-1]
        record("shares_outstanding", latest, "shares", tag)
        target = date.fromisoformat(latest["end"])
        year_ago = [f for f in shares if 300 <= (target - date.fromisoformat(f["end"])).days <= 430]
        if year_ago:
            best = min(year_ago, key=lambda f: abs((target - date.fromisoformat(f["end"])).days - 365))
            record("shares_outstanding_year_ago", best, "shares", tag)
        else:
            missing("shares_outstanding_year_ago", "shares", "No share count from about a year earlier.")
    else:
        missing("shares_outstanding", "shares", "No cover-page share count (multi-class companies often lack one).")
    return out

"""Massive (formerly Polygon.io), free "Stocks Basic" plan: end-of-day prices.

One "grouped daily" call returns open/high/low/close/volume for every US stock
on a date. Key: MARKET_DATA_API_KEY, sent in a header (never in the URL).
"""
from __future__ import annotations

from datetime import date, datetime, time

from ..models import Status
from ..timeutil import NEW_YORK
from .http import FetchResult, HttpClient

PROVIDER = "massive"


def market_close(day: date) -> datetime:
    """The 4:00 PM New York close for a trading date (daylight saving handled)."""
    return datetime.combine(day, time(16, 0), tzinfo=NEW_YORK)


class MassiveClient:
    def __init__(self, http: HttpClient, api_key: str | None):
        self.http = http
        self.base = http.settings.raw["providers"]["massive"]["base_url"].rstrip("/")
        self.api_key = api_key

    def grouped_daily(self, day: date) -> FetchResult:
        url = f"{self.base}/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}"
        if not self.api_key:
            return FetchResult(PROVIDER, url, Status.NOT_CONFIGURED,
                               "MARKET_DATA_API_KEY is not set in .env (free Massive key).", None, self.http.now())
        # Each day's prices are saved permanently in market_bars, so the raw (~1 MB)
        # response only needs to survive a retry on the same day.
        return self.http.get(PROVIDER, url, params={"adjusted": "false"},
                             headers={"Authorization": f"Bearer {self.api_key}"}, ttl_hours=24)


def parse_grouped_daily(data: dict) -> list[dict]:
    """Rows with ticker/open/high/low/close/volume/vwap. Empty list = no trading that day."""
    out = []
    for r in data.get("results") or []:
        if r.get("c") is None or r.get("v") is None or not r.get("T"):
            continue  # incomplete row: skip rather than invent values
        out.append({"ticker": r["T"].upper(), "open": r.get("o"), "high": r.get("h"), "low": r.get("l"),
                    "close": float(r["c"]), "volume": float(r["v"]), "vwap": r.get("vw")})
    return out

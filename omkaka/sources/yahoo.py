"""Public price charts and headline links for manual research; no API key."""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from ..models import Status
from .http import FetchResult

RANGES = {"1mo", "3mo", "6mo", "1y"}


def symbol(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"\^?[A-Z0-9][A-Z0-9.=-]{0,14}", value):
        raise ValueError("Enter a stock symbol such as AAPL, MSFT, or BRK-B.")
    return value


def number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        return None
    return float(value)


def parse_chart(data: dict) -> dict:
    result = data["chart"]["result"][0]
    meta = result["meta"]
    prices = result.get("indicators", {}).get("quote", [{}])[0]
    bars = []
    for i, ts in enumerate(result.get("timestamp") or []):
        closes, volumes = prices.get("close") or [], prices.get("volume") or []
        stamp = datetime.fromtimestamp(ts, tz=timezone.utc)
        bars.append({"time": stamp.isoformat(), "close": number(closes[i]) if i < len(closes) else None,
                     "volume": number(volumes[i]) if i < len(volumes) else None})
    price = number(meta.get("regularMarketPrice"))
    stamp = meta.get("regularMarketTime")
    as_of = datetime.fromtimestamp(stamp, tz=timezone.utc) if stamp else None
    previous = None
    if as_of:
        zone = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
        today = as_of.astimezone(zone).date()
        older = [b for b in bars if b["close"] is not None and
                 datetime.fromisoformat(b["time"]).astimezone(zone).date() < today]
        if older:
            previous = older[-1]["close"]
    # chartPreviousClose is the start of the requested range, NOT yesterday's close.
    change = (price / previous - 1) * 100 if price is not None and previous else None
    return {"symbol": meta["symbol"], "name": meta.get("longName") or meta.get("shortName") or meta["symbol"],
            "currency": meta.get("currency") or "", "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
            "price": price, "as_of": as_of.isoformat() if as_of else None, "change_pct": change,
            "day_high": number(meta.get("regularMarketDayHigh")), "day_low": number(meta.get("regularMarketDayLow")),
            "volume": number(meta.get("regularMarketVolume")), "bars": bars}


def parse_news(text: str) -> list[dict]:
    root = ET.fromstring(text)
    news, seen = [], set()
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url or url in seen or urlsplit(url).scheme not in ("https", "http"):
            continue
        seen.add(url)
        published = None
        try:
            dt = parsedate_to_datetime(item.findtext("pubDate") or "")
            published = dt.astimezone(timezone.utc).isoformat() if dt.tzinfo else None
        except (ValueError, TypeError):
            pass
        news.append({"title": title[:350], "url": url, "published_at": published})
    return news[:16]


class YahooClient:
    def __init__(self, http):
        self.http = http

    def chart(self, ticker: str, period="1mo") -> FetchResult:
        ticker = symbol(ticker)
        if period not in RANGES:
            raise ValueError("Choose one of the available chart periods.")
        url = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(ticker, safe="")
        result = self.http.get("yahoo", url, params={"range": period, "interval": "1d"},
                               headers={"User-Agent": "OmKakaFinance/1.0 (personal research)"}, ttl_hours=5/60)
        if result.ok:
            try:
                result.data = parse_chart(result.data)
                if result.data["price"] is None:
                    result.status, result.reason = Status.PARTIAL, "The latest quote is unavailable; recorded chart history may still be available."
            except (TypeError, ValueError, KeyError, IndexError, OverflowError):
                result.status, result.reason, result.data = Status.FAILED, "No usable price data was returned for this symbol.", None
        return result

    def news(self, ticker: str) -> FetchResult:
        ticker = symbol(ticker)
        result = self.http.get("yahoo", "https://feeds.finance.yahoo.com/rss/2.0/headline",
                               params={"s": ticker, "region": "US", "lang": "en-US"},
                               headers={"User-Agent": "OmKakaFinance/1.0 (personal research)"}, ttl_hours=.25, parse="text")
        if result.ok:
            try:
                result.data = parse_news(result.data)
                if not result.data:
                    result.status = Status.NO_RESULTS
                    result.reason = "No headlines were returned for this symbol."
            except (TypeError, ValueError, ET.ParseError):
                result.status, result.reason, result.data = Status.FAILED, "The headline feed could not be read.", None
        return result

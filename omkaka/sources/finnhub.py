"""Finnhub free tier: company news. Key: NEWS_API_KEY, sent in a header."""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Status
from .http import FetchResult, HttpClient
from .text import clean_text, story_group

PROVIDER = "finnhub"


class FinnhubClient:
    def __init__(self, http: HttpClient, api_key: str | None):
        self.http = http
        self.base = http.settings.raw["providers"]["finnhub"]["base_url"].rstrip("/")
        self.api_key = api_key

    def company_news(self, ticker: str, start: date, end: date) -> FetchResult:
        url = f"{self.base}/company-news"
        if not self.api_key:
            return FetchResult(PROVIDER, url, Status.NOT_CONFIGURED,
                               "NEWS_API_KEY is not set in .env (free Finnhub key).", None, self.http.now())
        result = self.http.get(PROVIDER, url, params={"symbol": ticker, "from": start.isoformat(), "to": end.isoformat()},
                               headers={"X-Finnhub-Token": self.api_key}, ttl_hours=1)
        if result.ok and isinstance(result.data, list) and not result.data:
            result.status = Status.NO_RESULTS  # the search worked; there is simply no news
        return result


def parse_company_news(items: list) -> list[dict]:
    out, seen = [], set()
    for it in items or []:
        url, headline = it.get("url"), clean_text(it.get("headline"), 300)
        if not url or not headline or url in seen:
            continue
        seen.add(url)
        ts = it.get("datetime")
        out.append({
            "id": str(it.get("id") or url), "headline": headline, "url": url,
            "summary": clean_text(it.get("summary"), 600), "source": clean_text(it.get("source"), 80),
            "published_at": datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
            "group": story_group(headline, url),
        })
    return out

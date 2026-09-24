"""Official Reddit Data API, used ONLY with approved credentials.

Since late 2025 Reddit requires manual approval for every API app. Without
approved keys this client does nothing and reports NOT_CONFIGURED; it never
scrapes, never uses search-engine snippets, never works around blocks.

Storage: Reddit's terms expect content deleted by users to disappear, which
conflicts with a permanent journal. So the permanent record keeps only
links, IDs, timestamps, counts, and a fingerprint of the title. Post text
lives only in the short-lived cache.
Search results are never complete coverage, so an OK search is recorded as
PARTIAL with that limitation spelled out.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import Status
from .http import FetchResult, HttpClient
from .text import clean_text, story_group

PROVIDER = "reddit"
COVERAGE_NOTE = ("Reddit search covers only the configured subreddits and is not exhaustive; "
                 "treat as noisy discussion, never as confirmation.")


class RedditClient:
    def __init__(self, http: HttpClient, client_id: str | None, client_secret: str | None, user_agent: str | None):
        self.http = http
        cfg = http.settings.raw["providers"]["reddit"]
        self.auth_url, self.api_base = cfg["auth_url"], cfg["api_base"].rstrip("/")
        self.subreddits = cfg["subreddits"]
        self.creds = (client_id, client_secret, user_agent)
        self._token: str | None = None

    def _not_configured(self, url) -> FetchResult:
        return FetchResult(PROVIDER, url, Status.NOT_CONFIGURED,
                           "Reddit API credentials not set (approved Reddit access required). "
                           "Reddit sentiment is UNKNOWN, not neutral.", None, self.http.now())

    def _authenticate(self) -> FetchResult:
        cid, secret, ua = self.creds
        return self.http.request(PROVIDER, self.auth_url, method="POST", data={"grant_type": "client_credentials"},
                                 auth=(cid, secret), headers={"User-Agent": ua})

    def search(self, ticker: str, company: str | None, lookback: str = "week") -> FetchResult:
        url = f"{self.api_base}/r/{'+'.join(self.subreddits)}/search"
        if not all(self.creds):
            return self._not_configured(url)
        if self._token is None:
            auth = self._authenticate()
            if not auth.ok or not (auth.data or {}).get("access_token"):
                auth.status = auth.status if not auth.ok else Status.NO_ACCESS
                auth.reason = auth.reason or "Reddit did not issue a token (access may not be approved)."
                return auth
            self._token = auth.data["access_token"]
        query = f'"${ticker}"' + (f' OR "{company}"' if company else "")
        result = self.http.get(PROVIDER, url, params={"q": query, "restrict_sr": "on", "sort": "new", "t": lookback,
                                                      "limit": 50},
                               headers={"Authorization": f"Bearer {self._token}", "User-Agent": self.creds[2]},
                               ttl_hours=1)
        if result.ok:
            posts = (result.data or {}).get("data", {}).get("children", [])
            if posts:
                result.status, result.reason = Status.PARTIAL, COVERAGE_NOTE
            else:
                result.status = Status.NO_RESULTS
        return result


def parse_search(data: dict) -> list[dict]:
    out = []
    for child in (data or {}).get("data", {}).get("children", []):
        d = child.get("data", {})
        title = clean_text(d.get("title"), 300)
        if not d.get("id") or not title:
            continue
        out.append({
            "id": d["id"], "subreddit": d.get("subreddit"), "author": d.get("author"),
            "permalink": "https://www.reddit.com" + (d.get("permalink") or ""),
            "created_at": datetime.fromtimestamp(d["created_utc"], tz=timezone.utc) if d.get("created_utc") else None,
            "score": d.get("score"), "num_comments": d.get("num_comments"),
            "group": story_group(title, d.get("url")), "title": title,
        })
    return out


def summarize_discussion(posts: list[dict]) -> dict:
    """Counts only. Reposts of one story are not independent evidence."""
    return {
        "posts": len(posts),
        "independent_stories": len({p["group"] for p in posts}),
        "distinct_authors": len({p["author"] for p in posts if p.get("author")}),
    }

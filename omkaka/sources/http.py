"""One shared way to call outside services.

Every request goes through HttpClient.get(), which:
  * refuses providers marked paid (the $0 plan),
  * waits between calls to respect each provider's rate limit,
  * stops at our own per-run call cap,
  * retries a small, fixed number of times on "slow down" / server errors,
  * caches successful responses (the cache keeps the ORIGINAL fetch time),
  * turns every outcome into a Status (OK / NO_RESULTS / FAILED / ...),
  * keeps secrets out of URLs, the cache, error messages, and logs.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from ..budget import PaidCallsBlocked, check_provider_is_free
from ..config import Settings
from ..db import transaction
from ..models import Status
from ..timeutil import parse_utc_iso, to_utc_iso, utc_now

MAX_RETRIES = 2
MAX_RETRY_AFTER_SECONDS = 60
USER_AGENT_FALLBACK = "OmKakaFinance (personal research tool)"


class TransportError(Exception):
    """Network-level failure (timeout, DNS, connection refused)."""


@dataclass
class TransportResponse:
    status_code: int
    text: str
    headers: dict = field(default_factory=dict)


Transport = Callable[..., TransportResponse]


def requests_transport(method: str, url: str, params=None, headers=None, data=None, auth=None,
                       timeout: float = 30) -> TransportResponse:
    """Real network transport (used on your computer)."""
    import requests

    try:
        r = requests.request(method, url, params=params, headers=headers, data=data, auth=auth, timeout=timeout)
    except requests.RequestException as exc:
        raise TransportError(type(exc).__name__) from None
    return TransportResponse(r.status_code, r.text, dict(r.headers))


@dataclass
class FetchResult:
    provider: str
    url: str                      # never contains secrets
    status: Status
    reason: str | None
    data: Any
    fetched_at: datetime
    http_status: int | None = None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "[REDACTED]")
    return text


class HttpClient:
    def __init__(self, conn, settings: Settings, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = utc_now,
                 secrets: list[str] | None = None):
        self.conn = conn
        self.settings = settings
        self.transport = transport or requests_transport
        self.sleep = sleep
        self.monotonic = monotonic
        self.now = now
        self.secrets = [s for s in (secrets or []) if s]
        self._last_call: dict[str, float] = {}
        self.calls: dict[str, int] = {}

    # -------------------------------------------------------------- helpers
    def _cfg(self, provider: str) -> dict:
        return self.settings.raw.get("providers", {}).get(provider, {})

    def _wait_turn(self, provider: str) -> None:
        interval = float(self._cfg(provider).get("min_interval_seconds", 1.0))
        last = self._last_call.get(provider)
        if last is not None:
            gap = self.monotonic() - last
            if gap < interval:
                self.sleep(interval - gap)
        self._last_call[provider] = self.monotonic()

    @staticmethod
    def cache_key(provider: str, method: str, url: str, params: dict | None) -> str:
        raw = json.dumps([provider, method, url, sorted((params or {}).items())], default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _display_url(self, url: str, params: dict | None) -> str:
        if not params:
            return url
        return url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

    def _read_cache(self, key: str):
        row = self.conn.execute("SELECT * FROM http_cache WHERE cache_key = ?", (key,)).fetchone()
        if row and parse_utc_iso(row["expires_at"]) > self.now():
            return row
        return None

    def _write_cache(self, key, provider, url, status_code, body, fetched_at, ttl_hours):
        with transaction(self.conn):
            self.conn.execute(
                """INSERT OR REPLACE INTO http_cache(cache_key, provider, url, http_status, body, fetched_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (key, provider, url, status_code, body, to_utc_iso(fetched_at),
                 to_utc_iso(fetched_at + timedelta(hours=ttl_hours))),
            )

    def _result(self, provider, url, status, reason=None, data=None, http_status=None,
                fetched_at=None, from_cache=False) -> FetchResult:
        if reason:
            reason = redact(reason, self.secrets)[:300]
        return FetchResult(provider, url, Status(status), reason, data, fetched_at or self.now(),
                           http_status, from_cache)

    # -------------------------------------------------------------- main entry
    def request(self, provider: str, url: str, params: dict | None = None, headers: dict | None = None,
                method: str = "GET", data: dict | None = None, auth=None, ttl_hours: float = 0,
                parse: str = "json", not_found: Status = Status.NO_RESULTS) -> FetchResult:
        shown = self._display_url(url, params)
        try:
            check_provider_is_free(self.settings, provider)
        except PaidCallsBlocked as exc:
            return self._result(provider, shown, Status.NO_ACCESS, str(exc))

        key = self.cache_key(provider, method, url, params)
        if ttl_hours > 0 and method == "GET":
            row = self._read_cache(key)
            if row is not None:
                return self._parse(provider, shown, row["http_status"], row["body"], parse,
                                   parse_utc_iso(row["fetched_at"]), True, not_found)

        cap = int(self._cfg(provider).get("max_calls_per_run", 50))
        attempt = 0
        while True:
            if self.calls.get(provider, 0) >= cap:
                return self._result(provider, shown, Status.RATE_LIMITED,
                                    f"OmKakaFinance's own per-run cap of {cap} calls reached (settings.toml).")
            self._wait_turn(provider)
            self.calls[provider] = self.calls.get(provider, 0) + 1
            try:
                resp = self.transport(method, url, params=params, headers=headers, data=data, auth=auth, timeout=30)
            except TransportError as exc:
                if attempt < MAX_RETRIES:
                    attempt += 1
                    self.sleep(2 ** attempt)
                    continue
                return self._result(provider, shown, Status.FAILED, f"Network error after {attempt + 1} tries: {exc}")
            fetched_at = self.now()
            code = resp.status_code
            if code == 429 or code >= 500:
                wait = self._retry_after(resp, attempt)
                if attempt < MAX_RETRIES and wait is not None:
                    attempt += 1
                    self.sleep(wait)
                    continue
                status = Status.RATE_LIMITED if code == 429 else Status.FAILED
                return self._result(provider, shown, status,
                                    f"HTTP {code} after {attempt + 1} tries: {self._snippet(resp.text)}",
                                    http_status=code, fetched_at=fetched_at)
            if code == 200 and ttl_hours > 0 and method == "GET":
                self._write_cache(key, provider, shown, code, resp.text, fetched_at, ttl_hours)
            return self._parse(provider, shown, code, resp.text, parse, fetched_at, False, not_found)

    get = request

    def _retry_after(self, resp: TransportResponse, attempt: int) -> float | None:
        header = {k.lower(): v for k, v in resp.headers.items()}.get("retry-after")
        if header:
            try:
                seconds = float(header)
            except ValueError:
                return None
            return seconds if seconds <= MAX_RETRY_AFTER_SECONDS else None
        return float(2 ** (attempt + 1))

    @staticmethod
    def _snippet(text: str) -> str:
        return " ".join((text or "").split())[:160]

    def _parse(self, provider, shown, code, body, parse, fetched_at, from_cache, not_found) -> FetchResult:
        common = dict(http_status=code, fetched_at=fetched_at, from_cache=from_cache)
        if code == 200:
            if parse == "text":
                return self._result(provider, shown, Status.OK, data=body, **common)
            try:
                return self._result(provider, shown, Status.OK, data=json.loads(body), **common)
            except json.JSONDecodeError:
                return self._result(provider, shown, Status.FAILED, "Response was not valid JSON.", **common)
        if code in (401, 403):
            return self._result(provider, shown, Status.NO_ACCESS,
                                f"HTTP {code} (access refused; check key/approval/plan): {self._snippet(body)}", **common)
        if code == 404:
            reason = None if not_found is Status.NO_RESULTS else f"HTTP 404 (not found at source) {self._snippet(body)}".strip()
            return self._result(provider, shown, not_found, reason, **common)
        return self._result(provider, shown, Status.FAILED, f"HTTP {code}: {self._snippet(body)}", **common)

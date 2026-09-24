"""The shared request layer: honest statuses, retries, caching, secrets, $0 plan."""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from omkaka.config import load_settings
from omkaka.models import Status
from omkaka.sources.http import HttpClient, TransportError, TransportResponse

T = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)


class Script:
    """Fake network: returns queued responses and records every call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


def client(conn, transport, settings=None, secrets=None, now=None):
    sleeps = []
    c = HttpClient(conn, settings or load_settings("live"), transport=transport, sleep=sleeps.append,
                   now=now or (lambda: T), secrets=secrets)
    c.sleeps = sleeps
    return c


def test_ok_json(live_conn):
    c = client(live_conn, Script(TransportResponse(200, '{"a": 1}')))
    r = c.get("sec", "https://x/y")
    assert r.status is Status.OK and r.data == {"a": 1} and r.fetched_at == T


def test_retry_then_success(live_conn):
    t = Script(TransportResponse(503, "busy"), TransportResponse(200, "[]"))
    r = client(live_conn, t).get("sec", "https://x/y")
    assert r.status is Status.OK and len(t.calls) == 2


def test_persistent_429_is_rate_limited_not_empty(live_conn):
    t = Script(TransportResponse(429, "slow down"))
    r = client(live_conn, t).get("finnhub", "https://x/news")
    assert r.status is Status.RATE_LIMITED and r.data is None
    assert len(t.calls) == 3  # 1 try + 2 retries, then stop


def test_huge_retry_after_is_not_waited(live_conn):
    t = Script(TransportResponse(429, "", {"Retry-After": "3600"}))
    c = client(live_conn, t)
    r = c.get("finnhub", "https://x/news")
    assert r.status is Status.RATE_LIMITED and len(t.calls) == 1 and all(s < 3600 for s in c.sleeps)


@pytest.mark.parametrize("code,status", [(401, Status.NO_ACCESS), (403, Status.NO_ACCESS), (500, Status.FAILED),
                                         (418, Status.FAILED)])
def test_error_codes_map_to_unavailable(live_conn, code, status):
    r = client(live_conn, Script(TransportResponse(code, "nope"))).get("sec", "https://x/y")
    assert r.status is status and r.reason


def test_404_means_no_results_or_no_coverage(live_conn):
    c = client(live_conn, Script(TransportResponse(404, "")))
    assert c.get("sec", "https://x/a").status is Status.NO_RESULTS
    assert c.get("sec", "https://x/b", not_found=Status.NO_COVERAGE).status is Status.NO_COVERAGE


def test_network_error_after_retries(live_conn):
    r = client(live_conn, Script(TransportError("ConnectTimeout"))).get("sec", "https://x/y")
    assert r.status is Status.FAILED and "Network error" in r.reason


def test_bad_json_is_failure(live_conn):
    r = client(live_conn, Script(TransportResponse(200, "<html>oops"))).get("sec", "https://x/y")
    assert r.status is Status.FAILED


def test_cache_keeps_original_fetch_time(live_conn):
    t = Script(TransportResponse(200, '{"v": 1}'))
    clock = itertools.count()
    c = client(live_conn, t, now=lambda: T + timedelta(minutes=next(clock)))
    first = c.get("sec", "https://x/y", ttl_hours=1)
    second = c.get("sec", "https://x/y", ttl_hours=1)
    assert len(t.calls) == 1 and second.from_cache
    assert second.fetched_at == first.fetched_at  # honest: when the data was actually fetched


def test_expired_cache_refetches(live_conn):
    t = Script(TransportResponse(200, '{"v": 1}'))
    clock = {"now": T}
    c = client(live_conn, t, now=lambda: clock["now"])
    c.get("sec", "https://x/y", ttl_hours=1)
    clock["now"] = T + timedelta(hours=2)  # cache entry is now past its 1-hour life
    c.get("sec", "https://x/y", ttl_hours=1)
    assert len(t.calls) == 2


def test_rate_limiter_waits_between_calls(live_conn):
    t = Script(TransportResponse(200, "{}"))
    c = client(live_conn, t)
    c.monotonic = lambda: 100.0  # no real time passes between the two calls
    c.get("massive", "https://x/1")
    c.get("massive", "https://x/2")
    assert c.sleeps and c.sleeps[0] == pytest.approx(12.5)


def test_per_run_cap(live_conn):
    s = load_settings("live")
    s.raw["providers"]["finnhub"]["max_calls_per_run"] = 2
    c = client(live_conn, Script(TransportResponse(200, "[]")), settings=s)
    results = [c.get("finnhub", f"https://x/{i}") for i in range(3)]
    assert [r.status for r in results] == [Status.OK, Status.OK, Status.RATE_LIMITED]
    assert "own per-run cap" in results[2].reason


def test_paid_provider_is_refused_without_calling(live_conn):
    s = load_settings("live")
    s.raw["providers"]["finnhub"]["paid"] = True
    t = Script(TransportResponse(200, "[]"))
    r = client(live_conn, t, settings=s).get("finnhub", "https://x/news")
    assert r.status is Status.NO_ACCESS and "budget" in r.reason and not t.calls


def test_unknown_provider_treated_as_paid(live_conn):
    t = Script(TransportResponse(200, "[]"))
    r = client(live_conn, t).get("some_new_vendor", "https://x")
    assert r.status is Status.NO_ACCESS and not t.calls


def test_secrets_never_in_reason_url_or_cache(live_conn):
    key = "SUPERSECRETKEY123"
    t = Script(TransportResponse(403, f"invalid key {key}"))
    c = client(live_conn, t, secrets=[key])
    r = c.get("finnhub", "https://x/news", headers={"X-Finnhub-Token": key}, ttl_hours=1)
    assert key not in (r.reason or "") and key not in r.url
    t2 = Script(TransportResponse(200, "[]"))
    c2 = client(live_conn, t2, secrets=[key])
    c2.get("finnhub", "https://x/news", params={"symbol": "ABC"}, headers={"X-Finnhub-Token": key}, ttl_hours=1)
    dump = " ".join(str(tuple(r)) for r in live_conn.execute("SELECT * FROM http_cache"))
    assert key not in dump

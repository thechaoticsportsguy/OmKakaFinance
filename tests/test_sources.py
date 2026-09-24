"""Parsers and source clients (offline)."""
from datetime import date, datetime, timezone

from omkaka.config import load_settings
from omkaka.models import Status
from omkaka.sources import finnhub as fh
from omkaka.sources import massive as ms
from omkaka.sources import reddit as rd
from omkaka.sources import sec
from omkaka.sources.http import HttpClient, TransportResponse
from omkaka.sources.text import clean_text, story_group

from .test_http import Script, T


def test_daily_index_handles_forms_with_spaces():
    text = "\n".join([
        "Form Type   Company Name      CIK   Date Filed  File Name", "-" * 80,
        "8-K         ACME CORP                                     1234567     20260923    edgar/data/1234567/a.txt",
        "SC 13D      SOME HOLDER LLC                               7654321     20260923    edgar/data/7654321/b.txt",
    ])
    rows = sec.parse_daily_index(text)
    assert [(r["form"], r["cik"]) for r in rows] == [("8-K", "0001234567"), ("SC 13D", "0007654321")]


def test_submissions_keep_exact_acceptance_time():
    data = {"filings": {"recent": {"accessionNumber": ["0001-26-1", "0001-25-9"], "filingDate": ["2026-09-20", "2025-01-01"],
                                   "acceptanceDateTime": ["2026-09-20T20:15:00.000Z", "2025-01-01T12:00:00.000Z"],
                                   "form": ["8-K", "10-K"], "items": ["2.02", ""], "primaryDocument": ["a.htm", "b.htm"]}}}
    rows = sec.parse_submissions(data, since=date(2026, 6, 1))
    assert len(rows) == 1 and rows[0]["accepted_at"] == datetime(2026, 9, 20, 20, 15, tzinfo=timezone.utc)


def _fact(start, end, val, filed="2026-08-05"):
    return {"start": start, "end": end, "val": val, "accn": "a", "form": "10-Q", "filed": filed}


def test_fundamentals_pick_real_quarters_and_label_by_date():
    cf = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _fact("2026-01-01", "2026-06-30", 999),              # six months: NOT a quarter
        _fact("2025-04-01", "2025-06-30", 40),
        _fact("2026-04-01", "2026-06-30", 50),
    ]}}}, "dei": {}}}
    out = {m["metric"]: m for m in sec.extract_fundamentals(cf, date(2026, 9, 24))}
    assert out["revenue_quarter"]["value"] == 50 and out["revenue_quarter"]["period_label"] == "2026-04-01..2026-06-30"
    assert out["revenue_quarter_prior_year"]["value"] == 40


def test_fundamentals_missing_is_no_coverage_not_zero():
    out = {m["metric"]: m for m in sec.extract_fundamentals({"facts": {}}, date(2026, 9, 24))}
    assert out["revenue_quarter"]["value"] is None and out["revenue_quarter"]["status"] is Status.NO_COVERAGE
    assert out["shares_outstanding"]["value"] is None and out["shares_outstanding"]["reason"]


def test_restated_value_uses_latest_filing():
    cf = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _fact("2026-04-01", "2026-06-30", 50, filed="2026-08-05"),
        _fact("2026-04-01", "2026-06-30", 48, filed="2026-11-05")]}}}}}
    out = {m["metric"]: m for m in sec.extract_fundamentals(cf, date(2026, 12, 1))}
    assert out["revenue_quarter"]["value"] == 48 and out["revenue_quarter"]["filed"] == "2026-11-05"


def test_grouped_daily_skips_incomplete_rows():
    rows = ms.parse_grouped_daily({"results": [{"T": "ABC", "c": 5, "v": 100}, {"T": "XYZ", "v": 5}, {"c": 1, "v": 1}]})
    assert [r["ticker"] for r in rows] == ["ABC"]
    assert ms.parse_grouped_daily({"resultsCount": 0}) == []


def test_market_close_handles_daylight_saving():
    assert ms.market_close(date(2026, 7, 1)).astimezone(timezone.utc).hour == 20
    assert ms.market_close(date(2026, 1, 15)).astimezone(timezone.utc).hour == 21


def test_news_dedupe_and_story_groups():
    items = fh.parse_company_news([
        {"id": 1, "headline": "Acme wins deal!", "url": "https://a/1", "datetime": 1790000000},
        {"id": 2, "headline": "ACME wins deal", "url": "https://b/2", "datetime": 1790000100},
        {"id": 3, "headline": "Acme wins deal", "url": "https://a/1", "datetime": 1790000000},  # same URL
    ])
    assert len(items) == 2 and items[0]["group"] == items[1]["group"]


def test_empty_news_is_no_results(live_conn):
    http = HttpClient(live_conn, load_settings("live"), transport=Script(TransportResponse(200, "[]")),
                      sleep=lambda s: None, now=lambda: T)
    r = fh.FinnhubClient(http, "k").company_news("ABC", date(2026, 9, 1), date(2026, 9, 2))
    assert r.status is Status.NO_RESULTS


def test_missing_keys_are_not_configured_and_make_no_calls(live_conn):
    t = Script(TransportResponse(200, "{}"))
    http = HttpClient(live_conn, load_settings("live"), transport=t, sleep=lambda s: None, now=lambda: T)
    assert ms.MassiveClient(http, None).grouped_daily(date(2026, 9, 23)).status is Status.NOT_CONFIGURED
    assert fh.FinnhubClient(http, None).company_news("A", date(2026, 9, 1), date(2026, 9, 2)).status is Status.NOT_CONFIGURED
    assert sec.SecClient(http, None).company_tickers().status is Status.NOT_CONFIGURED
    r = rd.RedditClient(http, None, None, None).search("ABC", None)
    assert r.status is Status.NOT_CONFIGURED and "not neutral" in r.reason
    assert not t.calls


def test_reddit_results_are_partial_coverage(live_conn):
    listing = '{"data": {"children": [{"data": {"id": "p1", "title": "ABC to the moon", "author": "u1", "subreddit": "stocks", "permalink": "/r/stocks/p1", "created_utc": 1790000000}}]}}'
    t = Script(TransportResponse(200, '{"access_token": "tok"}'), TransportResponse(200, listing))
    http = HttpClient(live_conn, load_settings("live"), transport=t, sleep=lambda s: None, now=lambda: T)
    r = rd.RedditClient(http, "id", "secret", "ua").search("ABC", None)
    assert r.status is Status.PARTIAL and "not exhaustive" in r.reason
    posts = rd.parse_search(r.data)
    assert posts[0]["permalink"] == "https://www.reddit.com/r/stocks/p1"


def test_reddit_denied_token_is_no_access(live_conn):
    t = Script(TransportResponse(401, '{"error": "unauthorized"}'))
    http = HttpClient(live_conn, load_settings("live"), transport=t, sleep=lambda s: None, now=lambda: T)
    assert rd.RedditClient(http, "id", "secret", "ua").search("ABC", None).status is Status.NO_ACCESS


def test_reposts_are_not_independent():
    posts = [{"group": "g1", "author": "a"}, {"group": "g1", "author": "b"}, {"group": "g2", "author": "a"}]
    assert rd.summarize_discussion(posts) == {"posts": 3, "independent_stories": 2, "distinct_authors": 2}


def test_clean_text_strips_html_controls_and_caps_length():
    assert clean_text("<b>Hi</b>\x00 there&amp;‮", 100) == "Hi there&"
    assert len(clean_text("x" * 5000, 100)) == 100
    assert story_group("Hello, World!") == story_group("hello world")

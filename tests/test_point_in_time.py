"""Research may only use information available at its decision cutoff."""
from datetime import timedelta

from omkaka import store

from .conftest import T0


def test_evidence_after_cutoff_is_excluded(live_conn, run_id):
    add = lambda eid, **kw: store.record_evidence(live_conn, run_id, "news", "p", eid, ticker="ABC",
                                                  evidence_id=eid, **kw)
    add("before", fetched_at=T0 - timedelta(hours=1), published_at=T0 - timedelta(hours=2))
    add("fetched_late", fetched_at=T0 + timedelta(minutes=5), published_at=T0 - timedelta(hours=2))
    # A source that back-dates or future-dates items: publication after cutoff is excluded too.
    add("published_late", fetched_at=T0 - timedelta(minutes=1), published_at=T0 + timedelta(hours=1))
    ids = {r["evidence_id"] for r in store.evidence_as_of(live_conn, T0)}
    assert ids == {"before"}
    assert {r["evidence_id"] for r in store.evidence_for_run(live_conn, run_id)} == {"before"}


def test_market_data_effective_after_cutoff_is_excluded(live_conn, run_id):
    rec = lambda mid, eff: store.record_metric(
        live_conn, run_id, "ABC", "close_price", "as_of", "USD/share", "MARKET_DATA", "p",
        fetched_at=T0 - timedelta(minutes=1), value=1.0, effective_at=eff, metric_id=mid)
    rec("ok", T0 - timedelta(hours=12))
    rec("future", T0 + timedelta(hours=1))
    assert {r["metric_id"] for r in store.metrics_as_of(live_conn, T0)} == {"ok"}


def test_stored_metric_keeps_company_metric_period_and_source(live_conn, run_id):
    """Groundwork for Phase 3: numbers are matched on all of these, not just the digits."""
    store.record_metric(live_conn, run_id, "abc", "revenue", "FY2026 Q2", "USD", "REPORTED", "SEC EDGAR",
                        fetched_at=T0, value=48.2e6, cik="0000000001", period_end="2026-06-30",
                        source_url="https://example.com/f", source_identifier="0000000001-26-000001",
                        published_at=T0 - timedelta(days=30), metric_id="m")
    r = live_conn.execute("SELECT * FROM metric_values WHERE metric_id='m'").fetchone()
    assert (r["ticker"], r["cik"], r["metric"], r["period_label"], r["period_end"]) == \
        ("ABC", "0000000001", "revenue", "FY2026 Q2", "2026-06-30")
    assert r["source_identifier"] and r["source_url"] and r["published_at"] and r["fetched_at"]

"""Missing data is never neutral: never zero, never 'OK', always explained."""
import sqlite3
from datetime import timedelta

import pytest

from omkaka import store
from omkaka.display import NO_RESULTS_TEXT, UNAVAILABLE_TEXT, describe_metric, status_text
from omkaka.models import Status

from .conftest import T0


def _metric(conn, run_id, **kw):
    base = dict(ticker="ABC", metric="revenue", period_label="FY2026 Q2", unit="USD",
                value_kind="REPORTED", provider="test", fetched_at=T0)
    base.update(kw)
    return store.record_metric(conn, run_id, **base)


def test_unavailable_metric_is_stored_as_null_not_zero(live_conn, run_id):
    mid = _metric(live_conn, run_id, value=None, status=Status.FAILED, reason="timeout")
    row = live_conn.execute("SELECT * FROM metric_values WHERE metric_id=?", (mid,)).fetchone()
    assert row["value"] is None
    assert row["status"] == "FAILED"


def test_real_zero_stays_zero_and_is_distinct_from_missing(live_conn, run_id):
    mid = _metric(live_conn, run_id, value=0.0)
    row = live_conn.execute("SELECT * FROM metric_values WHERE metric_id=?", (mid,)).fetchone()
    assert row["value"] == 0.0 and row["status"] == "OK"
    assert describe_metric(row, 72, now=T0)["value"] == "$0.00"


def test_python_layer_rejects_unavailable_with_a_value(live_conn, run_id):
    with pytest.raises(ValueError):
        _metric(live_conn, run_id, value=0.0, status=Status.FAILED, reason="timeout")


def test_python_layer_rejects_ok_without_value(live_conn, run_id):
    with pytest.raises(ValueError):
        _metric(live_conn, run_id, value=None)


def test_unavailable_requires_reason(live_conn, run_id):
    with pytest.raises(ValueError):
        _metric(live_conn, run_id, value=None, status=Status.NO_ACCESS, reason="  ")


def test_database_itself_rejects_zero_for_failed_metric(live_conn, run_id):
    """Even code that skips the Python checks cannot store 0 for missing data."""
    with pytest.raises(sqlite3.IntegrityError):
        live_conn.execute(
            """INSERT INTO metric_values(metric_id, run_id, ticker, metric, period_label, value, unit,
               value_kind, provider, fetched_at, status, status_reason)
               VALUES ('x', ?, 'ABC', 'revenue', 'Q', 0, 'USD', 'REPORTED', 'p',
                       '2026-09-24T09:00:00.000000+00:00', 'FAILED', 'timeout')""", (run_id,))


def test_failure_and_no_results_are_different(live_conn, run_id):
    store.record_source_check(live_conn, run_id, "Reddit", "reddit", Status.NO_ACCESS,
                              reason="API access not approved", fetched_at=T0)
    store.record_source_check(live_conn, run_id, "News", "p", Status.NO_RESULTS, result_count=0, fetched_at=T0)
    rows = {r["source"]: r for r in live_conn.execute("SELECT * FROM source_checks")}
    reddit = status_text(rows["Reddit"]["status"], rows["Reddit"]["status_reason"])
    assert reddit.startswith(UNAVAILABLE_TEXT) and "not approved" in reddit
    assert "neutral" not in reddit.lower()
    assert status_text(rows["News"]["status"], None) == NO_RESULTS_TEXT


def test_unknown_result_count_is_not_zero(live_conn, run_id):
    cid = store.record_source_check(live_conn, run_id, "News", "p", Status.FAILED, reason="HTTP 500", fetched_at=T0)
    row = live_conn.execute("SELECT result_count FROM source_checks WHERE check_id=?", (cid,)).fetchone()
    assert row["result_count"] is None


def test_stale_vs_fresh(live_conn, run_id):
    fresh = _metric(live_conn, run_id, metric="close_price", period_label="as_of", unit="USD/share",
                    value_kind="MARKET_DATA", value=5.0, effective_at=T0 - timedelta(hours=10))
    old = _metric(live_conn, run_id, metric="close_price", period_label="as_of", unit="USD/share",
                  value_kind="MARKET_DATA", value=5.0, effective_at=T0 - timedelta(days=10))
    get = lambda m: live_conn.execute("SELECT * FROM metric_values WHERE metric_id=?", (m,)).fetchone()
    assert describe_metric(get(fresh), 96, now=T0)["freshness"] == "fresh"
    assert describe_metric(get(old), 96, now=T0)["freshness"] == "STALE"


def test_calculated_values_need_inputs(live_conn, run_id):
    with pytest.raises(ValueError):
        _metric(live_conn, run_id, metric="market_cap", value_kind="CALCULATED", value=1e8)


def test_filing_numbers_use_filing_staleness_not_market_data(live_conn, run_id):
    from omkaka.config import load_settings
    from omkaka.display import staleness_category
    mid = _metric(live_conn, run_id, value=48.2e6, published_at=T0 - timedelta(days=30))
    row = live_conn.execute("SELECT * FROM metric_values WHERE metric_id=?", (mid,)).fetchone()
    assert staleness_category(row) == "filing"
    hours = load_settings("live").staleness_hours(staleness_category(row))
    assert describe_metric(row, hours, now=T0)["freshness"] == "fresh"


def test_dollar_signs_are_escaped_for_display():
    from omkaka.display import md_escape
    assert md_escape("$48.2M and $3") == "\\$48.2M and \\$3"

"""History cannot be silently overwritten."""
import sqlite3
from datetime import timedelta

import pytest

from omkaka import journal, store
from omkaka.models import Status

from .conftest import T0


def _fill(conn, run_id):
    store.record_source_check(conn, run_id, "News", "p", Status.OK, result_count=1, fetched_at=T0)
    store.record_evidence(conn, run_id, "news", "p", "a1", fetched_at=T0, ticker="ABC", evidence_id="e1")
    store.record_metric(conn, run_id, "ABC", "revenue", "Q2", "USD", "REPORTED", "p", fetched_at=T0,
                        value=1.0, metric_id="m1")
    journal.append_entry(conn, "note", "t", "b", now=T0)


@pytest.mark.parametrize("sql", [
    "UPDATE evidence SET title = 'changed'",
    "DELETE FROM evidence",
    "UPDATE metric_values SET value = 999",
    "DELETE FROM metric_values",
    "UPDATE source_checks SET status = 'OK'",
    "DELETE FROM source_checks",
    "UPDATE journal_entries SET body = 'rewritten'",
    "DELETE FROM journal_entries",
    "DELETE FROM runs",
    "UPDATE meta SET value = 'demo' WHERE key = 'db_mode'",
])
def test_tables_refuse_edits(live_conn, run_id, sql):
    _fill(live_conn, run_id)
    with pytest.raises(sqlite3.IntegrityError, match="append-only|cannot|only an unfinished"):
        live_conn.execute(sql)


def test_run_can_be_finished_once_then_frozen(live_conn, run_id):
    store.finish_run(live_conn, run_id, "completed", now=T0)
    with pytest.raises(sqlite3.IntegrityError):
        live_conn.execute("UPDATE runs SET status = 'failed' WHERE run_id = ?", (run_id,))


def test_run_cutoff_cannot_be_moved(live_conn, run_id):
    with pytest.raises(sqlite3.IntegrityError):
        live_conn.execute("UPDATE runs SET decision_cutoff_at = '2030-01-01T00:00:00.000000+00:00'")


def test_non_utc_times_are_rejected_by_database(live_conn, run_id):
    with pytest.raises(sqlite3.IntegrityError):
        live_conn.execute(
            """INSERT INTO evidence(evidence_id, run_id, source_type, is_primary_source, provider,
               source_identifier, fetched_at, status) VALUES
               ('x', ?, 'news', 0, 'p', 's', '2026-09-24 05:00:00-04:00', 'OK')""", (run_id,))


def test_naive_datetimes_are_rejected(live_conn, run_id):
    with pytest.raises(ValueError, match="timezone"):
        store.record_evidence(live_conn, run_id, "news", "p", "a", fetched_at=T0.replace(tzinfo=None))

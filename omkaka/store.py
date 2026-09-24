"""Writing and reading research records (runs, source checks, evidence, metrics).

All writes are inserts; stored history is never edited. All times must be
timezone-aware datetimes and are stored as UTC.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime

from .db import db_mode, transaction
from .models import (
    PRIMARY_SOURCE_TYPES,
    SourceType,
    Status,
    ValueKind,
    check_metric_value,
    check_status_and_reason,
)
from .timeutil import to_utc_iso, utc_now


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------- runs

def start_run(
    conn: sqlite3.Connection,
    run_kind: str,
    decision_cutoff: datetime,
    settings_snapshot: dict | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
) -> str:
    """Record the start of a research run.

    decision_cutoff: the research may only use information available at or
    before this moment.
    """
    mode = db_mode(conn)
    if mode == "demo" and run_kind != "demo":
        raise ValueError("The demo database only accepts demo runs.")
    if mode == "live" and run_kind == "demo":
        raise ValueError("Demo runs are not allowed in the live database.")
    now = now or utc_now()
    run_id = run_id or _new_id("run")
    with transaction(conn):
        conn.execute(
            """INSERT INTO runs(run_id, run_kind, is_demo, started_at, decision_cutoff_at,
                                status, settings_json, fetched_at)
               VALUES (?, ?, ?, ?, ?, 'running', ?, ?)""",
            (
                run_id, run_kind, 1 if mode == "demo" else 0, to_utc_iso(now),
                to_utc_iso(decision_cutoff), json.dumps(settings_snapshot or {}, sort_keys=True),
                to_utc_iso(now),
            ),
        )
    return run_id


def finish_run(conn, run_id: str, status: str, reason: str | None = None, now: datetime | None = None) -> None:
    if status not in ("completed", "failed", "paused_budget"):
        raise ValueError(f"Invalid finish status {status!r}")
    if status != "completed" and not (reason and reason.strip()):
        raise ValueError("A failed or paused run needs a reason.")
    with transaction(conn):
        cur = conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, status_reason = ? WHERE run_id = ?",
            (to_utc_iso(now or utc_now()), status, reason, run_id),
        )
        if cur.rowcount != 1:
            raise KeyError(f"No run {run_id!r}")


# ---------------------------------------------------------------- source checks

def record_source_check(
    conn, run_id: str, source: str, provider: str, status: Status | str,
    reason: str | None = None, query: str | None = None, ticker: str | None = None,
    result_count: int | None = None, fetched_at: datetime | None = None,
) -> int:
    status = check_status_and_reason(status, reason)
    if status is Status.NO_RESULTS and result_count not in (None, 0):
        raise ValueError("NO_RESULTS cannot have a positive result_count.")
    with transaction(conn):
        cur = conn.execute(
            """INSERT INTO source_checks(run_id, source, provider, query, ticker, status,
                                         status_reason, result_count, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, source, provider, query, ticker, status.value, reason, result_count,
             to_utc_iso(fetched_at or utc_now())),
        )
        return cur.lastrowid


# ---------------------------------------------------------------- evidence

def record_evidence(
    conn, run_id: str, source_type: SourceType | str, provider: str, source_identifier: str,
    fetched_at: datetime, status: Status | str = Status.OK, reason: str | None = None,
    ticker: str | None = None, company_name: str | None = None, cik: str | None = None,
    url: str | None = None, title: str | None = None, excerpt: str | None = None,
    storage_note: str | None = None, published_at: datetime | None = None,
    effective_at: datetime | None = None, evidence_id: str | None = None,
    doc_type: str | None = None, dedupe_group: str | None = None,
) -> str:
    source_type = SourceType(source_type)
    status = check_status_and_reason(status, reason)
    evidence_id = evidence_id or _new_id("ev")
    content_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest() if excerpt else None
    with transaction(conn):
        conn.execute(
            """INSERT INTO evidence(evidence_id, run_id, ticker, company_name, cik, source_type,
                   is_primary_source, provider, source_identifier, url, title, excerpt,
                   content_hash, storage_note, published_at, effective_at, fetched_at,
                   status, status_reason, doc_type, dedupe_group)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence_id, run_id, ticker, company_name, cik, source_type.value,
                1 if source_type in PRIMARY_SOURCE_TYPES else 0, provider, source_identifier,
                url, title, excerpt, content_hash, storage_note, to_utc_iso(published_at),
                to_utc_iso(effective_at), to_utc_iso(fetched_at), status.value, reason,
                doc_type, dedupe_group,
            ),
        )
    return evidence_id


# ---------------------------------------------------------------- metrics

def record_metric(
    conn, run_id: str, ticker: str, metric: str, period_label: str, unit: str,
    value_kind: ValueKind | str, provider: str, fetched_at: datetime,
    value: float | None = None, status: Status | str = Status.OK, reason: str | None = None,
    evidence_id: str | None = None, cik: str | None = None, period_end: str | None = None,
    calculation: dict | None = None, source_url: str | None = None,
    source_identifier: str | None = None, published_at: datetime | None = None,
    effective_at: datetime | None = None, metric_id: str | None = None,
) -> str:
    """Store one number with full context (company, metric, period, source, times).

    If the data is unavailable, pass status (e.g. Status.FAILED) and a reason,
    and leave value=None. Zero is only ever stored when the source said zero.
    """
    status = check_metric_value(status, value, reason)
    value_kind = ValueKind(value_kind)
    if value_kind is ValueKind.CALCULATED and status is Status.OK and not calculation:
        raise ValueError("Calculated values must include their inputs and formula.")
    metric_id = metric_id or _new_id("m")
    with transaction(conn):
        conn.execute(
            """INSERT INTO metric_values(metric_id, run_id, evidence_id, ticker, cik, metric,
                   period_label, period_end, value, unit, value_kind, calculation_json, provider,
                   source_url, source_identifier, published_at, effective_at, fetched_at,
                   status, status_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                metric_id, run_id, evidence_id, ticker.upper(), cik, metric, period_label,
                period_end, None if value is None else float(value), unit, value_kind.value,
                json.dumps(calculation, sort_keys=True) if calculation else None, provider,
                source_url, source_identifier, to_utc_iso(published_at),
                to_utc_iso(effective_at), to_utc_iso(fetched_at), status.value, reason,
            ),
        )
    return metric_id


# ---------------------------------------------------------------- point-in-time reads

_AS_OF_RULE = """
    fetched_at <= :cutoff
    AND (published_at IS NULL OR published_at <= :cutoff)
    AND (effective_at IS NULL OR effective_at <= :cutoff)
"""


def evidence_as_of(conn, cutoff: datetime, ticker: str | None = None) -> list[sqlite3.Row]:
    """Only evidence that actually existed (and we had fetched) by `cutoff`.

    Research for a decision must use this, never "latest data", so a report
    cannot be contaminated by information that arrived afterwards.
    """
    sql = f"SELECT * FROM evidence WHERE {_AS_OF_RULE}"
    params: dict = {"cutoff": to_utc_iso(cutoff)}
    if ticker:
        sql += " AND ticker = :ticker"
        params["ticker"] = ticker.upper()
    return conn.execute(sql + " ORDER BY fetched_at", params).fetchall()


def metrics_as_of(conn, cutoff: datetime, ticker: str | None = None) -> list[sqlite3.Row]:
    sql = f"SELECT * FROM metric_values WHERE {_AS_OF_RULE}"
    params: dict = {"cutoff": to_utc_iso(cutoff)}
    if ticker:
        sql += " AND ticker = :ticker"
        params["ticker"] = ticker.upper()
    return conn.execute(sql + " ORDER BY ticker, metric, fetched_at", params).fetchall()


def run_row(conn, run_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()


def evidence_for_run(conn, run_id: str) -> list[sqlite3.Row]:
    """Evidence usable by this run: attached to it AND available by its cutoff."""
    run = run_row(conn, run_id)
    if run is None:
        raise KeyError(run_id)
    return conn.execute(
        f"SELECT * FROM evidence WHERE run_id = :run_id AND {_AS_OF_RULE} ORDER BY published_at",
        {"run_id": run_id, "cutoff": run["decision_cutoff_at"]},
    ).fetchall()


def last_successful_run(conn) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs WHERE status = 'completed' ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()


# ---------------------------------------------------------------- watchlist

def watchlist_change(conn, ticker: str, action: str, note: str | None = None, now: datetime | None = None) -> None:
    if action not in ("add", "remove"):
        raise ValueError("action must be 'add' or 'remove'")
    ticker = ticker.strip().upper()
    if not ticker.replace("-", "").replace(".", "").isalnum() or len(ticker) > 10:
        raise ValueError(f"{ticker!r} does not look like a ticker")
    with transaction(conn):
        conn.execute("INSERT INTO watchlist_events(ticker, action, note, fetched_at) VALUES (?, ?, ?, ?)",
                     (ticker, action, note, to_utc_iso(now or utc_now())))


def current_watchlist(conn) -> list[str]:
    rows = conn.execute(
        """SELECT ticker, action FROM watchlist_events WHERE event_id IN
           (SELECT MAX(event_id) FROM watchlist_events GROUP BY ticker) ORDER BY ticker"""
    ).fetchall()
    return [r["ticker"] for r in rows if r["action"] == "add"]

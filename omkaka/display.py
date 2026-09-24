"""Turning stored records into honest, human-readable text.

Pure functions (no Streamlit) so they can be tested directly.
"""
from __future__ import annotations

from datetime import datetime

from .models import UNAVAILABLE_STATUSES, Status
from .timeutil import format_new_york, parse_utc_iso, utc_now

UNAVAILABLE_TEXT = "Data unavailable"
NO_RESULTS_TEXT = "No results (search succeeded, nothing matched)"


def status_text(status: str, reason: str | None) -> str:
    s = Status(status)
    if s is Status.OK:
        return "OK"
    if s is Status.NO_RESULTS:
        return NO_RESULTS_TEXT
    if s is Status.PARTIAL:
        return f"Partial data — {reason}"
    return f"{UNAVAILABLE_TEXT} — {reason}"


def reference_time(row) -> str | None:
    """The time that determines freshness: market-data time, else publication, else fetch."""
    keys = row.keys()
    for key in ("effective_at", "published_at", "fetched_at"):
        if key in keys and row[key]:
            return row[key]
    return None


def is_stale(time_text: str | None, max_age_hours: float, now: datetime | None = None) -> bool | None:
    """True/False, or None when there is no time to judge by (unknown, not 'fresh')."""
    if not time_text:
        return None
    age = (now or utc_now()) - parse_utc_iso(time_text)
    return age.total_seconds() > max_age_hours * 3600


def format_number(value: float | None, unit: str) -> str:
    if value is None:
        return UNAVAILABLE_TEXT
    if unit == "USD":
        if abs(value) >= 1e9:
            return f"${value / 1e9:,.2f}B"
        if abs(value) >= 1e6:
            return f"${value / 1e6:,.2f}M"
        return f"${value:,.2f}"
    if unit == "USD/share":
        return f"${value:,.2f}"
    if unit == "shares":
        return f"{value:,.0f} shares"
    return f"{value:,.4g} {unit}"


def staleness_category(row) -> str:
    """Which [staleness_hours] setting applies: prices age fast, filings slowly."""
    kind = row["value_kind"]
    if kind in ("MARKET_DATA", "CALCULATED"):
        return "market_data"
    if kind in ("REPORTED", "GUIDANCE"):
        return "filing"
    return "default"


def md_escape(text) -> str:
    """Stop '$' in plain text being rendered as math formulas in the dashboard."""
    return str(text).replace("$", "\\$")


def describe_metric(row, max_age_hours: float, now: datetime | None = None) -> dict:
    """Everything the dashboard shows for one number, including why it is missing."""
    if row["status"] != Status.OK.value:
        shown = status_text(row["status"], row["status_reason"])
        freshness = "n/a"
    else:
        shown = format_number(row["value"], row["unit"])
        stale = is_stale(reference_time(row), max_age_hours, now)
        freshness = {True: "STALE", False: "fresh", None: "age unknown"}[stale]
    return {
        "ticker": row["ticker"],
        "metric": row["metric"],
        "period": row["period_label"],
        "value": shown,
        "kind": row["value_kind"],
        "freshness": freshness,
        "as_of (market time)": format_new_york(row["effective_at"]),
        "published": format_new_york(row["published_at"]),
        "fetched": format_new_york(row["fetched_at"]),
        "provider": row["provider"],
        "source": row["source_url"] or "—",
    }


def coverage_summary(check_rows) -> dict:
    """Counts for the 'source coverage' box on every candidate report."""
    counts = {"ok": 0, "no_results": 0, "partial": 0, "unavailable": 0}
    for r in check_rows:
        s = Status(r["status"])
        if s is Status.OK:
            counts["ok"] += 1
        elif s is Status.NO_RESULTS:
            counts["no_results"] += 1
        elif s is Status.PARTIAL:
            counts["partial"] += 1
        elif s in UNAVAILABLE_STATUSES:
            counts["unavailable"] += 1
    return counts

"""Time helpers.

Rule: every time stored in the database is UTC, written in ONE exact format:
    2026-09-24T10:00:00.000000+00:00
Using a single format means times sort correctly as text, and the database
can reject anything that is not UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(dt: datetime | None) -> str | None:
    """Convert a timezone-aware datetime to the standard stored UTC text.

    Naive datetimes (no timezone) are refused: guessing the timezone is how
    "what was known when" records become wrong.
    """
    if dt is None:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Refusing a datetime without a timezone; attach one first.")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_utc_iso(text: str | None) -> datetime | None:
    if text is None:
        return None
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"Stored time has no timezone: {text!r}")
    return dt.astimezone(timezone.utc)


def format_new_york(text_or_dt: str | datetime | None) -> str:
    """Human-friendly New York time, e.g. '2026-09-24 06:00 AM EDT'."""
    if text_or_dt is None:
        return "—"
    dt = parse_utc_iso(text_or_dt) if isinstance(text_or_dt, str) else text_or_dt
    return dt.astimezone(NEW_YORK).strftime("%Y-%m-%d %I:%M %p %Z")

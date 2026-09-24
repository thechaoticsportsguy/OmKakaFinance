"""NYSE trading calendar (regular full-day holidays), computed from the rules.

Limitations (shown in the README): unscheduled closures (e.g. national days of
mourning, emergencies) cannot be predicted; add them to
[schedule].extra_closed_dates in settings.toml. Early-close days (1 PM) are
treated as normal trading days, which is fine for end-of-day data.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """Saturday holiday -> Friday; Sunday holiday -> Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def nyse_holidays(year: int) -> dict[date, str]:
    h: dict[date, str] = {}
    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        h[new_year + timedelta(days=1)] = "New Year's Day (observed)"
    elif new_year.weekday() != 5:  # NYSE does not close the Friday before a Saturday New Year
        h[new_year] = "New Year's Day"
    h[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    h[_nth_weekday(year, 2, 0, 3)] = "Washington's Birthday"
    h[_easter(year) - timedelta(days=2)] = "Good Friday"
    h[_last_weekday(year, 5, 0)] = "Memorial Day"
    if year >= 2022:
        h[_observed(date(year, 6, 19))] = "Juneteenth"
    h[_observed(date(year, 7, 4))] = "Independence Day"
    h[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    h[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving Day"
    h[_observed(date(year, 12, 25))] = "Christmas Day"
    return h


def closed_reason(d: date, extra_closed: tuple[str, ...] = ()) -> str | None:
    """Why the market is closed on `d`, or None if it is a trading day."""
    if d.weekday() == 5:
        return "Saturday"
    if d.weekday() == 6:
        return "Sunday"
    if d.isoformat() in extra_closed:
        return "Special closure (from settings)"
    return nyse_holidays(d.year).get(d)


def is_trading_day(d: date, extra_closed: tuple[str, ...] = ()) -> bool:
    return closed_reason(d, extra_closed) is None


def previous_trading_day(d: date, extra_closed: tuple[str, ...] = ()) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d, extra_closed):
        d -= timedelta(days=1)
    return d


def next_trading_day(d: date, extra_closed: tuple[str, ...] = ()) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d, extra_closed):
        d += timedelta(days=1)
    return d


def trading_days_before(d: date, count: int, extra_closed: tuple[str, ...] = ()) -> list[date]:
    """`count` trading days strictly before `d`, oldest first."""
    out = []
    while len(out) < count:
        d = previous_trading_day(d, extra_closed)
        out.append(d)
    return list(reversed(out))


def trading_days_between(start: date, end: date, extra_closed: tuple[str, ...] = ()) -> list[date]:
    """Trading days with start < day < end."""
    out, d = [], start
    while True:
        d = next_trading_day(d, extra_closed)
        if d >= end:
            return out
        out.append(d)

from datetime import date

from omkaka.market_calendar import (closed_reason, is_trading_day, nyse_holidays, previous_trading_day,
                                    trading_days_before)


def test_2026_nyse_holidays():
    # Published NYSE 2026 schedule.
    expected = {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
                date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
    assert set(nyse_holidays(2026)) == expected


def test_observed_rules():
    assert closed_reason(date(2027, 12, 24)) == "Christmas Day"          # Sat Christmas -> Fri
    assert is_trading_day(date(2027, 12, 31))                             # Sat New Year: no Friday closure
    assert closed_reason(date(2023, 1, 2)) == "New Year's Day (observed)"  # Sun -> Mon
    assert closed_reason(date(2025, 4, 18)) == "Good Friday"


def test_weekends_and_extra_closures():
    assert closed_reason(date(2026, 9, 26)) == "Saturday"
    assert not is_trading_day(date(2026, 9, 24), extra_closed=("2026-09-24",))


def test_previous_trading_day_skips_weekend_and_holiday():
    assert previous_trading_day(date(2026, 9, 8)) == date(2026, 9, 4)  # Tue after Labor Day -> Fri
    assert trading_days_before(date(2026, 9, 9), 3) == [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8)]

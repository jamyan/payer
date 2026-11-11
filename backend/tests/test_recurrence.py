from datetime import date

import pytest

from app.recurrence import generate_dates, next_date, occurrence_date


def test_month_end_anchor_is_preserved():
    assert generate_dates(date(2027, 1, 31), "months", 1, date(2027, 1, 1), date(2027, 4, 30)) == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
    ]


def test_leap_year_recovers_original_day():
    assert occurrence_date(date(2024, 2, 29), "years", 1, 1) == date(2025, 2, 28)
    assert occurrence_date(date(2024, 2, 29), "years", 1, 4) == date(2028, 2, 29)


@pytest.mark.parametrize(
    "frequency,interval,index,expected",
    [
        ("days", 3, 2, date(2026, 1, 7)),
        ("weeks", 2, 2, date(2026, 1, 29)),
        ("months", 3, 2, date(2026, 7, 1)),
        ("years", 2, 2, date(2030, 1, 1)),
    ],
)
def test_intervals(frequency, interval, index, expected):
    assert occurrence_date(date(2026, 1, 1), frequency, interval, index) == expected


@pytest.mark.parametrize("frequency,interval,index", [("days", 0, 0), ("days", 1, -1), ("wrong", 1, 0)])
def test_invalid_recurrence(frequency, interval, index):
    with pytest.raises(ValueError):
        occurrence_date(date(2026, 1, 1), frequency, interval, index)


def test_range_and_next():
    assert generate_dates(date(2026, 1, 1), "days", 1, date(2026, 2, 1), date(2026, 1, 1)) == []
    assert next_date(date(2026, 1, 31), "months", 1, date(2026, 2, 28)) == date(2026, 3, 31)

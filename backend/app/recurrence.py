from calendar import monthrange
from datetime import date, timedelta


def occurrence_date(anchor: date, frequency: str, interval: int, index: int) -> date:
    if interval < 1 or index < 0:
        raise ValueError("Invalid interval/index")
    step = interval * index
    if frequency in ("days", "weeks"):
        return anchor + timedelta(days=step * (7 if frequency == "weeks" else 1))
    if frequency not in ("months", "years"):
        raise ValueError("Unsupported frequency")
    months = anchor.year * 12 + anchor.month - 1 + step * (12 if frequency == "years" else 1)
    year, month = divmod(months, 12)
    return date(year, month + 1, min(anchor.day, monthrange(year, month + 1)[1]))


def generate_dates(anchor: date, frequency: str, interval: int, start: date, end: date):
    if interval < 1:
        raise ValueError("Invalid interval")
    if end < start:
        return []
    if frequency in ("days", "weeks"):
        index = max(0, (start - anchor).days // (interval * (7 if frequency == "weeks" else 1)))
    else:
        months = (start.year - anchor.year) * 12 + start.month - anchor.month
        index = max(0, months // (interval * (12 if frequency == "years" else 1)))
    result = []
    while True:
        try:
            current = occurrence_date(anchor, frequency, interval, index)
        except (OverflowError, ValueError):
            if frequency not in ("days", "weeks", "months", "years"):
                raise
            break  # Beyond Python's supported calendar.
        if current > end:
            break
        if current >= start:
            result.append(current)
        index += 1
    return result


def next_date(anchor: date, frequency: str, interval: int, after: date):
    # All supported intervals are at most 3660 years; find next via binary search.
    lo, hi = 0, max(1, (after - anchor).days + 2)
    while lo < hi:
        mid = (lo + hi) // 2
        try:
            value = occurrence_date(anchor, frequency, interval, mid)
        except (ValueError, OverflowError):
            value = date.max
        if value <= after:
            lo = mid + 1
        else:
            hi = mid
    try:
        return occurrence_date(anchor, frequency, interval, lo)
    except (ValueError, OverflowError):
        return None

import calendar
from datetime import date, datetime, timedelta

from .errors import ApiError


def today():
    return date.today().isoformat()


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def parse_date(value, field='date', required=True):
    if value in (None, ''):
        if required:
            raise ApiError('invalid_date', f'{field} is required', 422, details={field: 'required'})
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        raise ApiError('invalid_date', f'{field} must be YYYY-MM-DD', 422, details={field: 'invalid_date'})


def add_days(date_str, days):
    return (date.fromisoformat(date_str) + timedelta(days=int(days))).isoformat()


def month_end(period):
    """period = 'YYYY-MM' -> last day of that month as ISO date."""
    y, m = int(period[:4]), int(period[5:7])
    return date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def valid_period(period):
    try:
        y, m = int(period[:4]), int(period[5:7])
        if period[4] != '-' or not (1 <= m <= 12) or len(period) != 7:
            raise ValueError
        return f'{y:04d}-{m:02d}'
    except Exception:
        raise ApiError('invalid_period', 'period must be YYYY-MM', 422)

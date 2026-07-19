"""Irish working-day rules for recurring direct-debit forecasts."""
from datetime import date, timedelta


def _easter_sunday(year):
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    adjustment = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * adjustment) // 451
    month = (h + adjustment - 7 * m + 114) // 31
    day = (h + adjustment - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _weekday_in_month(year, month, weekday, occurrence=1):
    value = date(year, month, 1)
    return value + timedelta(days=(weekday - value.weekday()) % 7 + 7 * (occurrence - 1))


def _last_weekday_in_month(year, month, weekday):
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    value = next_month - timedelta(days=1)
    return value - timedelta(days=(value.weekday() - weekday) % 7)


def irish_public_holidays(year):
    """Return the ten statutory Republic of Ireland public holidays."""
    february_first = date(year, 2, 1)
    february = february_first if february_first.weekday() == 4 else _weekday_in_month(year, 2, 0)
    return {date(year, 1, 1), february, date(year, 3, 17),
            _easter_sunday(year) + timedelta(days=1), _weekday_in_month(year, 5, 0),
            _weekday_in_month(year, 6, 0), _weekday_in_month(year, 8, 0),
            _last_weekday_in_month(year, 10, 0), date(year, 12, 25), date(year, 12, 26)}


def is_irish_working_day(value):
    return value.weekday() < 5 and value not in irish_public_holidays(value.year)


def next_irish_working_day(value):
    """Move a non-processing date forward; keep an ordinary weekday unchanged."""
    while not is_irish_working_day(value):
        value += timedelta(days=1)
    return value


def is_payment_overdue(scheduled_date, selected_date):
    return next_irish_working_day(scheduled_date) < selected_date


def uses_irish_banking_day(source_type, payment_type=None):
    """Return whether a forecast source represents a bank-processed payment."""
    return source_type == "recurring_bill" or (
        source_type == "planned_commitment"
        and (payment_type or "").lower() in {"bill", "insurance", "pet", "savings"}
    )

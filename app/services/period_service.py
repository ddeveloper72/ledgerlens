from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ReportingPeriod:
    key: str
    start_date: date
    end_date: date
    label: str


def _shift_month(value, months):
    month_index = value.year * 12 + value.month - 1 + months
    return date(month_index // 12, month_index % 12 + 1, 1)


def resolve_period(key="current_month", start_text=None, end_text=None, today=None):
    """Resolve supported reporting-period controls into inclusive date boundaries."""
    today = today or date.today()
    key = (key or "current_month").strip().lower()
    month_start = today.replace(day=1)
    if key == "previous_month":
        start = _shift_month(month_start, -1)
        end = month_start - timedelta(days=1)
        label = "Previous month"
    elif key == "last_3_months":
        start = _shift_month(month_start, -2)
        end = today
        label = "Last 3 months"
    elif key == "year_to_date":
        start = date(today.year, 1, 1)
        end = today
        label = "Year to date"
    elif key == "custom":
        try:
            start = date.fromisoformat((start_text or "").strip())
            end = date.fromisoformat((end_text or "").strip())
        except ValueError:
            raise ValueError("Custom period requires valid start and end dates.") from None
        if start > end:
            raise ValueError("Custom period start must be on or before its end.")
        label = f"{start.isoformat()} to {end.isoformat()}"
    else:
        key = "current_month"
        start = month_start
        end = today
        label = "Current month"
    return ReportingPeriod(key, start, end, label)


def apply_transaction_period(query, period, transaction_model):
    """Apply inclusive reporting boundaries to a transaction query."""
    return query.filter(
        transaction_model.posted_date >= period.start_date,
        transaction_model.posted_date <= period.end_date,
    )

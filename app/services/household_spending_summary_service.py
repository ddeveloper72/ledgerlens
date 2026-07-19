from datetime import date

from app.models import HouseholdSpendingSummary
from app.services.money import parse_money

SOURCE_TYPES = ("manual_summary", "statement_import", "transaction_import", "estimated", "calculated")
CONFIDENCE_VALUES = ("high", "moderate", "low")


def create_summary(session, *, period_start, period_end, category_name, amount,
                   is_estimated=False, source_type="manual_summary", confidence="moderate",
                   note=None, category_id=None, submitted_date=None):
    """Persist an explicit category total without creating a Transaction row."""
    if period_end < period_start:
        raise ValueError("Period end must be on or after period start.")
    if source_type not in SOURCE_TYPES:
        raise ValueError("Unsupported household spending source type.")
    if confidence not in CONFIDENCE_VALUES:
        raise ValueError("Unsupported confidence value.")
    row = HouseholdSpendingSummary(
        period_start=period_start, period_end=period_end, category_id=category_id,
        category_name=category_name.strip(), reported_amount=parse_money(amount),
        is_estimated=bool(is_estimated), source_type=source_type, confidence=confidence,
        note=(note or "").strip() or None, submitted_date=submitted_date or date.today(),
    )
    if not row.category_name:
        raise ValueError("Category is required.")
    session.add(row)
    return row


def summaries_for_period(session, start_date, end_date):
    """Return summaries whose inclusive reporting periods overlap the requested period."""
    return session.query(HouseholdSpendingSummary).filter(
        HouseholdSpendingSummary.period_start <= end_date,
        HouseholdSpendingSummary.period_end >= start_date,
    ).order_by(HouseholdSpendingSummary.period_start, HouseholdSpendingSummary.id).all()

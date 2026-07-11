from decimal import Decimal

from app.models import Transaction
from app.services.period_service import apply_transaction_period


def cash_flow_calendar(session, period):
    """Return transaction cash-flow evidence for a shared reporting period."""
    query = session.query(Transaction).filter(Transaction.excluded_from_analysis.is_(False)).order_by(Transaction.posted_date, Transaction.id)
    rows = apply_transaction_period(query, period, Transaction).all()
    return [{"date": row.posted_date, "label": row.cleaned_description, "amount": Decimal(row.amount), "flow": "in" if row.amount > 0 else "out"} for row in rows]

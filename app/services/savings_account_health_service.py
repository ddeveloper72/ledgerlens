from datetime import date, timedelta
from decimal import Decimal

from app.models import Account, Transaction
from app.services.account_balance_service import account_balance_at


def savings_account_health(session, today=None):
    """Summarise savings-tracking accounts separately from operating cash flow."""
    today = today or date.today()
    rows = []
    for account in session.query(Account).filter_by(reporting_scope="savings_tracking").order_by(Account.name).all():
        transactions = session.query(Transaction).filter_by(account_id=account.id, excluded_from_analysis=False).all()
        payroll_rows = [row for row in transactions if "payroll" in (row.cleaned_description or "").lower()]
        latest = max((row.posted_date for row in transactions), default=None)
        payroll_saved = sum((abs(Decimal(row.amount)) for row in payroll_rows), Decimal("0.00"))
        recent_payroll = sum((abs(Decimal(row.amount)) for row in payroll_rows if row.posted_date >= today - timedelta(days=90)), Decimal("0.00"))
        rows.append({
            "account": account,
            "balance": account_balance_at(session, account, today, include_internal=True),
            "payroll_saved_total": payroll_saved.quantize(Decimal("0.01")),
            "payroll_saved_recent": recent_payroll.quantize(Decimal("0.01")),
            "payroll_count": len(payroll_rows),
            "latest_date": latest,
            "stale": latest is None or latest < today - timedelta(days=45),
        })
    return rows

from decimal import Decimal

from app.models import Transaction


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def account_balance_at(session, account, target_date):
    """Return an account balance using its latest snapshot plus later transactions."""
    query = session.query(Transaction).filter(
        Transaction.account_id == account.id,
        Transaction.excluded_from_analysis.is_(False),
        Transaction.internal_transfer.is_(False),
    )
    if account.current_balance is not None and account.balance_as_of and target_date >= account.balance_as_of:
        movement = query.filter(
            Transaction.posted_date > account.balance_as_of,
            Transaction.posted_date <= target_date,
        ).with_entities(Transaction.amount).all()
        return money(account.current_balance) + sum((money(row[0]) for row in movement), Decimal("0.00"))
    rows = query.filter(Transaction.posted_date <= target_date).with_entities(Transaction.amount).all()
    return sum((money(row[0]) for row in rows), Decimal("0.00"))


def household_balance_position(session, accounts, target_date):
    """Separate owned cash from overdraft-backed available funds."""
    current = sum((account_balance_at(session, account, target_date) for account in accounts), Decimal("0.00"))
    overdraft = sum((money(account.overdraft_limit) for account in accounts), Decimal("0.00"))
    return {"current_balance": money(current), "overdraft_limit": money(overdraft), "available_funds": money(current + overdraft)}

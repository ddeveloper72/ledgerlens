from datetime import date
from decimal import Decimal

from app.models import Transaction


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def account_balance_at(session, account, target_date, include_internal=False):
    """Return an account balance using its latest snapshot plus later transactions."""
    query = session.query(Transaction).filter(
        Transaction.account_id == account.id,
        Transaction.excluded_from_analysis.is_(False),
    )
    if not include_internal:
        query = query.filter(Transaction.internal_transfer.is_(False))
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


def household_balance_evidence(session, accounts, target_date, today=None):
    """Return balance provenance without persisting reconstructed or forecast values."""
    today = today or date.today()
    position = household_balance_position(session, accounts, min(target_date, today))
    snapshots = [account.balance_as_of for account in accounts if account.balance_as_of]
    latest_snapshot = max(snapshots, default=None)
    latest_transaction = session.query(Transaction.posted_date).filter(
        Transaction.account_id.in_([account.id for account in accounts]),
        Transaction.excluded_from_analysis.is_(False),
        Transaction.posted_date <= min(target_date, today),
    ).order_by(Transaction.posted_date.desc()).first() if accounts else None
    latest_information_date = max(
        [value for value in (latest_snapshot, latest_transaction[0] if latest_transaction else None) if value],
        default=None,
    )
    if target_date > today:
        status = "Estimated"
        label = "Latest confirmed balance used as the forecast opening point"
    elif target_date < today:
        exact_snapshot = any(account.balance_as_of == target_date for account in accounts)
        status = "Actual" if exact_snapshot else "Reconstructed"
        label = "Actual balance at selected historical date" if exact_snapshot else "Reconstructed balance at selected historical date"
    else:
        status = "Actual" if latest_information_date == today else "Reconstructed"
        label = "Latest imported balance" if status == "Actual" else "Reconstructed balance from latest available records"
    stale_days = (today - latest_information_date).days if latest_information_date else None
    return {
        **position,
        "status": status,
        "label": label,
        "latest_information_date": latest_information_date,
        "is_stale": stale_days is None or stale_days > 45,
        "stale_days": stale_days,
        "is_reconstructed": status == "Reconstructed",
    }

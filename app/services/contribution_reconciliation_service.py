from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from app.models import ContributionMatch


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def reconciliation_amounts(reconciliation):
    """Return amounts, retaining compatibility with legacy single-match rows."""
    expected = money(reconciliation.expected_amount)
    accepted = [row for row in reconciliation.matches if row.status == "accepted"]
    if accepted:
        matched = sum((money(row.accepted_amount) for row in accepted), Decimal("0.00"))
    elif reconciliation.matched_transaction is not None and reconciliation.status in {"matched", "partially_matched"}:
        matched = max(money(reconciliation.matched_transaction.amount), Decimal("0.00"))
    else:
        matched = money(reconciliation.matched_amount)
    return {"expected_amount": expected, "matched_amount": matched,
            "outstanding_amount": max(expected - matched, Decimal("0.00"))}


def reconciliation_status(reconciliation, as_of=None):
    if reconciliation.status in {"cancelled", "skipped"}:
        return reconciliation.status
    amounts = reconciliation_amounts(reconciliation)
    if amounts["matched_amount"] >= amounts["expected_amount"]:
        return "matched"
    if amounts["matched_amount"] > 0:
        return "partially_matched"
    if as_of is not None and reconciliation.expected_date < as_of:
        return "overdue"
    return "expected"


def refresh_reconciliation(reconciliation, as_of=None):
    amounts = reconciliation_amounts(reconciliation)
    reconciliation.matched_amount = amounts["matched_amount"]
    reconciliation.outstanding_amount = amounts["outstanding_amount"]
    reconciliation.status = reconciliation_status(reconciliation, as_of)
    reconciliation.matched_at = max((row.matched_at for row in reconciliation.matches if row.status == "accepted"), default=None)
    return reconciliation


def add_match(reconciliation, transaction, accepted_amount=None, matched_at=None):
    amount = money(accepted_amount if accepted_amount is not None else transaction.amount)
    transaction_amount = money(transaction.amount)
    if transaction_amount <= 0 or amount <= 0:
        raise ValueError("Accepted contribution amount must be greater than zero.")
    if amount > transaction_amount:
        raise ValueError("Accepted contribution amount cannot exceed the incoming transaction amount.")
    destination_id = reconciliation.income_allocation.destination_account_id
    if destination_id is not None and transaction.account_id != destination_id:
        raise ValueError("The incoming transaction is not in the contribution destination account.")
    if abs((transaction.posted_date - reconciliation.expected_date).days) > 5 and reconciliation.income_allocation.frequency != "irregular":
        raise ValueError("The incoming transaction is outside the expected contribution review window.")
    if transaction.excluded_from_analysis:
        raise ValueError("An excluded transaction cannot be accepted as a contribution.")
    row = ContributionMatch(transaction=transaction, accepted_amount=amount,
                            status="accepted", matched_at=matched_at or datetime.now())
    reconciliation.matches.append(row)
    refresh_reconciliation(reconciliation)
    return row

from app.models import Account, Category, Transaction


CREDIT_UNION_INTERNAL_RULES = {
    "mngtfee": {
        "category": "Savings",
        "reason": "Credit Union earmarked savings transfer",
    },
    "eft disbur": {
        "category": "Transfers",
        "reason": "Credit Union shares-account movement",
    },
}


def credit_union_internal_rule(description):
    """Return a user-confirmed internal-movement rule for a Credit Union description."""
    text = (description or "").strip().lower()
    return next((rule for marker, rule in CREDIT_UNION_INTERNAL_RULES.items() if marker in text), None)


def _category(session, name):
    category = session.query(Category).filter_by(name=name).first()
    if not category:
        category = Category(name=name)
        session.add(category)
        session.flush()
    return category


def mark_credit_union_internal_movements(session):
    """Backfill confirmed Credit Union movements without deleting ledger transactions."""
    rows = (
        session.query(Transaction)
        .join(Transaction.account)
        .filter(
            Account.name.ilike("%credit union%"),
            Transaction.excluded_from_analysis.is_(False),
            Transaction.internal_transfer.is_(False),
        )
        .all()
    )
    updated = 0
    for transaction in rows:
        rule = credit_union_internal_rule(transaction.cleaned_description)
        if not rule:
            continue
        transaction.internal_transfer = True
        transaction.internal_transfer_reason = rule["reason"]
        transaction.category_id = _category(session, rule["category"]).id
        transaction.household_flag = "personal"
        transaction.review_state = "reviewed"
        updated += 1
    session.flush()
    return updated

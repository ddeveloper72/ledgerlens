from decimal import Decimal

from app.models import Transaction


def is_transaction_duplicate(session, account_id, posted_date, amount, cleaned_description):
    """Check whether a transaction with the same account/date/description/amount already exists."""
    normalized_amount = Decimal(str(amount)).quantize(Decimal("0.01"))

    existing = (
        session.query(Transaction)
        .filter_by(
            account_id=account_id,
            posted_date=posted_date,
            cleaned_description=cleaned_description,
            amount=normalized_amount,
        )
        .first()
    )
    return existing is not None

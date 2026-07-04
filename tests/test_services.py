from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import Account, Merchant, MerchantAlias, Transaction, User
from app.services.categorization import assign_category
from app.services.duplicate_detection import is_transaction_duplicate
from app.services.merchant_mapping import resolve_merchant


def setup_base_records():
    user = User(name="Service User")
    db.session.add(user)
    db.session.flush()

    account = Account(user_id=user.id, name="Primary", account_type="checking")
    db.session.add(account)
    db.session.flush()

    return account


def test_merchant_mapping(app):
    with app.app_context():
        account = setup_base_records()
        merchant = Merchant(name="Sample Utility Provider")
        db.session.add(merchant)
        db.session.flush()
        db.session.add(MerchantAlias(alias="utility bill", merchant_id=merchant.id))
        db.session.commit()

        resolved = resolve_merchant(db.session, "Monthly Utility Bill Payment")
        assert resolved is not None
        assert resolved.name == "Sample Utility Provider"
        assert account.id > 0


def test_category_assignment(app):
    with app.app_context():
        category = assign_category(db.session, "Example Grocery Store", "Weekly grocery order")
        db.session.commit()

        assert category.name == "Groceries"


def test_duplicate_detection(app):
    with app.app_context():
        account = setup_base_records()
        txn = Transaction(
            account_id=account.id,
            posted_date=date(2026, 2, 5),
            original_description="Coffee Shop",
            cleaned_description="Coffee Shop",
            amount=Decimal("7.50"),
            household_flag="personal",
        )
        db.session.add(txn)
        db.session.commit()

        is_dup = is_transaction_duplicate(
            db.session,
            account.id,
            date(2026, 2, 5),
            Decimal("7.50"),
            "Coffee Shop",
        )

        assert is_dup is True

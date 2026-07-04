from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import Account, Transaction, User


def test_create_models(app):
    with app.app_context():
        user = User(name="Test User")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="Checking", account_type="checking")
        db.session.add(account)
        db.session.flush()

        transaction = Transaction(
            account_id=account.id,
            posted_date=date(2026, 1, 10),
            original_description="Example Grocery Store",
            cleaned_description="Example Grocery Store",
            amount=Decimal("24.99"),
            household_flag="household",
        )
        db.session.add(transaction)
        db.session.commit()

        assert User.query.count() == 1
        assert Account.query.count() == 1
        assert Transaction.query.count() == 1

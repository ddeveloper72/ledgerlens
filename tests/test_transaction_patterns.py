import io
from decimal import Decimal

import pytest
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import Account, Category, Transaction, TransactionPatternRule, User
from app.services.csv_import import CSVImportError, import_transactions
from app.services.description_patterns import description_pattern_key, payment_method_for


def _bank_file(account_key, description, amount="12.34", filename="statement.csv"):
    payload = (
        "Posted Account,Posted Transactions Date,Description1,Debit Amount,Credit Amount\n"
        f'"{account_key}","18/07/2026","{description}","{amount}",\n'
    ).encode()
    return FileStorage(stream=io.BytesIO(payload), filename=filename)


def test_canonical_pattern_removes_payment_rail_and_changing_bank_reference():
    first = description_pattern_key("D/D Example Service IE26070112345678", Decimal("-20"))
    second = description_pattern_key("D/D Example Service IE26080187654321", Decimal("-25"))

    assert first == second == "direct_debit:EXAMPLE SERVICE <BANKREF>"
    assert payment_method_for("VDP-EXAMPLE SHOP") == "card"
    assert payment_method_for("*MOBI CURRENT ACCOUNT") == "mobile_transfer"
    assert payment_method_for("HSE PAYROLL") == "payroll"


def test_canonical_pattern_keeps_short_account_suffixes_distinct():
    first = description_pattern_key("*MOBI CURRENT-123", Decimal("800"))
    second = description_pattern_key("*MOBI CURRENT-456", Decimal("800"))

    assert first == "mobile_transfer:CURRENT-123"
    assert second == "mobile_transfer:CURRENT-456"
    assert first != second


def test_import_blocks_statement_bound_to_another_account(app):
    with app.app_context():
        user = User(name="Example User")
        db.session.add(user)
        db.session.flush()
        joint = Account(user_id=user.id, name="Joint", statement_account_key="111111-00000083")
        personal = Account(user_id=user.id, name="Personal", statement_account_key="111111-00000006")
        db.session.add_all([joint, personal])
        db.session.commit()

        with pytest.raises(CSVImportError, match="belongs to account key"):
            import_transactions(
                _bank_file("111111-00000083", "D/D EXAMPLE SERVICE IE26071812345678"),
                personal.id,
                declared_source="bank",
            )
        assert Transaction.query.count() == 0


def test_durable_pattern_rule_reviews_future_variable_amount_import(app):
    with app.app_context():
        user = User(name="Example User")
        category = Category(name="Utilities")
        db.session.add_all([user, category])
        db.session.flush()
        account = Account(user_id=user.id, name="Household", statement_account_key="111111-00000083")
        db.session.add(account)
        db.session.flush()
        pattern = description_pattern_key("D/D EXAMPLE SERVICE IE26070112345678", Decimal("-20"))
        db.session.add(TransactionPatternRule(
            account_id=account.id,
            pattern_key=pattern,
            direction="out",
            category_id=category.id,
            household_flag="household",
            payment_method="direct_debit",
        ))
        db.session.commit()

        result = import_transactions(
            _bank_file("111111-00000083", "D/D EXAMPLE SERVICE IE26071887654321", "48.91"),
            account.id,
            declared_source="bank",
        )

        row = Transaction.query.one()
        assert result["created"] == 1
        assert row.canonical_pattern == pattern
        assert row.payment_method == "direct_debit"
        assert row.category.name == "Utilities"
        assert row.household_flag == "household"
        assert row.review_state == "reviewed"

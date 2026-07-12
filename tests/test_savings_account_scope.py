from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import Account, Transaction, User
from app.services.credit_union_internal import mark_credit_union_internal_movements
from app.services.recurrence_service import detect_recurring_candidates
from app.services.savings_account_health_service import savings_account_health


def test_savings_tracking_account_is_separate_from_operating_dashboard(client, app):
    with app.app_context():
        user = User(name="Example User"); db.session.add(user); db.session.flush()
        operating = Account(user_id=user.id, name="Example Operating", account_type="checking", reporting_scope="household_operating")
        savings = Account(user_id=user.id, name="Example Credit Union", account_type="savings", reporting_scope="savings_tracking")
        db.session.add_all([operating, savings]); db.session.flush()
        db.session.add_all([
            Transaction(account_id=operating.id, posted_date=date.today(), original_description="Example Income", cleaned_description="Example Income", amount=Decimal("100.00"), review_state="reviewed"),
            Transaction(account_id=savings.id, posted_date=date.today(), original_description="PAYROLL", cleaned_description="PAYROLL", amount=Decimal("-25.00"), review_state="reviewed"),
        ]); db.session.commit()
    body = client.get("/").get_data(as_text=True)
    assert "Monthly Income</p>\n        <p class=\"metric-value\">100.00" in body
    assert "Savings Account Health" in body


def test_payroll_savings_are_internal_and_reported_as_positive_saved_amount(app):
    with app.app_context():
        user = User(name="Example User"); db.session.add(user); db.session.flush()
        savings = Account(user_id=user.id, name="Example Credit Union", account_type="savings", reporting_scope="savings_tracking")
        db.session.add(savings); db.session.flush()
        for offset in (0, 14, 28):
            db.session.add(Transaction(account_id=savings.id, posted_date=date(2026, 1, 1 + offset), original_description="PAYROLL", cleaned_description="PAYROLL", amount=Decimal("-20.00"), review_state="reviewed"))
        db.session.commit()
        assert mark_credit_union_internal_movements(db.session) == 3
        db.session.commit()
        rows = Transaction.query.all()
        assert all(row.internal_transfer for row in rows)
        assert all(row.category.name == "Savings" for row in rows)
        health = savings_account_health(db.session, today=date(2026, 2, 1))[0]
        assert health["payroll_saved_total"] == Decimal("60.00")
        assert health["payroll_count"] == 3
        assert detect_recurring_candidates(db.session) == []

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import Account, Merchant, PlannedCommitment, RecurringBill, Transaction, User
from app.services.daily_financial_health_service import build_daily_financial_health
from app.services.irish_working_day_service import is_payment_overdue, next_irish_working_day


def test_weekend_direct_debit_moves_to_monday_and_is_not_early_overdue():
    saturday = date(2026, 7, 18)
    assert next_irish_working_day(saturday) == date(2026, 7, 20)
    assert not is_payment_overdue(saturday, date(2026, 7, 19))
    assert not is_payment_overdue(saturday, date(2026, 7, 20))
    assert is_payment_overdue(saturday, date(2026, 7, 21))


def test_weekend_before_august_bank_holiday_moves_to_tuesday():
    assert next_irish_working_day(date(2026, 8, 1)) == date(2026, 8, 4)


def test_daily_health_uses_adjusted_processing_date(app):
    with app.app_context():
        user = User(name="Example User")
        db.session.add(user); db.session.flush()
        account = Account(user_id=user.id, name="Example Household Account")
        merchant = Merchant(name="Example Service")
        db.session.add_all([account, merchant]); db.session.flush()
        db.session.add_all([
            Transaction(account_id=account.id, posted_date=date(2026, 7, 17),
                original_description="Example opening amount", cleaned_description="Example opening amount",
                amount=Decimal("500.00"), review_state="reviewed"),
            RecurringBill(merchant_id=merchant.id, display_name="Example Direct Debit",
                expected_amount=Decimal("50.00"), cadence="monthly",
                expected_next_date=date(2026, 7, 18), active=True),
        ])
        db.session.commit()
        sunday = build_daily_financial_health(db.session, date(2026, 7, 19))
        row = next(item for item in sunday["outstanding_bills"] if item["name"] == "Example Direct Debit")
        assert row["date"] == date(2026, 7, 20)
        assert row["status"] == "expected"
        assert not sunday["overdue_commitments"]
        tuesday = build_daily_financial_health(db.session, date(2026, 7, 21))
        row = next(item for item in tuesday["outstanding_bills"] if item["name"] == "Example Direct Debit")
        assert row["status"] == "overdue"


def test_bill_like_planned_commitment_uses_adjusted_processing_date(app):
    with app.app_context():
        user = User(name="Example User")
        db.session.add(user); db.session.flush()
        account = Account(user_id=user.id, name="Example Household Account")
        db.session.add_all([account, PlannedCommitment(display_name="Example Insurance",
            amount=Decimal("25.00"), frequency="monthly", next_expected_date=date(2026, 7, 18),
            commitment_type="insurance", active=True)])
        db.session.commit()
        result = build_daily_financial_health(db.session, date(2026, 7, 19))
        row = next(item for item in result["outstanding_bills"] if item["name"] == "Example Insurance")
        assert row["date"] == date(2026, 7, 20)
        assert row["status"] == "expected"

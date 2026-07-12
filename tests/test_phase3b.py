from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import (
    Account, HouseholdForecastSetting, IncomeAllocation, IncomeSchedule, Merchant, PaymentReconciliation,
    PlannedCommitment, RecurringBill, Transaction, User, VariableBudget,
)
from app.services.daily_financial_health_service import build_daily_financial_health
from app.services.account_balance_service import account_balance_at, household_balance_position


def setup_account():
    user = User(name="Example User")
    db.session.add(user); db.session.flush()
    account = Account(user_id=user.id, name="Example Account", account_type="checking")
    db.session.add(account); db.session.flush()
    return account


def add_transaction(account, when, amount, description="Example transaction", **values):
    row = Transaction(account_id=account.id, posted_date=when, original_description=description,
        cleaned_description=description, amount=Decimal(amount), review_state="reviewed", **values)
    db.session.add(row); db.session.flush(); return row


def add_household_income(account, amount, when, frequency="fortnightly"):
    schedule = IncomeSchedule(display_name="Example Income", account_id=account.id,
        amount=Decimal(amount), frequency=frequency, next_expected_date=when, active=True,
        availability_classification="fully_available")
    db.session.add(schedule); db.session.flush()
    db.session.add(IncomeAllocation(income_schedule_id=schedule.id, allocation_type="household_contribution",
        amount=Decimal(amount), destination_account_id=account.id, effective_from=when,
        frequency=frequency, status="confirmed", source_type="manual"))
    return schedule


def test_selected_date_before_income_and_five_day_bill(app):
    with app.app_context():
        account = setup_account()
        add_transaction(account, date(2026, 1, 10), "1000.00")
        add_household_income(account, "500.00", date(2026, 1, 20))
        merchant = Merchant(name="Example Essential Service"); db.session.add(merchant); db.session.flush()
        db.session.add(RecurringBill(merchant_id=merchant.id, display_name="Example Bill",
            expected_amount=Decimal("200.00"), amount_tolerance=Decimal("0.00"), cadence="monthly",
            expected_next_date=date(2026, 1, 12), active=True))
        db.session.commit()
        result = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert result["balance"] == Decimal("1000.00")
        assert result["next_income_date"] == date(2026, 1, 20)
        assert result["days_until_income"] == 10
        assert result["projected_pre_income_balance"] == Decimal("800.00")
        assert result["minimum_projected_balance"] == Decimal("800.00")
        assert len(result["upcoming_five_days"]) == 1


def test_internal_transfer_is_excluded_and_variable_budget_is_estimated(app):
    with app.app_context():
        account = setup_account()
        add_transaction(account, date(2026, 1, 10), "500.00")
        add_transaction(account, date(2026, 1, 10), "999.00", internal_transfer=True)
        add_household_income(account, "100.00", date(2026, 1, 20))
        db.session.add(VariableBudget(display_name="Example Grocery Estimate", amount=Decimal("30.00"), frequency="weekly", next_expected_date=date(2026, 1, 12), essential=True, active=True))
        db.session.commit()
        before = Transaction.query.count()
        result = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert result["balance"] == Decimal("500.00")
        assert any(row["label"] == "Estimated" for row in result["outstanding_bills"])
        assert result["data_confidence"]["level"] in {"moderate", "low"}
        assert Transaction.query.count() == before


def test_negative_and_below_buffer_states_generate_guidance(app):
    with app.app_context():
        account = setup_account(); add_transaction(account, date(2026, 1, 10), "100.00")
        add_household_income(account, "100.00", date(2026, 1, 20), "irregular")
        db.session.add_all([HouseholdForecastSetting(safety_buffer=Decimal("80.00")), PlannedCommitment(display_name="Example Essential", amount=Decimal("150.00"), frequency="one-off", next_expected_date=date(2026, 1, 15), active=True, commitment_type="bill")])
        db.session.commit()
        result = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert result["state"] == "critical"
        assert result["minimum_projected_balance"] == Decimal("-50.00")
        assert result["minimum_balance_date"] == date(2026, 1, 15)
        assert result["required_contribution"] == Decimal("130.00")
        assert result["contribution_deadline"] == date(2026, 1, 15)
        assert result["payments_before_next_income_total"] == Decimal("150.00")
        assert any(item["severity"] == "urgent" for item in result["recommendations"])


def test_no_topup_when_payments_and_buffer_are_covered(app):
    with app.app_context():
        account = setup_account(); add_transaction(account, date(2026, 1, 10), "500.00")
        add_household_income(account, "100.00", date(2026, 1, 20))
        db.session.add_all([
            HouseholdForecastSetting(safety_buffer=Decimal("100.00")),
            PlannedCommitment(display_name="Example Covered Payment", amount=Decimal("200.00"), frequency="one-off", next_expected_date=date(2026, 1, 15), active=True, commitment_type="bill"),
        ])
        db.session.commit()
        result = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert result["required_contribution"] == Decimal("0.00")
        assert result["contribution_deadline"] is None
        assert result["payments_before_next_income_total"] == Decimal("200.00")
        assert result["projected_position_after_contribution"] == Decimal("300.00")


def test_proposed_and_reviewed_partial_payment(app):
    with app.app_context():
        account = setup_account()
        merchant = Merchant(name="Example Provider"); db.session.add(merchant); db.session.flush()
        add_transaction(account, date(2026, 1, 12), "-60.00", merchant_id=merchant.id)
        bill = RecurringBill(merchant_id=merchant.id, display_name="Example Payment", expected_amount=Decimal("100.00"),
            amount_tolerance=Decimal("50.00"), cadence="monthly", expected_next_date=date(2026, 1, 12), active=True)
        db.session.add(bill); db.session.commit()
        proposal = build_daily_financial_health(db.session, date(2026, 1, 13))
        occurrence = next(row for row in proposal["outstanding_bills"] if row["source_type"] == "recurring_bill")
        assert occurrence["status"] == "overdue"
        assert occurrence["proposed_status"] == "partially_matched"
        db.session.add(PaymentReconciliation(source_type="recurring_bill", source_id=bill.id,
            expected_date=date(2026, 1, 12), expected_amount=Decimal("100.00"), status="partially_matched",
            matched_transaction_id=occurrence["proposed_transaction"].id))
        db.session.commit()
        reviewed = build_daily_financial_health(db.session, date(2026, 1, 13))
        assert reviewed["bills_paid"][0]["status"] == "partially_matched"


def test_daily_health_get_is_read_only_and_post_saves_buffer(client, app):
    with app.app_context():
        account = setup_account(); add_transaction(account, date.today(), "10.00"); db.session.commit()
        before = (Transaction.query.count(), PaymentReconciliation.query.count(), HouseholdForecastSetting.query.count())
    response = client.get(f"/daily-health?date={date.today().isoformat()}")
    assert response.status_code == 200
    assert "Daily Financial Health" in response.get_data(as_text=True)
    with app.app_context():
        assert (Transaction.query.count(), PaymentReconciliation.query.count(), HouseholdForecastSetting.query.count()) == before
    client.post("/daily-health/settings", data={"safety_buffer": "123.45", "selected_date": date.today().isoformat()})
    with app.app_context():
        assert HouseholdForecastSetting.query.one().safety_buffer == Decimal("123.45")


def test_missing_account_data_is_insufficient(app):
    with app.app_context():
        result = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert result["state"] == "insufficient_data"
        assert result["data_confidence"]["level"] == "insufficient"


def test_selected_date_boundary_and_payday_budget(app):
    with app.app_context():
        account = setup_account()
        add_transaction(account, date(2026, 1, 9), "40.00")
        add_transaction(account, date(2026, 1, 10), "60.00")
        add_transaction(account, date(2026, 1, 11), "80.00")
        add_household_income(account, "200.00", date(2026, 1, 20))
        db.session.add(VariableBudget(display_name="Example Payday Budget", amount=Decimal("25.00"), frequency="payday", next_expected_date=date(2026, 1, 20), active=True))
        db.session.commit()
        selected = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert selected["balance"] == Decimal("100.00")
        assert any(row["name"] == "Example Payday Budget" and row["date"] == date(2026, 1, 20) for row in selected["outstanding_bills"])
        after = build_daily_financial_health(db.session, date(2026, 1, 21))
        assert after["balance"] == Decimal("180.00")


def test_stale_account_reduces_confidence(app):
    with app.app_context():
        account = setup_account()
        add_transaction(account, date(2025, 1, 1), "10.00")
        db.session.commit()
        result = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert result["data_confidence"]["level"] in {"low", "insufficient"}
        assert any("stale" in reason.lower() for reason in result["data_confidence"]["reasons"])


def test_balance_snapshot_and_overdraft_are_kept_separate(app):
    with app.app_context():
        account = setup_account()
        account.current_balance = Decimal("500.00")
        account.balance_as_of = date(2026, 1, 10)
        account.overdraft_limit = Decimal("1000.00")
        add_transaction(account, date(2026, 1, 11), "-25.00")
        db.session.commit()
        assert account_balance_at(db.session, account, date(2026, 1, 10)) == Decimal("500.00")
        position = household_balance_position(db.session, [account], date(2026, 1, 11))
        assert position["current_balance"] == Decimal("475.00")
        assert position["overdraft_limit"] == Decimal("1000.00")
        assert position["available_funds"] == Decimal("1475.00")


def test_topup_avoids_overdraft_but_payment_shortfall_uses_available_funds(app):
    with app.app_context():
        account = setup_account()
        account.current_balance = Decimal("500.00")
        account.balance_as_of = date(2026, 1, 10)
        account.overdraft_limit = Decimal("1000.00")
        add_transaction(account, date(2026, 1, 10), "0.00")
        add_household_income(account, "100.00", date(2026, 1, 20))
        db.session.add(PlannedCommitment(display_name="Example Payment", amount=Decimal("800.00"), frequency="one-off", next_expected_date=date(2026, 1, 15), active=True, commitment_type="bill"))
        db.session.commit()
        result = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert result["joint_account_current_balance"] == Decimal("500.00")
        assert result["joint_account_available_funds"] == Decimal("1500.00")
        assert result["required_contribution"] == Decimal("300.00")
        assert result["payment_shortfall"] == Decimal("0.00")
        assert result["state"] == "at_risk"

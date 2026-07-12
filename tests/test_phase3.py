import re
from datetime import date
from decimal import Decimal

from sqlalchemy import inspect

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import (
    Account, ImportBatch, IncomeSchedule, OneOffForecastEvent, PlannedCommitment,
    RecurringBill, SinkingFundProvision, StatementImport, Transaction, User,
)
from app.services.cashflow_forecast_service import build_cashflow_forecast, sinking_fund_recommendation


def forecast(incomes=(), bills=(), commitments=(), one_offs=(), opening="100.00", start=date(2026, 1, 1), end=date(2026, 2, 1), latest=date(2026, 1, 1)):
    return build_cashflow_forecast(opening_balance=Decimal(opening), start_date=start, end_date=end, income_schedules=list(incomes), recurring_bills=list(bills), planned_commitments=list(commitments), one_off_events=list(one_offs), latest_actual_date=latest)


def test_fortnightly_income_schedule_and_decimal_totals():
    income = IncomeSchedule(display_name="Example Income", account_id=1, amount=Decimal("100.10"), frequency="fortnightly", next_expected_date=date(2026, 1, 2), active=True)
    result = forecast(incomes=[income], end=date(2026, 1, 31))
    assert [event["date"] for event in result["events"]] == [date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30)]
    assert result["total_expected_income"] == Decimal("300.30")
    assert result["projected_closing_balance"] == Decimal("400.30")


def test_weekly_and_fortnightly_commitments_and_annual_projection():
    weekly = PlannedCommitment(display_name="Example Grocery Budget", amount=Decimal("20.00"), frequency="weekly", next_expected_date=date(2026, 1, 1), active=True, commitment_type="groceries")
    fortnightly = PlannedCommitment(display_name="Example Allowance", amount=Decimal("10.00"), frequency="fortnightly", next_expected_date=date(2026, 1, 1), active=True, commitment_type="allowance")
    annual = PlannedCommitment(display_name="Example Annual Charge", amount=Decimal("120.00"), frequency="annual", next_expected_date=date(2026, 6, 1), active=True, commitment_type="bill")
    short = forecast(commitments=[weekly, fortnightly], end=date(2026, 1, 15))
    assert short["total_expected_expenditure"] == Decimal("80.00")
    yearly = forecast(commitments=[annual], end=date(2027, 6, 1))
    assert [event["date"] for event in yearly["events"]] == [date(2026, 6, 1), date(2027, 6, 1)]


def test_chronological_running_balance_and_minimum_before_close():
    income = IncomeSchedule(display_name="Example Income", account_id=1, amount=Decimal("100.00"), frequency="irregular", next_expected_date=date(2026, 1, 10), active=True)
    expense = PlannedCommitment(display_name="Example Pet Food", amount=Decimal("80.00"), frequency="one-off", next_expected_date=date(2026, 1, 5), active=True, commitment_type="pet")
    result = forecast(incomes=[income], commitments=[expense], opening="50.00", end=date(2026, 1, 20))
    assert [event["running_balance"] for event in result["events"]] == [Decimal("-30.00"), Decimal("70.00")]
    assert result["minimum_projected_balance"] == Decimal("-30.00")
    assert result["minimum_balance_date"] == date(2026, 1, 5)
    assert result["balance_before_payday"] == Decimal("-30.00")


def test_one_off_expense_and_inactive_schedules_are_separate_from_transactions(app):
    with app.app_context():
        before = Transaction.query.count()
        active = OneOffForecastEvent(display_name="Example One-Off Expense", amount=Decimal("25.50"), event_date=date(2026, 1, 4), direction="expense", status="planned")
        inactive_income = IncomeSchedule(display_name="Inactive Income", account_id=1, amount=Decimal("999.00"), frequency="weekly", next_expected_date=date(2026, 1, 1), active=False)
        result = forecast(incomes=[inactive_income], one_offs=[active], end=date(2026, 1, 10))
        assert result["total_expected_expenditure"] == Decimal("25.50")
        assert result["total_expected_income"] == Decimal("0.00")
        assert Transaction.query.count() == before


def test_stale_data_and_missing_income_warnings():
    result = forecast(start=date(2026, 3, 1), end=date(2026, 3, 31), latest=date(2025, 12, 1))
    assert any("No active income" in warning for warning in result["warnings"])
    assert any("stale" in warning for warning in result["warnings"])


def test_sinking_fund_recommendation_is_estimated():
    provision = SinkingFundProvision(display_name="Example Annual Charge", target_amount=Decimal("500.00"), amount_reserved=Decimal("100.00"), due_date=date(2026, 1, 30), active=True)
    income = IncomeSchedule(display_name="Example Income", account_id=1, amount=Decimal("100.00"), frequency="fortnightly", next_expected_date=date(2026, 1, 2), active=True)
    result = sinking_fund_recommendation(provision, [income], date(2026, 1, 1))
    assert result["payday_count"] == 3
    assert result["recommended_per_payday"] == Decimal("133.34")
    assert result["label"] == "Estimated"


def test_commitment_create_edit_deactivate_delete(client, app):
    create = client.post("/forecast/commitments", data={"display_name": "Example Allowance", "amount": "20.00", "frequency": "weekly", "next_expected_date": "2026-08-01", "commitment_type": "allowance", "household_flag": "household", "active": "on"})
    assert create.status_code == 302
    with app.app_context():
        item = PlannedCommitment.query.one()
        item_id = item.id
    client.post(f"/forecast/commitments/{item_id}", data={"display_name": "Example Allowance Updated", "amount": "25.00", "frequency": "fortnightly", "next_expected_date": "2026-08-01", "commitment_type": "allowance", "household_flag": "household", "active": "on"})
    with app.app_context():
        assert db.session.get(PlannedCommitment, item_id).amount == Decimal("25.00")
    client.post(f"/forecast/commitments/{item_id}/toggle")
    with app.app_context():
        assert db.session.get(PlannedCommitment, item_id).active is False
    client.post(f"/forecast/commitments/{item_id}/delete")
    with app.app_context():
        assert PlannedCommitment.query.count() == 0


def test_get_routes_with_triggerable_maintenance_data_are_read_only(client, app):
    with app.app_context():
        user = User(name="Maintenance Test User")
        db.session.add(user)
        db.session.flush()
        wallet = Account(user_id=user.id, name="PayPal", account_type="wallet")
        db.session.add(wallet)
        db.session.flush()
        batch = ImportBatch(source_filename="legacy.csv", row_count=1)
        db.session.add(batch)
        db.session.flush()
        row = Transaction(account_id=wallet.id, import_batch_id=batch.id, posted_date=date(2026, 1, 1), original_description="PayPal Bank Deposit", cleaned_description="PayPal Bank Deposit", amount=Decimal("10.00"), notes="Status: Completed")
        db.session.add(row)
        db.session.commit()
        row_id = row.id
        before = (StatementImport.query.count(), row.excluded_from_analysis, row.notes)
    for path in ["/", "/imports", "/transactions", "/accounts", "/intelligence", "/forecast"]:
        assert client.get(path).status_code == 200
    with app.app_context():
        refreshed = db.session.get(Transaction, row_id)
        after = (StatementImport.query.count(), refreshed.excluded_from_analysis, refreshed.notes)
        assert after == before


def test_explicit_maintenance_actions_update_triggerable_legacy_data(client, app):
    with app.app_context():
        user = User(name="Explicit Maintenance User")
        db.session.add(user)
        db.session.flush()
        account = Account(user_id=user.id, name="Example Account", account_type="checking")
        batch = ImportBatch(source_filename="legacy-statement.csv", row_count=2)
        db.session.add_all([account, batch])
        db.session.flush()
        bank = Transaction(account_id=account.id, import_batch_id=batch.id, posted_date=date(2026, 1, 5), original_description="D/D PayPal Europe", cleaned_description="D/D PayPal Europe", amount=Decimal("-15.00"), notes="Type: Direct Debit")
        paypal = Transaction(account_id=account.id, import_batch_id=batch.id, posted_date=date(2026, 1, 5), original_description="PayPal Example Vendor", cleaned_description="PayPal Example Vendor | PreApproved Payment Bill User Payment", amount=Decimal("-15.00"), notes="Status: Completed")
        db.session.add_all([bank, paypal])
        db.session.commit()
        bank_id = bank.id
        batch_id = batch.id

    runner = app.test_cli_runner()
    result = runner.invoke(args=["backfill-paypal-descriptions"])
    assert result.exit_code == 0
    assert "1 transaction(s) updated" in result.output
    with app.app_context():
        assert "Alt Description: Example Vendor" in db.session.get(Transaction, bank_id).notes
        assert StatementImport.query.filter_by(import_batch_id=batch_id).first() is None

    response = client.post("/imports/amend-metadata")
    assert response.status_code == 302
    with app.app_context():
        assert StatementImport.query.filter_by(import_batch_id=batch_id).first() is not None


def test_csrf_blocks_unprotected_post_and_accepts_page_token(tmp_path):
    class CsrfConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{(tmp_path / 'csrf.sqlite3').as_posix()}"
        WTF_CSRF_ENABLED = True
        SECRET_KEY = "csrf-test-secret"

    app = create_app(CsrfConfig)
    with app.app_context():
        db.create_all()
    client = app.test_client()
    assert client.post("/accounts", data={"account_name": "Blocked"}).status_code == 400
    page = client.get("/accounts").get_data(as_text=True)
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
    response = client.post("/accounts", data={"account_name": "Allowed", "account_type": "checking", "csrf_token": token})
    assert response.status_code == 302


def test_migration_upgrade_from_empty_database(tmp_path):
    database_path = tmp_path / "migration.sqlite3"

    class MigrationConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path.as_posix()}"

    app = create_app(MigrationConfig)
    runner = app.test_cli_runner()
    result = runner.invoke(args=["db", "upgrade"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        tables = set(inspect(db.engine).get_table_names())
        assert {"account", "transaction", "income_schedule", "income_allocation", "contribution_reconciliation", "planned_commitment", "one_off_forecast_event", "sinking_fund_provision", "household_forecast_setting", "variable_budget", "payment_reconciliation"} <= tables

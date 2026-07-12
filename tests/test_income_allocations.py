from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import (
    Account, ContributionReconciliation, IncomeAllocation, IncomeSchedule,
    Transaction, User, VariableBudget,
)
from app.services.cashflow_forecast_service import build_cashflow_forecast
from app.services.daily_financial_health_service import build_daily_financial_health
from app.services.income_allocation_service import ad_hoc_contribution_candidates, contribution_occurrences, income_breakdown


def setup_accounts():
    user = User(name="Example User"); db.session.add(user); db.session.flush()
    household = Account(user_id=user.id, name="Example Household Account", account_type="checking")
    private = Account(user_id=user.id, name="Example Private Account", account_type="checking")
    db.session.add_all([household, private]); db.session.flush()
    return household, private


def schedule_with_allocations(account, total="2000.00", contribution="1000.00", status="confirmed"):
    schedule = IncomeSchedule(display_name="Example Income Source", account_id=account.id,
        amount=Decimal(total), frequency="fortnightly", next_expected_date=date(2026, 1, 20),
        active=True, availability_classification="contribution_only")
    db.session.add(schedule); db.session.flush()
    allocation = IncomeAllocation(income_schedule_id=schedule.id, allocation_type="household_contribution",
        amount=Decimal(contribution), destination_account_id=account.id, effective_from=date(2026, 1, 20),
        frequency="fortnightly", status=status, source_type="manual")
    db.session.add(allocation); db.session.flush()
    return schedule, allocation


def forecast(schedule):
    return build_cashflow_forecast(opening_balance=Decimal("0.00"), start_date=date(2026, 1, 20),
        end_date=date(2026, 1, 20), income_schedules=[schedule], recurring_bills=[],
        planned_commitments=[], one_off_events=[], latest_actual_date=date(2026, 1, 20),
        require_household_allocations=True)


def test_total_salary_reported_but_only_contribution_forecast(app):
    with app.app_context():
        household, _ = setup_accounts(); schedule, _ = schedule_with_allocations(household)
        result = forecast(schedule)
        assert result["total_recorded_income"] == Decimal("2000.00")
        assert result["forecastable_household_income"] == Decimal("1000.00")
        assert result["income_excluded_from_forecast"] == Decimal("1000.00")
        assert result["projected_closing_balance"] == Decimal("1000.00")
        assert schedule.amount == Decimal("2000.00")


def test_personal_and_savings_allocations_are_not_available_cash(app):
    with app.app_context():
        household, _ = setup_accounts(); schedule, _ = schedule_with_allocations(household, contribution="500.00")
        db.session.add_all([
            IncomeAllocation(income_schedule_id=schedule.id, allocation_type="personal", amount=Decimal("900.00"), effective_from=date(2026, 1, 1), frequency="fortnightly", status="confirmed", source_type="manual"),
            IncomeAllocation(income_schedule_id=schedule.id, allocation_type="savings", amount=Decimal("600.00"), effective_from=date(2026, 1, 1), frequency="fortnightly", status="confirmed", source_type="manual"),
        ]); db.session.flush()
        breakdown = income_breakdown(schedule, date(2026, 1, 20))
        assert breakdown["household"] == Decimal("500.00")
        assert forecast(schedule)["projected_closing_balance"] == Decimal("500.00")


def test_no_allocation_excludes_income_and_warns(app):
    with app.app_context():
        household, _ = setup_accounts()
        schedule = IncomeSchedule(display_name="Example Unallocated Income", account_id=household.id,
            amount=Decimal("900.00"), frequency="irregular", next_expected_date=date(2026, 1, 20), active=True)
        db.session.add(schedule); db.session.flush()
        result = forecast(schedule)
        assert result["forecastable_household_income"] == Decimal("0.00")
        assert result["projected_closing_balance"] == Decimal("0.00")
        assert any("no household contribution" in warning.lower() for warning in result["warnings"])


def test_estimated_contribution_reduces_confidence(app):
    with app.app_context():
        household, _ = setup_accounts(); schedule, allocation = schedule_with_allocations(household, status="estimated")
        db.session.add(Transaction(account_id=household.id, posted_date=date(2026, 1, 10), original_description="Example Opening", cleaned_description="Example Opening", amount=Decimal("100.00"), review_state="reviewed")); db.session.commit()
        estimated = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert any("estimated rather than confirmed" in reason for reason in estimated["data_confidence"]["reasons"])
        allocation.status = "confirmed"; db.session.commit()
        confirmed = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert not any("estimated rather than confirmed" in reason for reason in confirmed["data_confidence"]["reasons"])
        assert schedule.availability_classification == "contribution_only"


def test_actual_contribution_match_is_not_double_counted(app):
    with app.app_context():
        household, _ = setup_accounts(); schedule, allocation = schedule_with_allocations(household, total="200.00", contribution="100.00")
        incoming = Transaction(account_id=household.id, posted_date=date(2026, 1, 20), original_description="Example Transfer", cleaned_description="Example Transfer", amount=Decimal("100.00"), review_state="reviewed")
        db.session.add(incoming); db.session.flush()
        db.session.add(ContributionReconciliation(income_allocation_id=allocation.id, expected_date=date(2026, 1, 20), expected_amount=Decimal("100.00"), status="matched", matched_transaction_id=incoming.id)); db.session.commit()
        result = build_daily_financial_health(db.session, date(2026, 1, 20))
        assert result["joint_account_available_balance"] == Decimal("100.00")
        assert result["projected_pre_income_balance"] == Decimal("100.00")
        assert result["conservative_projected_balance"] == Decimal("200.00")
        assert result["household_contributions_received"] == Decimal("100.00")


def test_overdue_contribution_and_proposed_incoming_match(app):
    with app.app_context():
        household, _ = setup_accounts(); schedule, allocation = schedule_with_allocations(household, total="200.00", contribution="100.00")
        incoming = Transaction(account_id=household.id, posted_date=date(2026, 1, 21), original_description="Example Incoming", cleaned_description="Example Incoming", amount=Decimal("100.00"), review_state="reviewed")
        db.session.add(incoming); db.session.commit()
        rows = contribution_occurrences(db.session, [schedule], date(2026, 1, 20), date(2026, 1, 25), date(2026, 1, 25))
        assert rows[0]["status"] == "overdue"
        assert rows[0]["proposed_status"] == "matched"


def test_private_account_spending_is_not_subtracted_from_household_cash(app):
    with app.app_context():
        household, private = setup_accounts(); schedule, _ = schedule_with_allocations(household)
        db.session.add_all([
            Transaction(account_id=household.id, posted_date=date(2026, 1, 10), original_description="Example Household", cleaned_description="Example Household", amount=Decimal("500.00"), review_state="reviewed"),
            Transaction(account_id=private.id, posted_date=date(2026, 1, 10), original_description="Example Private", cleaned_description="Example Private", amount=Decimal("-400.00"), review_state="reviewed"),
            VariableBudget(display_name="Example Non-visible Household Cost", amount=Decimal("25.00"), frequency="weekly", next_expected_date=date(2026, 1, 12), active=True),
        ]); db.session.commit()
        result = build_daily_financial_health(db.session, date(2026, 1, 10))
        assert result["joint_account_available_balance"] == Decimal("500.00")
        assert result["estimated_household_spending"] > 0


def test_income_allocation_get_is_read_only(client, app):
    with app.app_context():
        household, _ = setup_accounts(); schedule, _ = schedule_with_allocations(household); db.session.commit()
        before = (IncomeAllocation.query.count(), ContributionReconciliation.query.count(), schedule.amount)
    assert client.get("/income-allocations").status_code == 200
    with app.app_context():
        schedule = IncomeSchedule.query.one()
        assert (IncomeAllocation.query.count(), ContributionReconciliation.query.count(), schedule.amount) == before


def test_ad_hoc_allocation_does_not_assume_fortnightly_topup(app):
    with app.app_context():
        household, _ = setup_accounts()
        schedule = IncomeSchedule(display_name="Example Irregular Contributor", account_id=household.id,
            amount=Decimal("800.00"), frequency="fortnightly", next_expected_date=date(2026, 1, 20),
            active=True, availability_classification="contribution_only")
        db.session.add(schedule); db.session.flush()
        db.session.add(IncomeAllocation(income_schedule_id=schedule.id, allocation_type="household_contribution",
            amount=None, percentage=None, destination_account_id=household.id, effective_from=date(2026, 1, 1),
            frequency="irregular", status="confirmed", source_type="manual")); db.session.flush()
        result = forecast(schedule)
        assert result["total_recorded_income"] == Decimal("800.00")
        assert result["forecastable_household_income"] == Decimal("0.00")
        assert result["events"] == []
        assert any("ad hoc" in warning.lower() for warning in result["warnings"])


def test_ad_hoc_incoming_transfer_requires_review_and_is_not_duplicated(app):
    with app.app_context():
        household, _ = setup_accounts()
        schedule = IncomeSchedule(display_name="Example Irregular Contributor", account_id=household.id,
            amount=Decimal("800.00"), frequency="fortnightly", next_expected_date=date(2026, 1, 20),
            active=True, availability_classification="contribution_only")
        db.session.add(schedule); db.session.flush()
        allocation = IncomeAllocation(income_schedule_id=schedule.id, allocation_type="household_contribution",
            destination_account_id=household.id, effective_from=date(2026, 1, 1), frequency="irregular",
            status="confirmed", source_type="manual")
        incoming = Transaction(account_id=household.id, posted_date=date(2026, 1, 18), original_description="Example Top Up",
            cleaned_description="Example Top Up", amount=Decimal("75.25"), review_state="reviewed")
        db.session.add_all([allocation, incoming]); db.session.commit()
        candidates = ad_hoc_contribution_candidates(db.session, [schedule], date(2026, 1, 1), date(2026, 1, 31))
        assert candidates[0]["transaction"].id == incoming.id
        db.session.add(ContributionReconciliation(income_allocation_id=allocation.id, expected_date=incoming.posted_date,
            expected_amount=incoming.amount, status="matched", matched_transaction_id=incoming.id)); db.session.commit()
        assert ad_hoc_contribution_candidates(db.session, [schedule], date(2026, 1, 1), date(2026, 1, 31)) == []
        result = build_daily_financial_health(db.session, date(2026, 1, 18))
        assert result["joint_account_available_balance"] == Decimal("75.25")
        assert result["household_contributions_received"] == Decimal("75.25")
        assert result["projected_pre_income_balance"] is None

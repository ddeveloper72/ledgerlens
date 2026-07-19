from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Account, ContributionReconciliation, IncomeAllocation, IncomeSchedule, Transaction, User
from app.services.contribution_reconciliation_service import add_match, reconciliation_amounts
from app.services.daily_financial_health_service import intervention_thresholds
from app.services.income_allocation_service import validate_allocation_totals


def _schedule(amount="1000.00"):
    user = User(name="Example User")
    account = Account(user=user, name="Example Household")
    schedule = IncomeSchedule(display_name="Example Income", account=account, amount=Decimal(amount),
                              frequency="monthly", next_expected_date=date(2026, 1, 1))
    db.session.add_all([user, account, schedule])
    db.session.flush()
    return account, schedule


def _allocation(schedule, amount=None, percentage=None, start=date(2026, 1, 1), end=None):
    row = IncomeAllocation(income_schedule=schedule, allocation_type="household_contribution",
                           amount=Decimal(amount) if amount else None,
                           percentage=Decimal(percentage) if percentage else None,
                           effective_from=start, effective_to=end, frequency="monthly",
                           status="confirmed", source_type="manual")
    db.session.add(row)
    return row


def test_multiple_partial_contribution_matches_and_overpayment(app):
    with app.app_context():
        account, schedule = _schedule()
        allocation = _allocation(schedule, amount="1000.00")
        reconciliation = ContributionReconciliation(income_allocation=allocation,
            expected_date=date(2026, 1, 1), expected_amount=Decimal("1000.00"))
        first = Transaction(account=account, posted_date=date(2026, 1, 1), amount=Decimal("400.00"),
                            original_description="Example contribution", cleaned_description="Example contribution")
        second = Transaction(account=account, posted_date=date(2026, 1, 2), amount=Decimal("700.00"),
                             original_description="Example contribution", cleaned_description="Example contribution")
        db.session.add_all([reconciliation, first, second]); db.session.flush()
        add_match(reconciliation, first)
        assert reconciliation_amounts(reconciliation)["outstanding_amount"] == Decimal("600.00")
        add_match(reconciliation, second)
        amounts = reconciliation_amounts(reconciliation)
        assert amounts["matched_amount"] == Decimal("1100.00")
        assert amounts["outstanding_amount"] == Decimal("0.00")
        assert reconciliation.status == "matched"


def test_mixed_overlapping_allocations_are_rejected(app):
    with app.app_context():
        _, schedule = _schedule()
        _allocation(schedule, amount="600.00")
        _allocation(schedule, percentage="50.00")
        with pytest.raises(ValueError, match="Active allocations total 1100.00"):
            validate_allocation_totals(schedule)


def test_non_overlapping_allocations_are_valid(app):
    with app.app_context():
        _, schedule = _schedule()
        _allocation(schedule, amount="1000.00", end=date(2026, 1, 31))
        _allocation(schedule, percentage="100.00", start=date(2026, 2, 1))
        assert validate_allocation_totals(schedule)


def test_three_intervention_thresholds_are_independent():
    values = intervention_thresholds(Decimal("-120.00"), Decimal("300.00"), Decimal("400.00"))
    assert values == {
        "payment_failure_prevention_amount": Decimal("0.00"),
        "overdraft_avoidance_amount": Decimal("120.00"),
        "safety_buffer_preservation_amount": Decimal("520.00"),
    }

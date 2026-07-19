from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import Category, HouseholdSpendingSummary, Transaction, VariableBudget
from app.services.forecast_calibration_service import compare_forecast_actual
from app.services.household_spending_summary_service import create_summary
from app.services.variable_budget_service import accept_calibration, calibration_suggestion


def test_forecast_variance_and_partial_observation_are_decimal():
    result = compare_forecast_actual("500.00", "565.00", date(2026, 1, 1), date(2026, 1, 3))
    assert result["variance_amount"] == Decimal("65.00")
    assert result["variance_percentage"] == Decimal("13.00")
    assert result["date_variance"] == 2
    assert result["match_status"] == "over_forecast"
    partial = compare_forecast_actual("80", None, date(2026, 1, 1), coverage="partial")
    assert partial["match_status"] == "partially_observed"
    assert partial["actual_amount"] is None


def test_household_summary_does_not_create_transaction(app):
    with app.app_context():
        before = Transaction.query.count()
        row = create_summary(db.session, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            category_name="Example groceries", amount="120.25", confidence="high")
        db.session.commit()
        assert row.reported_amount == Decimal("120.25")
        assert HouseholdSpendingSummary.query.count() == 1
        assert Transaction.query.count() == before


def test_budget_suggestion_requires_explicit_acceptance_and_keeps_history(app):
    with app.app_context():
        category = Category(name="Example household category"); db.session.add(category); db.session.flush()
        budget = VariableBudget(display_name="Example estimate", category_id=category.id, amount=Decimal("100.00"),
            frequency="monthly", next_expected_date=date(2026, 4, 1)); db.session.add(budget)
        for month, amount in ((1, "110"), (2, "120"), (3, "130")):
            create_summary(db.session, period_start=date(2026, month, 1), period_end=date(2026, month, 28),
                category_name=category.name, category_id=category.id, amount=amount)
        db.session.commit()
        suggestion = calibration_suggestion(db.session, budget)
        assert suggestion["suggested_amount"] == Decimal("120.00")
        assert budget.amount == Decimal("100.00")
        accept_calibration(db.session, budget, "120.00", date(2026, 4, 1), "Example observed history")
        db.session.commit()
        assert budget.amount == Decimal("120.00")
        assert budget.calibration_history[0].previous_value == Decimal("100.00")

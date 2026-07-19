from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from app.models import BudgetCalibrationHistory, HouseholdSpendingSummary
from app.services.money import parse_money


def calibration_suggestion(session, budget, periods=3, method="recent_average"):
    rows = session.query(HouseholdSpendingSummary).filter_by(category_id=budget.category_id).order_by(
        HouseholdSpendingSummary.period_end.desc()).limit(periods).all()
    values = [Decimal(row.reported_amount) for row in rows]
    if not values:
        return None
    raw = Decimal(str(median(values))) if method == "recent_median" else sum(values, Decimal("0")) / Decimal(len(values))
    suggested = raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(Decimal("0.01"))
    return {"current_amount": Decimal(budget.amount), "observed_amount": raw.quantize(Decimal("0.01")),
            "suggested_amount": suggested, "method": method, "period_count": len(values)}


def accept_calibration(session, budget, new_amount, effective_date, reason, source="accepted_suggestion"):
    value = parse_money(str(new_amount))
    history = BudgetCalibrationHistory(variable_budget_id=budget.id, previous_value=budget.amount,
        new_value=value, effective_date=effective_date, change_source=source, reason=reason)
    budget.amount = value
    session.add(history)
    return history

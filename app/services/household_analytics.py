from decimal import Decimal

from app.models import Transaction
from app.services.period_service import apply_transaction_period

ESSENTIAL_CATEGORIES = {"Groceries", "Utilities", "Transport", "Loan Payments", "Transfers"}


def household_analytics_snapshot(session, period):
    """Compute household KPIs from the same bounded period used by the dashboard."""
    rows = apply_transaction_period(session.query(Transaction).filter(Transaction.excluded_from_analysis.is_(False)), period, Transaction).all()
    totals = {
        "groceries": Decimal("0"), "technology": Decimal("0"), "car": Decimal("0"),
        "pet": Decimal("0"), "allowance": Decimal("0"), "essential": Decimal("0"),
        "discretionary": Decimal("0"),
    }
    months = set()
    for txn in rows:
        amount = abs(Decimal(txn.amount)) if txn.amount < 0 else Decimal("0")
        months.add((txn.posted_date.year, txn.posted_date.month))
        category = txn.category.name if txn.category else "Uncategorized"
        desc = (txn.cleaned_description or "").lower()
        merchant = txn.merchant.name.lower() if txn.merchant else ""
        if category == "Groceries": totals["groceries"] += amount
        if "subscription" in category.lower() and any(key in f"{merchant} {desc}" for key in ["microsoft", "google", "github", "spotify"]): totals["technology"] += amount
        if any(key in desc for key in ["fuel", "car", "motor", "parking"]): totals["car"] += amount
        if any(key in desc for key in ["dog", "pet", "vet"]): totals["pet"] += amount
        if "allowance" in desc: totals["allowance"] += amount
        totals["essential" if category in ESSENTIAL_CATEGORIES else "discretionary"] += amount
    month_count = Decimal(max(len(months), 1))
    return {
        "avg_monthly_groceries": (totals["groceries"] / month_count).quantize(Decimal("0.01")),
        "technology_subscriptions_yearly": totals["technology"],
        "car_cost_12m": totals["car"], "pet_cost_12m": totals["pet"],
        "allowances_12m": totals["allowance"], "essential_spend": totals["essential"],
        "discretionary_spend": totals["discretionary"],
    }

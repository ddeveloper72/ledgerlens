import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.models import Category, Merchant, MerchantAlias, RecurringBill, SavingsGoal, Transaction


ESSENTIAL_CATEGORIES = {
    "Groceries",
    "Utilities",
    "Transport",
    "Loan Payments",
    "Transfers",
}


def canonical_merchant_hint(description):
    """Convert noisy bank description text into a reusable merchant alias hint."""
    value = (description or "").strip().lower()
    value = re.sub(r"^(d/d|card|pos)\s+", "", value)
    value = re.sub(r"^(paypal)\s*[\*-]?\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    return value[:120]


def ensure_category(session, category_name):
    """Get or create a category record by display name."""
    name = (category_name or "Uncategorized").strip() or "Uncategorized"
    category = session.query(Category).filter_by(name=name).first()
    if category:
        return category

    category = Category(name=name)
    session.add(category)
    session.flush()
    return category


def ensure_merchant_with_alias(session, alias_text, merchant_name):
    """Ensure merchant and alias mapping exist for future automatic imports."""
    merchant = session.query(Merchant).filter_by(name=merchant_name.strip()).first()
    if not merchant:
        merchant = Merchant(name=merchant_name.strip())
        session.add(merchant)
        session.flush()

    alias_value = canonical_merchant_hint(alias_text)
    alias = session.query(MerchantAlias).filter_by(alias=alias_value).first()
    if alias:
        alias.merchant_id = merchant.id
    else:
        alias = MerchantAlias(alias=alias_value, merchant_id=merchant.id)
        session.add(alias)

    session.flush()
    return merchant


def infer_financial_labels(merchant_name, category_name, description):
    """Infer domain, subtype, and recurrence hints from known merchant/category context."""
    merchant_text = (merchant_name or "").lower()
    category_text = (category_name or "Uncategorized").lower()
    description_text = (description or "").lower()

    domain = "General"
    subtype = "Variable"
    recurrence = "Ad hoc"

    if "microsoft" in merchant_text or "google" in merchant_text or "github" in merchant_text:
        domain = "Technology"
    elif "spotify" in merchant_text or "broadband" in description_text or "mobile" in description_text:
        domain = "Subscriptions"
    elif "mortgage" in description_text or "loan" in category_text:
        domain = "Debt"
    elif "insurance" in description_text:
        domain = "Insurance"

    if "subscription" in category_text or "subscription" in description_text:
        subtype = "Subscription"
        recurrence = "Recurring Monthly"
    elif "mortgage" in description_text:
        subtype = "Mortgage"
        recurrence = "Recurring Monthly"
    elif "electric" in description_text or "utility" in category_text:
        subtype = "Utilities"
        recurrence = "Recurring Monthly"

    return {
        "domain": domain,
        "subtype": subtype,
        "recurrence": recurrence,
    }


def apply_mapping_to_pending_transactions(session, alias_text, merchant_name, category_name):
    """Apply a learned alias/merchant/category mapping to uncategorized pending transactions."""
    merchant = ensure_merchant_with_alias(session, alias_text, merchant_name)
    category = ensure_category(session, category_name)
    alias_value = canonical_merchant_hint(alias_text)

    updated = 0
    candidates = (
        session.query(Transaction)
        .filter(Transaction.review_state == "pending")
        .all()
    )

    for txn in candidates:
        haystack = canonical_merchant_hint(txn.cleaned_description)
        if alias_value not in haystack:
            continue

        txn.merchant_id = merchant.id
        txn.category_id = category.id
        updated += 1

    session.flush()
    return updated


def detect_recurring_candidates(session, months_back=12, min_occurrences=3):
    """Detect likely recurring outgoing merchants and return expected schedule metadata."""
    rows = (
        session.query(Transaction)
        .filter(Transaction.amount < 0, Transaction.merchant_id.isnot(None))
        .order_by(Transaction.posted_date.asc())
        .all()
    )

    grouped = defaultdict(list)
    for txn in rows:
        grouped[txn.merchant_id].append(txn)

    candidates = []
    for merchant_id, txns in grouped.items():
        month_keys = {(txn.posted_date.year, txn.posted_date.month) for txn in txns}
        if len(txns) < min_occurrences or len(month_keys) < min_occurrences:
            continue

        amounts = sorted([abs(Decimal(txn.amount)) for txn in txns])
        days = sorted([txn.posted_date.day for txn in txns])
        median_amount = amounts[len(amounts) // 2]
        median_day = days[len(days) // 2]
        merchant_name = txns[-1].merchant.name if txns[-1].merchant else "Unknown"
        category_name = txns[-1].category.name if txns[-1].category else "Uncategorized"

        candidates.append(
            {
                "merchant_id": merchant_id,
                "merchant_name": merchant_name,
                "category_name": category_name,
                "expected_amount": median_amount,
                "expected_day": median_day,
                "occurrences": len(txns),
            }
        )

    return sorted(candidates, key=lambda item: item["occurrences"], reverse=True)


def sync_recurring_bills(session):
    """Persist recurring candidate metadata into RecurringBill records."""
    candidates = detect_recurring_candidates(session)
    for item in candidates:
        bill = session.query(RecurringBill).filter_by(merchant_id=item["merchant_id"]).first()
        if not bill:
            bill = RecurringBill(
                merchant_id=item["merchant_id"],
                cadence="monthly",
            )
            session.add(bill)

        category = session.query(Category).filter_by(name=item["category_name"]).first()
        bill.category_id = category.id if category else None
        bill.expected_amount = item["expected_amount"]
        bill.cadence = "monthly"

    session.flush()
    return candidates


def recurring_expected_vs_missing(session, today=None):
    """Return recurring merchants expected this month and those not yet observed."""
    today = today or date.today()
    candidates = detect_recurring_candidates(session)
    expected = []
    missing = []

    for item in candidates:
        seen = (
            session.query(Transaction)
            .filter(
                Transaction.merchant_id == item["merchant_id"],
                Transaction.posted_date >= today.replace(day=1),
                Transaction.posted_date <= today,
            )
            .first()
        )

        record = {
            "merchant_name": item["merchant_name"],
            "expected_day": item["expected_day"],
            "expected_amount": item["expected_amount"],
            "category_name": item["category_name"],
        }
        expected.append(record)
        if not seen and today.day >= item["expected_day"]:
            missing.append(record)

    return {
        "expected": sorted(expected, key=lambda item: item["expected_day"]),
        "missing": sorted(missing, key=lambda item: item["expected_day"]),
    }


def cash_flow_calendar(session, today=None):
    """Build a simple inflow/outflow timeline for the current month."""
    today = today or date.today()
    month_start = today.replace(day=1)
    rows = (
        session.query(Transaction)
        .filter(Transaction.posted_date >= month_start, Transaction.posted_date <= today)
        .order_by(Transaction.posted_date.asc(), Transaction.id.asc())
        .all()
    )

    calendar = []
    for txn in rows:
        calendar.append(
            {
                "date": txn.posted_date,
                "label": txn.cleaned_description,
                "amount": Decimal(txn.amount),
                "flow": "in" if Decimal(txn.amount) > 0 else "out",
            }
        )

    return calendar


def savings_recovery_summary(session):
    """Compute emergency-fund recovery progress from SavingsGoal records."""
    goal = (
        session.query(SavingsGoal)
        .filter(SavingsGoal.name.ilike("%emergency%"))
        .order_by(SavingsGoal.id.asc())
        .first()
    )
    if not goal:
        return None

    current_amount = Decimal(goal.current_amount)
    target_amount = Decimal(goal.target_amount)
    gap = max(target_amount - current_amount, Decimal("0.00"))
    progress = Decimal("0.00")
    if target_amount > 0:
        progress = ((current_amount / target_amount) * Decimal("100.00")).quantize(Decimal("0.01"))

    return {
        "goal_name": goal.name,
        "current_amount": current_amount,
        "target_amount": target_amount,
        "gap": gap,
        "progress_percent": progress,
        "target_date": goal.target_date,
    }


def household_analytics_snapshot(session):
    """Produce baseline household analytics KPIs from transaction history."""
    rows = session.query(Transaction).all()
    if not rows:
        return {
            "avg_monthly_groceries": Decimal("0.00"),
            "technology_subscriptions_yearly": Decimal("0.00"),
            "car_cost_12m": Decimal("0.00"),
            "pet_cost_12m": Decimal("0.00"),
            "allowances_12m": Decimal("0.00"),
            "essential_spend": Decimal("0.00"),
            "discretionary_spend": Decimal("0.00"),
        }

    groceries_total = Decimal("0.00")
    technology_total = Decimal("0.00")
    car_total = Decimal("0.00")
    pet_total = Decimal("0.00")
    allowance_total = Decimal("0.00")
    essential_total = Decimal("0.00")
    discretionary_total = Decimal("0.00")

    month_keys = set()

    for txn in rows:
        amount = abs(Decimal(txn.amount)) if Decimal(txn.amount) < 0 else Decimal("0.00")
        month_keys.add((txn.posted_date.year, txn.posted_date.month))
        category_name = txn.category.name if txn.category else "Uncategorized"
        desc = (txn.cleaned_description or "").lower()
        merchant_name = txn.merchant.name.lower() if txn.merchant else ""

        if category_name == "Groceries":
            groceries_total += amount

        if "subscription" in category_name.lower() and (
            any(key in merchant_name for key in ["microsoft", "google", "github", "spotify"])
            or any(key in desc for key in ["microsoft", "google", "github", "spotify"])
        ):
            technology_total += amount

        if any(key in desc for key in ["fuel", "car", "motor", "parking"]):
            car_total += amount

        if any(key in desc for key in ["dog", "pet", "vet"]):
            pet_total += amount

        if "allowance" in desc:
            allowance_total += amount

        if category_name in ESSENTIAL_CATEGORIES:
            essential_total += amount
        else:
            discretionary_total += amount

    month_count = max(len(month_keys), 1)
    avg_groceries = (groceries_total / Decimal(month_count)).quantize(Decimal("0.01"))

    return {
        "avg_monthly_groceries": avg_groceries,
        "technology_subscriptions_yearly": technology_total,
        "car_cost_12m": car_total,
        "pet_cost_12m": pet_total,
        "allowances_12m": allowance_total,
        "essential_spend": essential_total,
        "discretionary_spend": discretionary_total,
    }

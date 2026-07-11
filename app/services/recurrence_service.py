from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median

from app.models import Category, RecurringBill, RecurringCandidate, Transaction

FREQUENCIES = ("weekly", "fortnightly", "monthly", "quarterly", "annual", "irregular")
FREQUENCY_DAYS = {"weekly": 7, "fortnightly": 14, "monthly": 30, "quarterly": 91, "annual": 365}


def classify_frequency(intervals):
    """Classify a series of day gaps with practical calendar tolerances."""
    if not intervals:
        return "irregular"
    typical = Decimal(str(median(intervals)))
    bands = {
        "weekly": (Decimal("5"), Decimal("9")),
        "fortnightly": (Decimal("11"), Decimal("17")),
        "monthly": (Decimal("25"), Decimal("35")),
        "quarterly": (Decimal("75"), Decimal("105")),
        "annual": (Decimal("330"), Decimal("400")),
    }
    return next((name for name, (low, high) in bands.items() if low <= typical <= high), "irregular")


def detect_recurring_candidates(session, min_occurrences=3):
    """Return evidence-rich suggestions without writing to the database."""
    rows = (
        session.query(Transaction)
        .filter(Transaction.amount < 0, Transaction.excluded_from_analysis.is_(False))
        .order_by(Transaction.posted_date.asc(), Transaction.id.asc())
        .all()
    )
    grouped = defaultdict(list)
    for txn in rows:
        key = ("merchant", txn.merchant_id) if txn.merchant_id else ("description", txn.cleaned_description.lower())
        grouped[key].append(txn)
    suggestions = []
    for _key, txns in grouped.items():
        if len(txns) < min_occurrences:
            continue
        dates = sorted(txn.posted_date for txn in txns)
        intervals = [(right - left).days for left, right in zip(dates, dates[1:])]
        frequency = classify_frequency(intervals)
        if frequency == "irregular":
            continue
        amounts = [abs(Decimal(txn.amount)).quantize(Decimal("0.01")) for txn in txns]
        typical_amount = Decimal(median(amounts)).quantize(Decimal("0.01"))
        variation = (max(amounts) - min(amounts)).quantize(Decimal("0.01"))
        typical_days = FREQUENCY_DAYS[frequency]
        interval_deviation = sum(abs(value - typical_days) for value in intervals) / len(intervals)
        timing_score = max(Decimal("0"), Decimal("1") - Decimal(str(interval_deviation / max(typical_days, 1))))
        amount_score = Decimal("1") if typical_amount == 0 else max(
            Decimal("0"), Decimal("1") - (variation / typical_amount)
        )
        confidence = ((timing_score * Decimal("0.75") + amount_score * Decimal("0.25")) * 100).quantize(Decimal("0.01"))
        sample = txns[-1]
        merchant_name = sample.merchant.name if sample.merchant else sample.cleaned_description
        suggestions.append(
            {
                "merchant_id": sample.merchant_id,
                "normalized_description": sample.cleaned_description.lower()[:120],
                "display_name": merchant_name,
                "category_id": sample.category_id,
                "observed_count": len(txns),
                "first_observed_date": dates[0],
                "last_observed_date": dates[-1],
                "typical_amount": typical_amount,
                "amount_variation": variation,
                "frequency": frequency,
                "estimated_next_date": dates[-1] + timedelta(days=typical_days),
                "confidence_score": confidence,
                "amount_tolerance": variation,
                "household_flag": sample.household_flag,
            }
        )
    return sorted(suggestions, key=lambda item: (item["confidence_score"], item["observed_count"]), reverse=True)


def refresh_candidates(session):
    """Persist current suggestions as pending candidates after explicit user action."""
    created = updated = 0
    for item in detect_recurring_candidates(session):
        query = session.query(RecurringCandidate)
        candidate = query.filter_by(merchant_id=item["merchant_id"]).first() if item["merchant_id"] else query.filter_by(normalized_description=item["normalized_description"]).first()
        if not candidate:
            candidate = RecurringCandidate(status="pending", **item)
            session.add(candidate)
            created += 1
        elif candidate.status == "pending":
            for key, value in item.items():
                setattr(candidate, key, value)
            updated += 1
    session.flush()
    return created, updated


def reject_candidate(candidate):
    candidate.status = "rejected"
    candidate.reviewed_at = datetime.now()


def confirm_candidate(session, candidate, values):
    """Confirm edited evidence into a recurring bill; never called during detection."""
    candidate.display_name = values["display_name"]
    candidate.category_id = values.get("category_id")
    candidate.frequency = values["frequency"]
    candidate.typical_amount = values["expected_amount"]
    candidate.amount_tolerance = values["amount_tolerance"]
    candidate.estimated_next_date = values.get("expected_next_date")
    candidate.household_flag = values["household_flag"]
    candidate.active = values["active"]
    candidate.status = "confirmed"
    candidate.reviewed_at = datetime.now()
    bill = session.query(RecurringBill).filter_by(merchant_id=candidate.merchant_id).first()
    if not bill:
        bill = RecurringBill(merchant_id=candidate.merchant_id)
        session.add(bill)
    bill.category_id = candidate.category_id
    bill.display_name = candidate.display_name
    bill.cadence = candidate.frequency
    bill.expected_amount = candidate.typical_amount
    bill.amount_tolerance = candidate.amount_tolerance
    bill.expected_next_date = candidate.estimated_next_date
    bill.household_flag = candidate.household_flag
    bill.active = candidate.active
    session.flush()
    return bill


def recurring_expected_vs_missing(session, today=None):
    """Report only user-confirmed active bills as expected or missing."""
    today = today or date.today()
    expected, missing = [], []
    for bill in session.query(RecurringBill).filter_by(active=True).all():
        expected_date = bill.expected_next_date
        record = {
            "merchant_name": bill.display_name or (bill.merchant.name if bill.merchant else "Recurring payment"),
            "expected_day": expected_date.day if expected_date else 1,
            "expected_amount": Decimal(bill.expected_amount or 0),
            "category_name": bill.category.name if bill.category else "Uncategorized",
        }
        expected.append(record)
        if expected_date and expected_date < today:
            missing.append(record)
    return {"expected": expected, "missing": missing}

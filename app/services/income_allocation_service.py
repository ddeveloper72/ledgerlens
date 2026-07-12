from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.models import ContributionReconciliation, Transaction
from app.services.cashflow_forecast_service import occurrence_dates

ALLOCATION_TYPES = ("household_contribution", "personal", "savings", "unknown")
ALLOCATION_STATUSES = ("estimated", "confirmed", "actual", "inactive")
SOURCE_TYPES = ("manual", "inferred", "imported")
AVAILABILITY_CLASSES = ("fully_available", "contribution_only", "summary_only", "not_available")
RECONCILIATION_STATUSES = ("expected", "matched", "partially_matched", "overdue", "skipped", "cancelled")


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def allocation_amount(allocation, total_income):
    """Return a fixed or percentage allocation without exceeding expected income."""
    if allocation.amount is not None:
        value = money(allocation.amount)
    elif allocation.percentage is not None:
        value = (money(total_income) * Decimal(allocation.percentage) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        value = Decimal("0.00")
    return min(max(value, Decimal("0.00")), money(total_income))


def active_allocations(schedule, event_date, allocation_type=None):
    rows = []
    for allocation in schedule.allocations:
        if allocation.status == "inactive" or event_date < allocation.effective_from:
            continue
        if allocation.effective_to and event_date > allocation.effective_to:
            continue
        if allocation_type and allocation.allocation_type != allocation_type:
            continue
        rows.append(allocation)
    return rows


def income_occurrences(schedule, start_date, end_date):
    return occurrence_dates(schedule.next_expected_date, schedule.frequency, start_date, end_date)


def income_breakdown(schedule, event_date):
    total = money(schedule.amount)
    household = sum((allocation_amount(row, total) for row in active_allocations(schedule, event_date, "household_contribution")), Decimal("0.00"))
    personal = sum((allocation_amount(row, total) for row in active_allocations(schedule, event_date, "personal")), Decimal("0.00"))
    savings = sum((allocation_amount(row, total) for row in active_allocations(schedule, event_date, "savings")), Decimal("0.00"))
    known = min(total, household + personal + savings)
    return {"total": total, "household": min(household, total), "personal": personal, "savings": savings, "unallocated": max(total - known, Decimal("0.00"))}


def proposed_contribution_match(session, allocation, expected_date, expected_amount, tolerance=Decimal("0.01")):
    query = session.query(Transaction).filter(
        Transaction.amount > 0,
        Transaction.excluded_from_analysis.is_(False),
        Transaction.posted_date >= expected_date - timedelta(days=5),
        Transaction.posted_date <= expected_date + timedelta(days=5),
    )
    if allocation.destination_account_id:
        query = query.filter(Transaction.account_id == allocation.destination_account_id)
    candidates = sorted(query.all(), key=lambda row: (abs(money(row.amount) - expected_amount), abs((row.posted_date - expected_date).days)))
    if not candidates:
        return None, None
    row = candidates[0]
    difference = abs(money(row.amount) - expected_amount)
    if difference <= tolerance:
        return row, "matched"
    if money(row.amount) < expected_amount:
        return row, "partially_matched"
    return None, None


def contribution_occurrences(session, schedules, start_date, end_date, selected_date=None):
    """Build read-only contribution evidence and proposed matches."""
    selected_date = selected_date or start_date
    saved_rows = session.query(ContributionReconciliation).all()
    saved = {(row.income_allocation_id, row.expected_date): row for row in saved_rows}
    results = []
    for schedule in schedules:
        if not schedule.active:
            continue
        for event_date in income_occurrences(schedule, start_date, end_date):
            breakdown = income_breakdown(schedule, event_date)
            allocations = active_allocations(schedule, event_date, "household_contribution")
            for allocation in allocations:
                amount = allocation_amount(allocation, schedule.amount)
                reconciliation = saved.get((allocation.id, event_date))
                status = reconciliation.status if reconciliation else ("overdue" if event_date < selected_date else "expected")
                proposed, proposed_status = proposed_contribution_match(session, allocation, event_date, amount)
                results.append({"schedule": schedule, "allocation": allocation, "date": event_date, "total_income": breakdown["total"], "amount": amount, "status": status, "matched_transaction": reconciliation.matched_transaction if reconciliation else None, "proposed_transaction": proposed, "proposed_status": proposed_status})
    return results

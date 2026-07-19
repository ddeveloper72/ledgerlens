from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.models import ContributionMatch, ContributionReconciliation, Transaction
from app.services.cashflow_forecast_service import occurrence_dates
from app.services.contribution_reconciliation_service import reconciliation_amounts, reconciliation_status

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
    household = sum((allocation_amount(row, total) for row in active_allocations(schedule, event_date, "household_contribution") if row.frequency != "irregular"), Decimal("0.00"))
    personal = sum((allocation_amount(row, total) for row in active_allocations(schedule, event_date, "personal")), Decimal("0.00"))
    savings = sum((allocation_amount(row, total) for row in active_allocations(schedule, event_date, "savings")), Decimal("0.00"))
    known = min(total, household + personal + savings)
    return {"total": total, "household": min(household, total), "personal": personal, "savings": savings, "unallocated": max(total - known, Decimal("0.00"))}


def validate_allocation_totals(schedule, candidate=None):
    """Reject active fixed/percentage allocations that overlap above gross pay."""
    rows = [row for row in schedule.allocations if row.status != "inactive" and row is not candidate]
    if candidate is not None and candidate.status != "inactive":
        rows.append(candidate)
    for row in rows:
        if row.percentage is not None and not Decimal("0") <= Decimal(row.percentage) <= Decimal("100"):
            raise ValueError("Allocation percentages must be between 0 and 100.")
    boundaries = {row.effective_from for row in rows}
    boundaries.update(row.effective_to + timedelta(days=1) for row in rows if row.effective_to)
    for event_date in sorted(boundaries):
        active = [row for row in rows if row.effective_from <= event_date and (row.effective_to is None or event_date <= row.effective_to)]
        total = sum((allocation_amount(row, schedule.amount) for row in active), Decimal("0.00"))
        expected = money(schedule.amount)
        if total > expected:
            raise ValueError(f"Active allocations total {total:.2f}, but expected income is {expected:.2f}. Reduce one or more allocations before saving.")
    return True


def proposed_contribution_match(session, allocation, expected_date, expected_amount, tolerance=Decimal("0.01")):
    query = session.query(Transaction).filter(
        Transaction.amount > 0,
        Transaction.excluded_from_analysis.is_(False),
        Transaction.posted_date >= expected_date - timedelta(days=5),
        Transaction.posted_date <= expected_date + timedelta(days=5),
        ~Transaction.id.in_(session.query(ContributionMatch.transaction_id)),
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
        allocations = [row for row in schedule.allocations if row.allocation_type == "household_contribution" and row.status != "inactive"]
        for allocation in allocations:
            if allocation.frequency == "irregular":
                dates = [row.expected_date for row in saved_rows if row.income_allocation_id == allocation.id and start_date <= row.expected_date <= end_date]
            else:
                dates = occurrence_dates(allocation.effective_from, allocation.frequency, start_date, end_date, allocation.effective_to)
            for event_date in dates:
                amount = allocation_amount(allocation, schedule.amount)
                reconciliation = saved.get((allocation.id, event_date))
                if allocation.frequency == "irregular" and reconciliation:
                    amount = money(reconciliation.expected_amount)
                status = reconciliation_status(reconciliation, selected_date) if reconciliation else ("overdue" if event_date < selected_date else "expected")
                amounts = reconciliation_amounts(reconciliation) if reconciliation else {"expected_amount": amount, "matched_amount": Decimal("0.00"), "outstanding_amount": amount}
                proposed, proposed_status = proposed_contribution_match(session, allocation, event_date, amount)
                results.append({"schedule": schedule, "allocation": allocation, "date": event_date, "total_income": money(schedule.amount), "amount": amount, **amounts, "status": status, "matched_transaction": reconciliation.matched_transaction if reconciliation else None, "proposed_transaction": proposed, "proposed_status": proposed_status})
    return results


def ad_hoc_contribution_candidates(session, schedules, start_date, end_date):
    """Return unmatched incoming rows for explicit review against irregular allocations."""
    matched_ids = {row.matched_transaction_id for row in session.query(ContributionReconciliation).filter(ContributionReconciliation.matched_transaction_id.isnot(None)).all()}
    results = []
    for schedule in schedules:
        for allocation in schedule.allocations:
            if allocation.allocation_type != "household_contribution" or allocation.frequency != "irregular" or allocation.status == "inactive" or not allocation.destination_account_id:
                continue
            rows = session.query(Transaction).filter(
                Transaction.account_id == allocation.destination_account_id,
                Transaction.amount > 0,
                Transaction.excluded_from_analysis.is_(False),
                Transaction.posted_date >= max(start_date, allocation.effective_from),
                Transaction.posted_date <= min(end_date, allocation.effective_to or end_date),
            ).order_by(Transaction.posted_date.desc(), Transaction.id.desc()).all()
            for transaction in rows:
                if transaction.id not in matched_ids:
                    results.append({"schedule": schedule, "allocation": allocation, "transaction": transaction})
    return results

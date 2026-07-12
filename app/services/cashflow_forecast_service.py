from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def _add_months(value, months):
    month_index = value.year * 12 + value.month - 1 + months
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _next_date(value, frequency):
    if frequency == "weekly":
        return value + timedelta(days=7)
    if frequency == "fortnightly":
        return value + timedelta(days=14)
    if frequency == "monthly":
        return _add_months(value, 1)
    if frequency == "quarterly":
        return _add_months(value, 3)
    if frequency == "annual":
        return _add_months(value, 12)
    return None


def occurrence_dates(first_date, frequency, start_date, end_date, item_end_date=None):
    """Expand a schedule into bounded occurrence dates with calendar-aware frequencies."""
    if not first_date or first_date > end_date:
        return []
    current = first_date
    dates = []
    while current < start_date:
        current = _next_date(current, frequency)
        if current is None:
            return []
    while current <= end_date and (item_end_date is None or current <= item_end_date):
        dates.append(current)
        current = _next_date(current, frequency)
        if current is None:
            break
    return dates


def build_cashflow_forecast(
    *, opening_balance, start_date, end_date, income_schedules, recurring_bills,
    planned_commitments, one_off_events, latest_actual_date=None, stale_days=45,
    require_household_allocations=False,
):
    """Build an ordered estimated cash-flow projection without creating transactions."""
    opening = _money(opening_balance)
    events = []
    total_recorded_income = Decimal("0.00")
    forecastable_income = Decimal("0.00")
    excluded_income = Decimal("0.00")
    allocation_warnings = []
    for schedule in income_schedules:
        if not schedule.active:
            continue
        for event_date in occurrence_dates(schedule.next_expected_date, schedule.frequency, start_date, end_date):
            gross = _money(schedule.amount)
            total_recorded_income += gross
            if require_household_allocations:
                from app.services.income_allocation_service import income_breakdown
                contribution = income_breakdown(schedule, event_date)["household"]
                if contribution == 0:
                    irregular = any(row.allocation_type == "household_contribution" and row.frequency == "irregular" and row.status != "inactive" for row in schedule.allocations)
                    allocation_warnings.append(f"{schedule.display_name} uses ad hoc household contributions; no future top-up is assumed." if irregular else f"{schedule.display_name} has no household contribution allocation; its income is excluded from available cash.")
            else:
                contribution = gross
            forecastable_income += contribution
            excluded_income += gross - contribution
            if contribution:
                events.append({"date": event_date, "display_name": f"{schedule.display_name} household contribution", "amount": contribution, "direction": "income", "source": "Household income allocation", "label": "Forecast"})
    for bill in recurring_bills:
        if not bill.active or not bill.expected_next_date or bill.expected_amount is None:
            continue
        for event_date in occurrence_dates(bill.expected_next_date, bill.cadence, start_date, end_date):
            events.append({"date": event_date, "display_name": bill.display_name or (bill.merchant.name if bill.merchant else "Recurring bill"), "amount": -_money(bill.expected_amount), "direction": "expense", "source": "Confirmed recurring bill", "label": "Forecast"})
    for commitment in planned_commitments:
        if not commitment.active:
            continue
        for event_date in occurrence_dates(commitment.next_expected_date, commitment.frequency, start_date, end_date, commitment.end_date):
            events.append({"date": event_date, "display_name": commitment.display_name, "amount": -_money(commitment.amount), "direction": "expense", "source": "Planned commitment", "label": "Forecast"})
    for event in one_off_events:
        if event.status != "planned" or not start_date <= event.event_date <= end_date:
            continue
        amount = _money(event.amount)
        signed = amount if event.direction == "income" else -amount
        events.append({"date": event.event_date, "display_name": event.display_name, "amount": signed, "direction": event.direction, "source": "One-off event", "label": "Forecast"})

    # Expenses precede income on the same date to expose the conservative intraday minimum.
    events.sort(key=lambda item: (item["date"], 0 if item["amount"] < 0 else 1, item["display_name"]))
    running = opening
    minimum = opening
    minimum_date = start_date
    total_income = Decimal("0.00")
    total_expense = Decimal("0.00")
    next_income_date = next((item["date"] for item in events if item["amount"] > 0), None)
    before_payday_balance = opening
    for item in events:
        if next_income_date and item["date"] == next_income_date and item["amount"] > 0:
            before_payday_balance = running
        running += item["amount"]
        item["running_balance"] = running.quantize(Decimal("0.01"))
        if item["amount"] > 0:
            total_income += item["amount"]
        else:
            total_expense += abs(item["amount"])
        if running < minimum:
            minimum = running
            minimum_date = item["date"]
    commitments_before_income = [item for item in events if item["amount"] < 0 and (next_income_date is None or item["date"] <= next_income_date)]
    warnings = []
    if not any(schedule.active for schedule in income_schedules):
        warnings.append("No active income schedule is configured; next-payday results are incomplete.")
    warnings.extend(dict.fromkeys(allocation_warnings))
    if latest_actual_date is None:
        warnings.append("No actual transaction date is available for completeness checking.")
    elif (start_date - latest_actual_date).days > stale_days:
        warnings.append("Actual transaction data is stale relative to the forecast start date.")
    return {
        "events": events,
        "opening_balance": opening,
        "total_expected_income": total_income.quantize(Decimal("0.01")),
        "total_recorded_income": total_recorded_income.quantize(Decimal("0.01")),
        "forecastable_household_income": forecastable_income.quantize(Decimal("0.01")),
        "income_excluded_from_forecast": excluded_income.quantize(Decimal("0.01")),
        "total_expected_expenditure": total_expense.quantize(Decimal("0.01")),
        "projected_closing_balance": running.quantize(Decimal("0.01")),
        "minimum_projected_balance": minimum.quantize(Decimal("0.01")),
        "minimum_balance_date": minimum_date,
        "next_income_date": next_income_date,
        "days_until_payday": (next_income_date - start_date).days if next_income_date else None,
        "balance_before_payday": before_payday_balance.quantize(Decimal("0.01")) if next_income_date else None,
        "commitments_before_next_income": commitments_before_income,
        "warnings": warnings,
        "start_date": start_date,
        "end_date": end_date,
    }


def sinking_fund_recommendation(provision, income_schedules, start_date=None):
    """Calculate an estimated per-payday provision; this is not financial advice."""
    start_date = start_date or date.today()
    remaining = max(_money(provision.target_amount) - _money(provision.amount_reserved), Decimal("0.00"))
    paydays = []
    for schedule in income_schedules:
        if schedule.active:
            paydays.extend(occurrence_dates(schedule.next_expected_date, schedule.frequency, start_date, provision.due_date))
    payday_count = len(set(paydays))
    amount = (remaining / Decimal(payday_count)).quantize(Decimal("0.01"), rounding=ROUND_CEILING) if payday_count else None
    return {"remaining": remaining, "payday_count": payday_count, "recommended_per_payday": amount, "label": "Estimated"}

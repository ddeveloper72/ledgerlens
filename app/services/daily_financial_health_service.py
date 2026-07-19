from datetime import timedelta
from decimal import Decimal

from app.models import (
    Account, Category, HouseholdForecastSetting, IncomeSchedule, OneOffForecastEvent,
    PaymentReconciliation, PlannedCommitment, RecurringBill, Transaction, VariableBudget,
)
from app.services.cashflow_forecast_service import occurrence_dates
from app.services.financial_guidance_service import generate_financial_guidance
from app.services.income_allocation_service import contribution_occurrences, income_breakdown
from app.services.account_balance_service import household_balance_evidence
from app.services.confidence_service import calculate_forecast_confidence
from app.services.irish_working_day_service import next_irish_working_day, uses_irish_banking_day


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def intervention_thresholds(minimum_balance, overdraft_limit, safety_buffer):
    minimum = _money(minimum_balance)
    overdraft = _money(overdraft_limit)
    buffer_value = _money(safety_buffer)
    return {
        "payment_failure_prevention_amount": _money(max(-(minimum + overdraft), Decimal("0.00"))),
        "overdraft_avoidance_amount": _money(max(-minimum, Decimal("0.00"))),
        "safety_buffer_preservation_amount": _money(max(buffer_value - minimum, Decimal("0.00"))),
    }


def _confidence(session, selected_date, estimated_count, allocation_reasons=(), account_ids=None):
    account_query = session.query(Account)
    accounts = account_query.filter(Account.id.in_(account_ids)).all() if account_ids else account_query.all()
    reasons = []
    if not accounts:
        return {"level": "insufficient", "reasons": ["No accounts are configured."]}
    latest = []
    for account in accounts:
        value = session.query(Transaction.posted_date).filter_by(account_id=account.id, excluded_from_analysis=False).order_by(Transaction.posted_date.desc()).first()
        latest.append(value[0] if value else None)
    if any(value is None for value in latest): reasons.append("One or more accounts have no imported transactions.")
    if any(value and (selected_date - value).days > 45 for value in latest): reasons.append("One or more account imports are stale.")
    eligible_query = session.query(Transaction).filter(Transaction.excluded_from_analysis.is_(False), Transaction.internal_transfer.is_(False))
    eligible = eligible_query.filter(Transaction.account_id.in_(account_ids)).all() if account_ids else eligible_query.all()
    reviewed = sum(row.review_state == "reviewed" for row in eligible)
    categorised = sum(row.category_id is not None for row in eligible)
    reviewed_pct = Decimal(reviewed * 100) / Decimal(len(eligible)) if eligible else Decimal("0")
    categorised_pct = Decimal(categorised * 100) / Decimal(len(eligible)) if eligible else Decimal("0")
    if reviewed_pct < 80: reasons.append("Fewer than 80% of analysis transactions are reviewed.")
    if categorised_pct < 80: reasons.append("Fewer than 80% of analysis transactions are categorised.")
    if not session.query(IncomeSchedule).filter_by(active=True).first(): reasons.append("No active income schedule is configured.")
    if not session.query(RecurringBill).filter_by(active=True).first() and not session.query(PlannedCommitment).filter_by(active=True).first(): reasons.append("No confirmed recurring commitments are configured.")
    if estimated_count: reasons.append("The forecast includes estimated variable budgets.")
    reasons.extend(allocation_reasons)
    if any(value is None for value in latest) or not eligible: level = "insufficient"
    elif len(reasons) >= 3: level = "low"
    elif reasons: level = "moderate"
    else: level = "high"
    return {"level": level, "reasons": reasons, "reviewed_percent": reviewed_pct.quantize(Decimal("0.01")), "categorised_percent": categorised_pct.quantize(Decimal("0.01"))}


def _proposed_match(session, source, expected_date, expected_amount, tolerance, account_id=None):
    query = session.query(Transaction).filter(
        Transaction.amount < 0,
        Transaction.excluded_from_analysis.is_(False),
        Transaction.internal_transfer.is_(False),
        Transaction.posted_date >= expected_date - timedelta(days=5),
        Transaction.posted_date <= expected_date + timedelta(days=5),
    )
    if account_id: query = query.filter(Transaction.account_id == account_id)
    candidates = []
    for row in query.all():
        merchant_match = getattr(source, "merchant_id", None) and row.merchant_id == source.merchant_id
        category_match = getattr(source, "category_id", None) and row.category_id == source.category_id
        difference = abs(abs(_money(row.amount)) - expected_amount)
        # A category is far too broad to match one named merchant to another
        # (for example, Google to Audible merely because both are Apps).
        identity_match = merchant_match if getattr(source, "merchant_id", None) else category_match
        if identity_match and difference <= tolerance:
            candidates.append((difference, abs((row.posted_date - expected_date).days), row))
    if not candidates: return None, None
    row = min(candidates, key=lambda item: (item[0], item[1]))[2]
    status = "matched" if abs(_money(row.amount)) >= expected_amount else "partially_matched"
    return row, status


def _is_observed_spending_pattern(source_type, source):
    """Return true for wallet/card patterns whose absence does not create debt."""
    merchant_name = source.merchant.name if getattr(source, "merchant", None) else ""
    return source_type == "recurring_bill" and merchant_name.lower().startswith("paypal ")


def build_daily_financial_health(session, selected_date, horizon_days=30):
    """Calculate an explainable selected-date household position without writes."""
    setting = session.query(HouseholdForecastSetting).order_by(HouseholdForecastSetting.id).first()
    safety_buffer = _money(setting.safety_buffer if setting else 0)
    incomes = session.query(IncomeSchedule).filter_by(active=True).all()
    destination_ids = {allocation.destination_account_id for schedule in incomes for allocation in schedule.allocations if allocation.allocation_type == "household_contribution" and allocation.status != "inactive" and allocation.destination_account_id}
    destination_accounts = session.query(Account).filter(Account.id.in_(destination_ids)).all() if destination_ids else session.query(Account).filter_by(reporting_scope="household_operating").all()
    if not destination_ids:
        destination_ids = {account.id for account in destination_accounts}
    balance_position = household_balance_evidence(session, destination_accounts, selected_date)
    actual_query = session.query(Transaction).filter(Transaction.posted_date <= selected_date, Transaction.excluded_from_analysis.is_(False))
    actual_rows = actual_query.filter(Transaction.account_id.in_(destination_ids)).all() if destination_ids else []
    analysis_rows = [row for row in actual_rows if not row.internal_transfer]
    balance = balance_position["current_balance"]
    actual_income = sum((_money(row.amount) for row in analysis_rows if row.amount > 0), Decimal("0.00"))
    actual_spend = sum((abs(_money(row.amount)) for row in analysis_rows if row.amount < 0), Decimal("0.00"))
    end_date = selected_date + timedelta(days=horizon_days)
    income_events = []
    total_income_expected = Decimal("0.00")
    household_contributions_expected = Decimal("0.00")
    income_excluded = Decimal("0.00")
    income_calculation = []
    allocation_reasons = []
    for item in incomes:
        for value in occurrence_dates(item.next_expected_date, item.frequency, selected_date + timedelta(days=1), end_date):
            breakdown = income_breakdown(item, value)
            total_income_expected += breakdown["total"]
            household_contributions_expected += breakdown["household"]
            income_excluded += breakdown["total"] - breakdown["household"]
            income_calculation.append({"schedule": item, "date": value, **breakdown})
            if breakdown["household"]:
                income_events.append({"date": value, "name": f"{item.display_name} household contribution", "amount": breakdown["household"], "type": "income", "label": "Forecast"})
            else:
                irregular = any(row.allocation_type == "household_contribution" and row.frequency == "irregular" and row.status != "inactive" for row in item.allocations)
                allocation_reasons.append(f"{item.display_name} uses ad hoc contributions; no future top-up is assumed." if irregular else f"{item.display_name} has no household contribution allocation; its income is excluded from available cash.")
    all_contributions = contribution_occurrences(session, incomes, selected_date - timedelta(days=35), end_date, selected_date)
    received_contributions = [row for row in all_contributions if row["status"] in {"matched", "partially_matched"} and row["date"] <= selected_date]
    due_contributions = [row for row in all_contributions if row["status"] in {"expected", "overdue", "partially_matched"} and row["date"] <= end_date]
    if any(allocation.allocation_type == "household_contribution" and allocation.status == "estimated" for schedule in incomes for allocation in schedule.allocations): allocation_reasons.append("A household contribution is estimated rather than confirmed.")
    if any(row["status"] == "overdue" for row in due_contributions): allocation_reasons.append("An expected household contribution is overdue and unmatched.")
    next_income = min((row["date"] for row in income_events), default=None)
    forecast_end = next_income or end_date
    due_contributions = [row for row in due_contributions if row["date"] <= forecast_end]
    occurrences = []
    sources = [("recurring_bill", row, row.expected_next_date, row.cadence, _money(row.expected_amount), _money(row.amount_tolerance), "bill", False) for row in session.query(RecurringBill).filter_by(active=True).all() if row.expected_next_date and row.expected_amount is not None]
    sources += [("planned_commitment", row, row.next_expected_date, row.frequency, _money(row.amount), Decimal("0.00"), row.commitment_type, False) for row in session.query(PlannedCommitment).filter_by(active=True).all()]
    sources += [("variable_budget", row, row.next_expected_date, row.frequency, _money(row.amount), Decimal("0.00"), "variable_budget", True) for row in session.query(VariableBudget).filter_by(active=True).all()]
    reconciliations = {(row.source_type, row.source_id, row.expected_date): row for row in session.query(PaymentReconciliation).all()}
    for source_type, source, first, frequency, amount, tolerance, item_type, estimated in sources:
        dates = ([row["date"] for row in income_events] if frequency == "payday" else
                 occurrence_dates(first, frequency, selected_date - timedelta(days=35), end_date))
        for scheduled_value in dates:
            value = next_irish_working_day(scheduled_value) if uses_irish_banking_day(source_type, item_type) else scheduled_value
            if value > end_date:
                continue
            saved = reconciliations.get((source_type, source.id, value))
            status = saved.status if saved else (
                "not_observed"
                if value < selected_date and _is_observed_spending_pattern(source_type, source)
                else "overdue" if value < selected_date else "expected"
            )
            proposed, proposed_status = _proposed_match(session, source, value, amount, tolerance, getattr(source, "account_id", None))
            occurrences.append({"source_type": source_type, "source_id": source.id, "date": value, "name": source.display_name or "Expected payment", "amount": amount, "status": status, "matched_transaction": saved.matched_transaction if saved else None, "proposed_transaction": proposed, "proposed_status": proposed_status, "type": item_type, "essential": item_type in {"bill", "groceries", "pet", "transport"} or getattr(source, "essential", False), "label": "Estimated" if estimated else "Forecast"})
            occurrences[-1].update({"scheduled_date": scheduled_value, "processing_date_adjusted": value != scheduled_value})
    for item in session.query(OneOffForecastEvent).filter_by(status="planned").all():
        if selected_date - timedelta(days=35) <= item.event_date <= end_date and item.direction == "expense":
            saved = reconciliations.get(("one_off", item.id, item.event_date))
            occurrences.append({"source_type": "one_off", "source_id": item.id, "date": item.event_date, "name": item.display_name, "amount": _money(item.amount), "status": saved.status if saved else ("overdue" if item.event_date < selected_date else "expected"), "matched_transaction": saved.matched_transaction if saved else None, "proposed_transaction": None, "proposed_status": None, "type": "one_off", "essential": False, "label": "Forecast"})
    paid = [row for row in occurrences if row["status"] in {"matched", "partially_matched"} and row["date"] <= selected_date]
    outstanding = [row for row in occurrences if row["status"] in {"expected", "overdue", "partially_matched"} and row["date"] <= forecast_end]
    upcoming = [row for row in outstanding if selected_date < row["date"] <= selected_date + timedelta(days=5)]
    overdue = [row for row in outstanding if row["status"] == "overdue"]
    not_observed = [row for row in occurrences if row["status"] == "not_observed"]
    events = income_events + [{"date": row["date"], "name": row["name"], "amount": -row["amount"], "type": row["type"], "label": row["label"]} for row in outstanding if row["date"] > selected_date]
    events.sort(key=lambda row: (row["date"], 0 if row["amount"] < 0 else 1))
    running = balance; minimum = balance; minimum_date = selected_date; pre_income = balance
    for event in events:
        if event["date"] > forecast_end: continue
        running += event["amount"]
        event["running_balance"] = _money(running)
        if running < minimum: minimum, minimum_date = running, event["date"]
        if next_income and event["date"] == next_income and event["amount"] > 0: pre_income = running - event["amount"]
    minimum_available_funds = minimum + balance_position["overdraft_limit"]
    thresholds = intervention_thresholds(minimum, balance_position["overdraft_limit"], safety_buffer)
    payment_failure_prevention_amount = thresholds["payment_failure_prevention_amount"]
    overdraft_avoidance_amount = thresholds["overdraft_avoidance_amount"]
    safety_buffer_preservation_amount = thresholds["safety_buffer_preservation_amount"]
    # Compatibility aliases for existing callers.
    required_contribution = safety_buffer_preservation_amount
    payment_shortfall = payment_failure_prevention_amount
    contribution_deadline = selected_date if balance < safety_buffer else next((event["date"] for event in events if event.get("running_balance") is not None and event["running_balance"] < safety_buffer), None)
    payments_before_income = [row for row in outstanding if selected_date < row["date"] <= forecast_end]
    payments_before_income_total = sum((row["amount"] for row in payments_before_income), Decimal("0.00"))
    projected_after_contribution = _money(minimum + required_contribution)
    estimated_count = sum(row["label"] == "Estimated" for row in occurrences)
    if estimated_count: allocation_reasons.append("Some household spending occurs through a non-visible account; estimated costs are used.")
    confidence = calculate_forecast_confidence(session, selected_date=selected_date,
        estimated_event_count=estimated_count, allocation_reasons=list(dict.fromkeys(allocation_reasons)),
        account_ids=destination_ids)
    essential_uncovered = any(row["essential"] and row["status"] == "overdue" for row in outstanding)
    if confidence["level"] == "insufficient": state = "insufficient_data"
    elif minimum_available_funds < 0: state = "critical"
    elif minimum < 0: state = "at_risk"
    elif essential_uncovered: state = "at_risk"
    elif minimum < safety_buffer or overdue: state = "caution"
    else: state = "healthy"
    evidence = []
    if minimum_available_funds < 0: evidence.append(f"The forecast exceeds the account balance and available overdraft on {minimum_date}.")
    elif minimum < 0: evidence.append(f"The forecast uses the overdraft from {minimum_date} unless an additional contribution is made.")
    if minimum < safety_buffer: evidence.append(f"The projected minimum {minimum:.2f} is below the configured safety buffer {safety_buffer:.2f}.")
    if overdue: evidence.append(f"{len(overdue)} expected payment(s) are overdue and unmatched.")
    if not evidence: evidence.append("Expected commitments remain covered above the configured safety buffer.")
    result = {"selected_date": selected_date, "balance": _money(balance), "joint_account_current_balance": _money(balance), "joint_account_overdraft": balance_position["overdraft_limit"], "joint_account_available_funds": balance_position["available_funds"], "joint_account_available_balance": _money(balance), "actual_income": _money(actual_income), "actual_expenditure": _money(actual_spend), "total_household_income_expected": _money(total_income_expected), "household_contributions_expected": _money(household_contributions_expected), "household_contributions_received": sum((row["amount"] for row in received_contributions), Decimal("0.00")), "household_contributions_due": sum((row["amount"] for row in due_contributions), Decimal("0.00")), "income_excluded_from_forecast": _money(income_excluded), "estimated_household_spending": sum((row["amount"] for row in occurrences if row["label"] == "Estimated" and row["date"] > selected_date), Decimal("0.00")), "income_calculation": income_calculation, "contribution_occurrences": all_contributions, "bills_paid": paid, "essential_bills_paid": [row for row in paid if row["essential"]], "outstanding_bills": outstanding, "upcoming_five_days": upcoming, "overdue_commitments": overdue, "next_income_date": next_income, "days_until_income": (next_income - selected_date).days if next_income else None, "projected_pre_income_balance": _money(pre_income) if next_income else None, "minimum_projected_balance": _money(minimum), "minimum_available_funds": _money(minimum_available_funds), "minimum_balance_date": minimum_date, "conservative_projected_balance": _money(running), "required_contribution": _money(required_contribution), "payment_shortfall": _money(payment_shortfall), "contribution_deadline": contribution_deadline, "projected_position_after_contribution": projected_after_contribution, "payments_before_next_income": payments_before_income, "payments_before_next_income_total": _money(payments_before_income_total), "state": state, "evidence": evidence, "data_confidence": confidence, "safety_buffer": safety_buffer, "events": events}
    result["payment_failure_prevention_amount"] = _money(payment_failure_prevention_amount)
    result["overdraft_avoidance_amount"] = _money(overdraft_avoidance_amount)
    result["safety_buffer_preservation_amount"] = _money(safety_buffer_preservation_amount)
    result["household_contributions_received"] = _money(sum((row["matched_amount"] for row in received_contributions), Decimal("0.00")))
    result["household_contributions_outstanding"] = _money(sum((row["outstanding_amount"] for row in due_contributions), Decimal("0.00")))
    result["household_contributions_due"] = result["household_contributions_outstanding"]
    result.update({"balance_status": balance_position["status"], "balance_label": balance_position["label"],
        "balance_information_date": balance_position["latest_information_date"],
        "balance_as_of": balance_position["latest_information_date"],
        "balance_is_stale": balance_position["is_stale"],
        "balance_is_reconstructed": balance_position["is_reconstructed"]})
    result["not_observed_patterns"] = not_observed
    result["recommendations"] = generate_financial_guidance(result)
    return result

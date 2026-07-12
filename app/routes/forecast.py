from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import (
    Account, Category, ContributionReconciliation, IncomeAllocation, IncomeSchedule,
    OneOffForecastEvent, PlannedCommitment, HouseholdForecastSetting,
    PaymentReconciliation, RecurringBill, SavingsGoal,
    SinkingFundProvision, Transaction, VariableBudget,
)
from app.services.cashflow_forecast_service import (
    build_cashflow_forecast, occurrence_dates, sinking_fund_recommendation,
)
from app.services.merchant_service import ensure_category
from app.services.money import parse_money
from app.services.daily_financial_health_service import build_daily_financial_health
from app.services.income_allocation_service import (
    ALLOCATION_STATUSES, ALLOCATION_TYPES, AVAILABILITY_CLASSES,
    RECONCILIATION_STATUSES as CONTRIBUTION_RECONCILIATION_STATUSES,
    SOURCE_TYPES, ad_hoc_contribution_candidates, contribution_occurrences,
)

bp = Blueprint("forecast", __name__)

INCOME_FREQUENCIES = ("weekly", "fortnightly", "monthly", "irregular")
COMMITMENT_FREQUENCIES = ("weekly", "fortnightly", "monthly", "quarterly", "annual", "one-off")
COMMITMENT_TYPES = ("bill", "allowance", "groceries", "pet", "transport", "savings", "other")
VARIABLE_BUDGET_FREQUENCIES = ("weekly", "fortnightly", "monthly", "payday")
RECONCILIATION_STATUSES = ("expected", "matched", "partially_matched", "overdue", "skipped", "cancelled")
HOUSEHOLD_FLAGS = ("household", "personal", "shared", "reimbursable", "unknown")


def _date_value(name, required=True):
    value = request.form.get(name, "").strip()
    if not value and not required:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name.replace('_', ' ').title()} must be a valid date.") from None


def _actual_opening_balance(today, account_ids=None):
    query = db.session.query(db.func.coalesce(db.func.sum(Transaction.amount), Decimal("0.00"))).filter(
        Transaction.posted_date <= today, Transaction.excluded_from_analysis.is_(False)
    )
    if account_ids is not None:
        query = query.filter(Transaction.account_id.in_(account_ids)) if account_ids else query.filter(Transaction.id.is_(None))
    value = query.scalar()
    return Decimal(value).quantize(Decimal("0.01"))


def _forecast_bounds(today, view, start_text=None, end_text=None):
    start = today
    if view == "custom":
        try:
            start = date.fromisoformat(start_text or "")
            end = date.fromisoformat(end_text or "")
        except ValueError:
            raise ValueError("Custom forecast requires valid start and end dates.") from None
        if start > end:
            raise ValueError("Forecast start must be on or before its end.")
        return start, end
    if view == "next_90_days":
        return start, start + timedelta(days=90)
    if view == "next_30_days":
        return start, start + timedelta(days=30)
    next_dates = []
    for schedule in IncomeSchedule.query.filter_by(active=True).all():
        occurrences = occurrence_dates(
            schedule.next_expected_date,
            schedule.frequency,
            start,
            start + timedelta(days=366),
        )
        if occurrences:
            next_dates.append(occurrences[0])
    return start, min(next_dates) if next_dates else start + timedelta(days=30)


@bp.route("/forecast")
def payday_forecast():
    """Render actual opening data alongside a separate estimated forecast."""
    today = date.today()
    view = request.args.get("view", "next_payday")
    try:
        start, end = _forecast_bounds(today, view, request.args.get("start_date"), request.args.get("end_date"))
    except ValueError as exc:
        flash(str(exc), "error")
        view = "next_payday"
        start, end = _forecast_bounds(today, view)
    incomes = IncomeSchedule.query.order_by(IncomeSchedule.next_expected_date).all()
    destination_ids = {allocation.destination_account_id for item in incomes for allocation in item.allocations if allocation.allocation_type == "household_contribution" and allocation.status != "inactive" and allocation.destination_account_id}
    actual_opening = _actual_opening_balance(start, destination_ids)
    try:
        opening = parse_money(request.args.get("opening_balance", str(actual_opening)), allow_negative=True)
    except ValueError as exc:
        flash(str(exc), "error")
        opening = actual_opening
    latest_actual = db.session.query(db.func.max(Transaction.posted_date)).filter(Transaction.excluded_from_analysis.is_(False)).scalar()
    commitments = PlannedCommitment.query.order_by(PlannedCommitment.next_expected_date).all()
    one_offs = OneOffForecastEvent.query.order_by(OneOffForecastEvent.event_date).all()
    recurring = RecurringBill.query.filter_by(active=True).all()
    forecast = build_cashflow_forecast(opening_balance=opening, start_date=start, end_date=end, income_schedules=incomes, recurring_bills=recurring, planned_commitments=commitments, one_off_events=one_offs, latest_actual_date=latest_actual, require_household_allocations=True)
    funds = SinkingFundProvision.query.order_by(SinkingFundProvision.due_date).all()
    fund_rows = [{"provision": fund, "recommendation": sinking_fund_recommendation(fund, incomes, start)} for fund in funds]
    return render_template("forecast.html", forecast=forecast, view=view, actual_opening=actual_opening, incomes=incomes, commitments=commitments, one_offs=one_offs, fund_rows=fund_rows, accounts=Account.query.order_by(Account.name).all(), categories=Category.query.order_by(Category.name).all(), savings_goals=SavingsGoal.query.order_by(SavingsGoal.name).all(), income_frequencies=INCOME_FREQUENCIES, commitment_frequencies=COMMITMENT_FREQUENCIES, commitment_types=COMMITMENT_TYPES, household_flags=HOUSEHOLD_FLAGS)


@bp.route("/forecast/incomes", methods=["POST"])
@bp.route("/forecast/incomes/<int:item_id>", methods=["POST"])
def save_income(item_id=None):
    try:
        account = db.get_or_404(Account, request.form.get("account_id", type=int))
        frequency = request.form.get("frequency", "")
        if frequency not in INCOME_FREQUENCIES:
            raise ValueError("Select a supported income frequency.")
        item = db.session.get(IncomeSchedule, item_id) if item_id else IncomeSchedule()
        if item_id and not item:
            raise ValueError("Income schedule not found.")
        item.display_name = request.form.get("display_name", "").strip()
        if not item.display_name:
            raise ValueError("Income display name is required.")
        item.account_id = account.id
        item.amount = parse_money(request.form.get("amount"), non_negative=True)
        item.frequency = frequency
        item.next_expected_date = _date_value("next_expected_date")
        item.active = request.form.get("active") == "on"
        db.session.add(item)
        db.session.commit()
        flash("Income schedule saved.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/forecast/incomes/<int:item_id>/toggle", methods=["POST"])
def toggle_income(item_id):
    item = db.get_or_404(IncomeSchedule, item_id)
    item.active = not item.active
    db.session.commit()
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/forecast/incomes/<int:item_id>/delete", methods=["POST"])
def delete_income(item_id):
    db.session.delete(db.get_or_404(IncomeSchedule, item_id))
    db.session.commit()
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/forecast/commitments", methods=["POST"])
@bp.route("/forecast/commitments/<int:item_id>", methods=["POST"])
def save_commitment(item_id=None):
    try:
        frequency = request.form.get("frequency", "")
        commitment_type = request.form.get("commitment_type", "")
        if frequency not in COMMITMENT_FREQUENCIES or commitment_type not in COMMITMENT_TYPES:
            raise ValueError("Select supported commitment options.")
        item = db.session.get(PlannedCommitment, item_id) if item_id else PlannedCommitment()
        if item_id and not item:
            raise ValueError("Commitment not found.")
        item.display_name = request.form.get("display_name", "").strip()
        if not item.display_name:
            raise ValueError("Commitment display name is required.")
        category_name = request.form.get("category_name", "").strip()
        item.category_id = ensure_category(db.session, category_name).id if category_name else None
        item.household_flag = request.form.get("household_flag") if request.form.get("household_flag") in HOUSEHOLD_FLAGS else "unknown"
        item.amount = parse_money(request.form.get("amount"), non_negative=True)
        item.frequency = frequency
        item.next_expected_date = _date_value("next_expected_date")
        item.end_date = _date_value("end_date", required=False)
        item.active = request.form.get("active") == "on"
        item.commitment_type = commitment_type
        db.session.add(item)
        db.session.commit()
        flash("Planned commitment saved.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/forecast/commitments/<int:item_id>/toggle", methods=["POST"])
def toggle_commitment(item_id):
    item = db.get_or_404(PlannedCommitment, item_id)
    item.active = not item.active
    db.session.commit()
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/forecast/commitments/<int:item_id>/delete", methods=["POST"])
def delete_commitment(item_id):
    db.session.delete(db.get_or_404(PlannedCommitment, item_id))
    db.session.commit()
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/forecast/one-offs", methods=["POST"])
def save_one_off():
    try:
        direction = request.form.get("direction")
        if direction not in {"income", "expense"}:
            raise ValueError("Select income or expense.")
        item = OneOffForecastEvent(display_name=request.form.get("display_name", "").strip(), amount=parse_money(request.form.get("amount"), non_negative=True), event_date=_date_value("event_date"), direction=direction, household_flag=request.form.get("household_flag") if request.form.get("household_flag") in HOUSEHOLD_FLAGS else "unknown", status="planned")
        if not item.display_name:
            raise ValueError("Event display name is required.")
        category_name = request.form.get("category_name", "").strip()
        item.category_id = ensure_category(db.session, category_name).id if category_name else None
        db.session.add(item)
        db.session.commit()
        flash("One-off forecast event saved.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/forecast/one-offs/<int:item_id>/<status>", methods=["POST"])
def set_one_off_status(item_id, status):
    item = db.get_or_404(OneOffForecastEvent, item_id)
    item.status = status if status in {"planned", "completed", "cancelled"} else item.status
    db.session.commit()
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/forecast/sinking-funds", methods=["POST"])
@bp.route("/forecast/sinking-funds/<int:item_id>", methods=["POST"])
def save_sinking_fund(item_id=None):
    try:
        item = db.session.get(SinkingFundProvision, item_id) if item_id else SinkingFundProvision(active=True)
        if item_id and not item:
            raise ValueError("Sinking fund not found.")
        item.display_name = request.form.get("display_name", "").strip()
        item.target_amount = parse_money(request.form.get("target_amount"), non_negative=True)
        item.due_date = _date_value("due_date")
        item.amount_reserved = parse_money(request.form.get("amount_reserved", "0"), non_negative=True)
        item.savings_goal_id = request.form.get("savings_goal_id", type=int)
        item.active = request.form.get("active", "on") == "on"
        if not item.display_name:
            raise ValueError("Sinking fund display name is required.")
        db.session.add(item)
        db.session.commit()
        flash("Sinking-fund provision saved.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/forecast/sinking-funds/<int:item_id>/toggle", methods=["POST"])
def toggle_sinking_fund(item_id):
    item = db.get_or_404(SinkingFundProvision, item_id)
    item.active = not item.active
    db.session.commit()
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/forecast/sinking-funds/<int:item_id>/delete", methods=["POST"])
def delete_sinking_fund(item_id):
    db.session.delete(db.get_or_404(SinkingFundProvision, item_id))
    db.session.commit()
    return redirect(url_for("forecast.payday_forecast"))


@bp.route("/daily-health")
def daily_health():
    """Render a selected-date calculation without persisting forecast results."""
    try:
        selected = date.fromisoformat(request.args.get("date", date.today().isoformat()))
    except ValueError:
        selected = date.today()
        flash("Select a valid timeline date.", "error")
    view = request.args.get("view", "30")
    horizon = 90 if view == "90" else 30
    snapshot = build_daily_financial_health(db.session, selected, horizon)
    month_end = (selected.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return render_template("daily_health.html", snapshot=snapshot, selected_date=selected,
        previous_date=selected - timedelta(days=1), next_date=selected + timedelta(days=1),
        today=date.today(), slider_end=date.today() + timedelta(days=365), month_end=month_end, view=view,
        budgets=VariableBudget.query.order_by(VariableBudget.next_expected_date).all(),
        categories=Category.query.order_by(Category.name).all(),
        budget_frequencies=VARIABLE_BUDGET_FREQUENCIES,
        reconciliation_statuses=RECONCILIATION_STATUSES)


@bp.route("/daily-health/settings", methods=["POST"])
def save_daily_health_settings():
    try:
        amount = parse_money(request.form.get("safety_buffer"), non_negative=True)
        setting = HouseholdForecastSetting.query.order_by(HouseholdForecastSetting.id).first() or HouseholdForecastSetting()
        setting.safety_buffer = amount
        db.session.add(setting); db.session.commit()
        flash("Household safety buffer saved.", "success")
    except ValueError as exc:
        db.session.rollback(); flash(str(exc), "error")
    return redirect(url_for("forecast.daily_health", date=request.form.get("selected_date")))


@bp.route("/daily-health/budgets", methods=["POST"])
@bp.route("/daily-health/budgets/<int:item_id>", methods=["POST"])
def save_variable_budget(item_id=None):
    try:
        frequency = request.form.get("frequency", "")
        if frequency not in VARIABLE_BUDGET_FREQUENCIES: raise ValueError("Select a supported budget frequency.")
        item = db.session.get(VariableBudget, item_id) if item_id else VariableBudget()
        if item_id and not item: raise ValueError("Variable budget not found.")
        item.display_name = request.form.get("display_name", "").strip()
        if not item.display_name: raise ValueError("Budget display name is required.")
        item.amount = parse_money(request.form.get("amount"), non_negative=True)
        item.frequency = frequency
        item.next_expected_date = _date_value("next_expected_date")
        category_name = request.form.get("category_name", "").strip()
        item.category_id = ensure_category(db.session, category_name).id if category_name else None
        item.essential = request.form.get("essential") == "on"
        item.active = request.form.get("active", "on") == "on"
        db.session.add(item); db.session.commit(); flash("Variable household budget saved.", "success")
    except ValueError as exc:
        db.session.rollback(); flash(str(exc), "error")
    return redirect(url_for("forecast.daily_health", date=request.form.get("selected_date")))


@bp.route("/daily-health/reconciliations", methods=["POST"])
def review_reconciliation():
    try:
        status = request.form.get("status", "")
        source_type = request.form.get("source_type", "")
        source_id = request.form.get("source_id", type=int)
        expected_date = date.fromisoformat(request.form.get("expected_date", ""))
        if status not in RECONCILIATION_STATUSES or not source_type or not source_id: raise ValueError("Select a valid reconciliation status.")
        row = PaymentReconciliation.query.filter_by(source_type=source_type, source_id=source_id, expected_date=expected_date).first() or PaymentReconciliation(source_type=source_type, source_id=source_id, expected_date=expected_date)
        row.expected_amount = parse_money(request.form.get("expected_amount"), non_negative=True)
        row.status = status
        row.matched_transaction_id = request.form.get("matched_transaction_id", type=int) if status in {"matched", "partially_matched"} else None
        if status in {"matched", "partially_matched"} and not row.matched_transaction_id:
            raise ValueError("Select a proposed transaction before confirming a match.")
        row.reviewed_at = datetime.now()
        db.session.add(row); db.session.commit(); flash("Payment status reviewed.", "success")
    except ValueError as exc:
        db.session.rollback(); flash(str(exc), "error")
    return redirect(url_for("forecast.daily_health", date=request.form.get("selected_date")))


@bp.route("/income-allocations")
def income_allocations():
    """Display income allocations and read-only contribution match proposals."""
    schedules = IncomeSchedule.query.order_by(IncomeSchedule.next_expected_date).all()
    today = date.today()
    contributions = contribution_occurrences(db.session, schedules, today - timedelta(days=45), today + timedelta(days=90), today)
    ad_hoc_candidates = ad_hoc_contribution_candidates(db.session, schedules, today - timedelta(days=90), today)
    return render_template("income_allocations.html", schedules=schedules, accounts=Account.query.order_by(Account.name).all(),
        contributions=contributions, allocation_types=ALLOCATION_TYPES, allocation_statuses=ALLOCATION_STATUSES,
        availability_classes=AVAILABILITY_CLASSES, source_types=SOURCE_TYPES,
        reconciliation_statuses=CONTRIBUTION_RECONCILIATION_STATUSES, ad_hoc_candidates=ad_hoc_candidates, today=today)


@bp.route("/income-allocations/<int:schedule_id>", methods=["POST"])
@bp.route("/income-allocations/<int:schedule_id>/<int:allocation_id>", methods=["POST"])
def save_income_allocation(schedule_id, allocation_id=None):
    schedule = db.get_or_404(IncomeSchedule, schedule_id)
    try:
        allocation_type = request.form.get("allocation_type", "")
        status = request.form.get("status", "")
        source_type = request.form.get("source_type", "manual")
        availability = request.form.get("availability_classification", "")
        if allocation_type not in ALLOCATION_TYPES or status not in ALLOCATION_STATUSES or source_type not in SOURCE_TYPES or availability not in AVAILABILITY_CLASSES:
            raise ValueError("Select supported income allocation options.")
        amount_text = request.form.get("amount", "").strip()
        percentage_text = request.form.get("percentage", "").strip()
        frequency = request.form.get("frequency") if request.form.get("frequency") in INCOME_FREQUENCIES else schedule.frequency
        allows_actual_only = allocation_type == "household_contribution" and frequency == "irregular"
        if bool(amount_text) and bool(percentage_text): raise ValueError("Enter a fixed amount or a percentage, not both.")
        if not amount_text and not percentage_text and not allows_actual_only: raise ValueError("Enter either a fixed amount or a percentage.")
        allocation = db.session.get(IncomeAllocation, allocation_id) if allocation_id else IncomeAllocation(income_schedule_id=schedule.id)
        if allocation_id and (not allocation or allocation.income_schedule_id != schedule.id): raise ValueError("Income allocation not found.")
        allocation.allocation_type = allocation_type
        allocation.amount = parse_money(amount_text, non_negative=True) if amount_text else None
        allocation.percentage = parse_money(percentage_text, non_negative=True) if percentage_text else None
        if allocation.percentage is not None and allocation.percentage > 100: raise ValueError("Percentage cannot exceed 100.")
        allocation.destination_account_id = request.form.get("destination_account_id", type=int)
        if allocation_type == "household_contribution" and not allocation.destination_account_id: raise ValueError("Select a destination household account.")
        allocation.effective_from = _date_value("effective_from")
        allocation.effective_to = _date_value("effective_to", required=False)
        if allocation.effective_to and allocation.effective_to < allocation.effective_from: raise ValueError("Effective end cannot precede its start.")
        allocation.frequency = frequency
        allocation.status = status; allocation.source_type = source_type
        schedule.availability_classification = availability
        db.session.add(allocation); db.session.commit(); flash("Income allocation saved without changing total expected pay.", "success")
    except ValueError as exc:
        db.session.rollback(); flash(str(exc), "error")
    return redirect(url_for("forecast.income_allocations"))


@bp.route("/income-allocations/<int:allocation_id>/deactivate", methods=["POST"])
def deactivate_income_allocation(allocation_id):
    allocation = db.get_or_404(IncomeAllocation, allocation_id)
    allocation.status = "inactive"; db.session.commit(); flash("Income allocation deactivated.", "success")
    return redirect(url_for("forecast.income_allocations"))


@bp.route("/contribution-reconciliations", methods=["POST"])
def review_contribution_reconciliation():
    try:
        allocation = db.get_or_404(IncomeAllocation, request.form.get("allocation_id", type=int))
        expected_date = date.fromisoformat(request.form.get("expected_date", ""))
        status = request.form.get("status", "")
        if status not in CONTRIBUTION_RECONCILIATION_STATUSES: raise ValueError("Select a valid contribution status.")
        row = ContributionReconciliation.query.filter_by(income_allocation_id=allocation.id, expected_date=expected_date).first() or ContributionReconciliation(income_allocation_id=allocation.id, expected_date=expected_date)
        row.expected_amount = parse_money(request.form.get("expected_amount"), non_negative=True)
        row.status = status
        row.matched_transaction_id = request.form.get("matched_transaction_id", type=int) if status in {"matched", "partially_matched"} else None
        if status in {"matched", "partially_matched"} and not row.matched_transaction_id: raise ValueError("Select a proposed incoming transaction before confirming a match.")
        row.reviewed_at = datetime.now(); db.session.add(row); db.session.commit(); flash("Household contribution status reviewed.", "success")
    except ValueError as exc:
        db.session.rollback(); flash(str(exc), "error")
    return redirect(url_for("forecast.income_allocations"))

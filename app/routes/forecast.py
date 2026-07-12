from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import (
    Account, Category, IncomeSchedule, OneOffForecastEvent, PlannedCommitment,
    RecurringBill, SavingsGoal, SinkingFundProvision, Transaction,
)
from app.services.cashflow_forecast_service import (
    build_cashflow_forecast, occurrence_dates, sinking_fund_recommendation,
)
from app.services.merchant_service import ensure_category
from app.services.money import parse_money

bp = Blueprint("forecast", __name__)

INCOME_FREQUENCIES = ("weekly", "fortnightly", "monthly", "irregular")
COMMITMENT_FREQUENCIES = ("weekly", "fortnightly", "monthly", "quarterly", "annual", "one-off")
COMMITMENT_TYPES = ("bill", "allowance", "groceries", "pet", "transport", "savings", "other")
HOUSEHOLD_FLAGS = ("household", "personal", "shared", "reimbursable", "unknown")


def _date_value(name, required=True):
    value = request.form.get(name, "").strip()
    if not value and not required:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name.replace('_', ' ').title()} must be a valid date.") from None


def _actual_opening_balance(today):
    value = (
        db.session.query(db.func.coalesce(db.func.sum(Transaction.amount), Decimal("0.00")))
        .filter(
            Transaction.posted_date <= today,
            Transaction.excluded_from_analysis.is_(False),
        )
        .scalar()
    )
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
    actual_opening = _actual_opening_balance(start)
    try:
        opening = parse_money(request.args.get("opening_balance", str(actual_opening)), allow_negative=True)
    except ValueError as exc:
        flash(str(exc), "error")
        opening = actual_opening
    latest_actual = db.session.query(db.func.max(Transaction.posted_date)).filter(Transaction.excluded_from_analysis.is_(False)).scalar()
    incomes = IncomeSchedule.query.order_by(IncomeSchedule.next_expected_date).all()
    commitments = PlannedCommitment.query.order_by(PlannedCommitment.next_expected_date).all()
    one_offs = OneOffForecastEvent.query.order_by(OneOffForecastEvent.event_date).all()
    recurring = RecurringBill.query.filter_by(active=True).all()
    forecast = build_cashflow_forecast(opening_balance=opening, start_date=start, end_date=end, income_schedules=incomes, recurring_bills=recurring, planned_commitments=commitments, one_off_events=one_offs, latest_actual_date=latest_actual)
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

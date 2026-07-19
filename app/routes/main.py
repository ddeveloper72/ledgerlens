import os
from collections import defaultdict
from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import (
    Account, Category, CategoryFlagRule, ImportBatch, Merchant, MerchantAlias,
    RecurringBill, RecurringCandidate, SavingsGoal, Transaction, TransactionPatternRule, User,
)
from app.services.csv_import import (
    CSVImportError,
    DOCUMENT_TYPE_LABELS,
    SOURCE_NEEDS_ACCOUNT_KEY,
    amend_existing_import_metadata,
    import_transactions,
)
from app.services.cashflow_service import cash_flow_calendar
from app.services.account_balance_service import account_balance_at
from app.services.savings_account_health_service import savings_account_health
from app.services.completeness_service import data_completeness_report
from app.services.household_analytics import household_analytics_snapshot
from app.services.merchant_service import (
    apply_mapping, canonical_merchant_hint, ensure_category, infer_financial_labels,
    preview_mapping_count, save_mapping,
)
from app.services.imports.paypal_import import (
    exclude_legacy_paypal_internal_rows,
    restore_excluded_paypal_internal_rows,
)
from app.services.credit_union_internal import mark_credit_union_internal_movements
from app.services.description_patterns import is_counterparty_candidate, transaction_description_context
from app.services.description_patterns import (
    PAYMENT_METHODS, description_pattern_key, payment_method_for, transaction_direction,
)
from app.services.duplicate_maintenance import exclude_verified_duplicates, verified_duplicate_rows
from app.services.money import parse_money
from app.services.period_service import apply_transaction_period, resolve_period
from app.services.recurrence_service import (
    FREQUENCIES, confirm_candidate, deactivate_candidate_and_bill,
    deactivate_recurring_bill as deactivate_bill_service, refresh_candidates,
    recurring_expected_vs_missing,
)
from app.services.savings_service import REASON_CATEGORIES, add_recovery_event, savings_recovery_summary

bp = Blueprint("main", __name__)

HOUSEHOLD_FLAGS = ["household", "personal", "shared", "reimbursable", "unknown"]


_description_pattern_key = description_pattern_key


def _build_smart_review_groups(pending_transactions):
    """Group changing references and variable amounts under one canonical pattern."""
    grouped = defaultdict(list)
    for txn in pending_transactions:
        amount = Decimal(txn.amount).quantize(Decimal("0.01"))
        pattern_key = _description_pattern_key(txn.cleaned_description, txn.amount)
        direction = transaction_direction(txn.amount)
        if not pattern_key:
            continue
        grouped[(txn.account_id, direction, pattern_key)].append(txn)

    candidates = []
    for (account_id, direction, pattern_key), txns in grouped.items():

        distinct_descriptions = {txn.cleaned_description for txn in txns}
        match_mode = "exact" if len(distinct_descriptions) == 1 else "pattern"
        sample = txns[0]
        candidates.append(
            {
                "account_id": account_id,
                "account_name": sample.account.name if sample.account else "Unknown account",
                "direction": direction,
                "amount_min": min(Decimal(row.amount) for row in txns),
                "amount_max": max(Decimal(row.amount) for row in txns),
                "pattern_key": pattern_key,
                "sample_description": sample.cleaned_description,
                "count": len(txns),
                "match_mode": match_mode,
            }
        )

    candidates.sort(key=lambda item: item["count"], reverse=True)
    return candidates[:50]


def _remember_pattern_rule(account_id, pattern_key, direction, category, household_flag, payment_method):
    rule = TransactionPatternRule.query.filter_by(
        account_id=account_id, pattern_key=pattern_key, direction=direction
    ).first()
    if not rule:
        rule = TransactionPatternRule(
            account_id=account_id, pattern_key=pattern_key, direction=direction
        )
        db.session.add(rule)
    rule.category_id = category.id if category else None
    rule.household_flag = household_flag
    rule.payment_method = payment_method
    rule.active = True
    return rule


def _auto_align_reviewed_classifications():
    """Align reviewed outliers when a smart group has a strong majority category and flag."""
    reviewed_transactions = Transaction.query.filter_by(review_state="reviewed", excluded_from_analysis=False).all()
    grouped = defaultdict(list)
    for txn in reviewed_transactions:
        amount = Decimal(txn.amount).quantize(Decimal("0.01"))
        pattern_key = _description_pattern_key(txn.cleaned_description)
        if not pattern_key:
            continue
        grouped[(txn.account_id, amount, pattern_key)].append(txn)

    groups_aligned = 0
    rows_updated = 0
    skipped_ambiguous = 0

    for _group_key, rows in grouped.items():
        if len(rows) < 5:
            continue

        category_counts = defaultdict(int)
        for row in rows:
            category_name = row.category.name if row.category else "Uncategorized"
            category_counts[category_name] += 1

        flag_counts = defaultdict(int)
        for row in rows:
            flag_counts[row.household_flag] += 1

        majority_category, majority_category_count = max(category_counts.items(), key=lambda item: item[1])
        majority_flag, majority_flag_count = max(flag_counts.items(), key=lambda item: item[1])

        category_ratio = majority_category_count / len(rows)
        flag_ratio = majority_flag_count / len(rows)

        # Conservative threshold avoids overriding genuinely mixed groups.
        if category_ratio < 0.9 or flag_ratio < 0.9:
            skipped_ambiguous += 1
            continue

        if majority_category == "Uncategorized":
            skipped_ambiguous += 1
            continue

        category = Category.query.filter_by(name=majority_category).first()
        if not category:
            skipped_ambiguous += 1
            continue

        group_changed = False
        for row in rows:
            row_category_name = row.category.name if row.category else "Uncategorized"
            row_changed = False

            if row_category_name != majority_category:
                row.category_id = category.id
                row_changed = True

            if row.household_flag != majority_flag:
                row.household_flag = majority_flag
                row_changed = True

            if row_changed:
                rows_updated += 1
                group_changed = True

        if group_changed:
            groups_aligned += 1

    return {
        "groups_aligned": groups_aligned,
        "rows_updated": rows_updated,
        "skipped_ambiguous": skipped_ambiguous,
    }


def _month_bounds(today=None):
    """Return the current month start and next-month start date boundaries."""
    today = today or date.today()
    month_start = today.replace(day=1)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)
    return month_start, next_month_start


@bp.route("/")
def dashboard():
    """Render dashboard totals, top categories, and recent transactions."""
    try:
        period = resolve_period(
            request.args.get("period"), request.args.get("start_date"), request.args.get("end_date")
        )
    except ValueError as exc:
        flash(str(exc), "error")
        period = resolve_period()
    operating_filter = Account.reporting_scope != "savings_tracking"
    total_transactions = Transaction.query.join(Account).filter(operating_filter, Transaction.excluded_from_analysis.is_(False)).count()
    pending_transactions = Transaction.query.join(Account).filter(operating_filter, Transaction.review_state == "pending", Transaction.excluded_from_analysis.is_(False), Transaction.internal_transfer.is_(False)).count()
    month_transactions = apply_transaction_period(
        Transaction.query.join(Account).filter(operating_filter, Transaction.excluded_from_analysis.is_(False), Transaction.internal_transfer.is_(False)).order_by(Transaction.posted_date.desc(), Transaction.id.desc()),
        period,
        Transaction,
    ).all()

    monthly_income = Decimal("0.00")
    monthly_spending = Decimal("0.00")
    household_spending = Decimal("0.00")
    insurance_spend = Decimal("0.00")
    insurance_claims = Decimal("0.00")
    category_totals = defaultdict(lambda: Decimal("0.00"))

    for txn in month_transactions:
        amount = Decimal(txn.amount)
        if amount > 0:
            monthly_income += amount
            if txn.category and txn.category.name == "Insurance Claims":
                insurance_claims += amount
        else:
            expense = abs(amount)
            monthly_spending += expense
            if txn.household_flag == "household":
                household_spending += expense

            category_name = txn.category.name if txn.category else "Uncategorized"
            category_totals[category_name] += expense
            if category_name == "Insurance":
                insurance_spend += expense

    uncategorised_transactions = (
        Transaction.query.join(Account).outerjoin(Category)
        .filter(
            operating_filter,
            Transaction.excluded_from_analysis.is_(False),
            Transaction.internal_transfer.is_(False),
            db.or_(
                Transaction.category_id.is_(None),
                Category.name == "Uncategorized",
            )
        )
        .count()
    )

    top_categories = sorted(category_totals.items(), key=lambda item: item[1], reverse=True)[:5]
    recurring_snapshot = recurring_expected_vs_missing(db.session)
    cash_calendar = cash_flow_calendar(db.session, period)
    analytics_snapshot = household_analytics_snapshot(db.session, period)
    recovery_snapshot = savings_recovery_summary(db.session)
    completeness = data_completeness_report(db.session, period)
    recent_transactions = (
        Transaction.query.join(Account).filter(operating_filter, Transaction.excluded_from_analysis.is_(False)).order_by(Transaction.posted_date.desc(), Transaction.id.desc())
        .limit(8)
        .all()
    )

    return render_template(
        "dashboard.html",
        total_transactions=total_transactions,
        pending_transactions=pending_transactions,
        monthly_income=monthly_income,
        monthly_spending=monthly_spending,
        household_spending=household_spending,
        insurance_spend=insurance_spend,
        insurance_claims=insurance_claims,
        net_insurance_cost=insurance_spend - insurance_claims,
        uncategorised_transactions=uncategorised_transactions,
        top_categories=top_categories,
        recurring_expected=recurring_snapshot["expected"],
        recurring_missing=recurring_snapshot["missing"],
        cash_calendar=cash_calendar,
        analytics_snapshot=analytics_snapshot,
        recovery_snapshot=recovery_snapshot,
        recent_transactions=recent_transactions,
        period=period,
        completeness=completeness,
        savings_accounts=savings_account_health(db.session),
    )

def _intelligence_context(preview=None):
    """Build the read-only intelligence page context."""
    period = resolve_period("current_month")
    recent_transactions = Transaction.query.filter_by(excluded_from_analysis=False).order_by(Transaction.posted_date.desc(), Transaction.id.desc()).limit(20).all()
    journey = []
    for txn in recent_transactions:
        description_context = transaction_description_context(txn.cleaned_description, txn.amount)
        assigned_name = txn.merchant.name if txn.merchant and is_counterparty_candidate(txn.merchant.name) else None
        merchant_name = assigned_name or description_context["counterparty_hint"] or "Unconfirmed"
        category_name = txn.category.name if txn.category else "Uncategorized"
        journey.append({"transaction": txn, "merchant_name": merchant_name, "category_name": category_name,
            "description_context": description_context,
            "labels": infer_financial_labels(merchant_name, category_name, txn.cleaned_description)})
    recurring = recurring_expected_vs_missing(db.session)
    alias_candidates = []
    seen_aliases = set()
    for transaction in recent_transactions:
        description_context = transaction_description_context(transaction.cleaned_description, transaction.amount)
        if (description_context["user_note"] or description_context["contains_sensitive_reference"]
                or description_context["reference_kind"] == "account_alias"):
            continue
        hint = canonical_merchant_hint(transaction.cleaned_description)
        if hint and hint not in seen_aliases:
            label_detail = description_context["payment_method_label"]
            alias_candidates.append({"value": transaction.cleaned_description,
                "label": f"{label_detail}: {transaction.cleaned_description}",
                "context": description_context})
            seen_aliases.add(hint)
    merchant_options = [row for row in Merchant.query.order_by(Merchant.name).all()
                        if is_counterparty_candidate(row.name)]
    return {
        "merchant_journey": journey,
        "merchant_aliases": MerchantAlias.query.order_by(MerchantAlias.alias).all(),
        "recurring_expected": recurring["expected"], "recurring_missing": recurring["missing"],
        "cash_calendar": cash_flow_calendar(db.session, period), "preview": preview,
        "alias_candidates": alias_candidates,
        "merchant_options": merchant_options,
        "category_options": Category.query.order_by(Category.name).all(),
    }


def _mapping_form_value(name, default=""):
    """Resolve a structured selection or its explicit custom-value field."""
    selected = request.form.get(name, "").strip()
    if selected == "__new__":
        return request.form.get(f"{name}_custom", "").strip()
    return selected or default


@bp.route("/intelligence", methods=["GET", "POST"])
def intelligence():
    """Render explainable intelligence without changing database state."""
    if request.method == "POST":
        return save_merchant_mapping()
    return render_template("intelligence.html", **_intelligence_context())


@bp.route("/merchant-mappings/preview", methods=["POST"])
def preview_merchant_mapping():
    alias_text = _mapping_form_value("alias_text")
    merchant_name = _mapping_form_value("merchant_name")
    category_name = _mapping_form_value("category_name", "Uncategorized")
    household_flag = request.form.get("household_flag") if request.form.get("household_flag") in HOUSEHOLD_FLAGS else "unknown"
    if not alias_text or not merchant_name:
        flash("Alias and merchant are required.", "error")
        return redirect(url_for("main.intelligence"))
    preview = {"alias_text": alias_text, "merchant_name": merchant_name, "category_name": category_name, "household_flag": household_flag, "count": preview_mapping_count(db.session, alias_text)}
    return render_template("intelligence.html", **_intelligence_context(preview))


@bp.route("/merchant-mappings", methods=["POST"])
def save_merchant_mapping():
    alias_text = _mapping_form_value("alias_text")
    merchant_name = _mapping_form_value("merchant_name")
    category_name = _mapping_form_value("category_name", "Uncategorized")
    household_flag = request.form.get("household_flag") if request.form.get("household_flag") in HOUSEHOLD_FLAGS else "unknown"
    if not alias_text or not merchant_name:
        flash("Alias and merchant are required.", "error")
        return redirect(url_for("main.intelligence"))
    alias = save_mapping(db.session, alias_text, merchant_name, category_name=category_name, household_flag=household_flag, origin="manual")
    updated = apply_mapping(db.session, alias, category_name) if request.form.get("confirm_apply") == "on" else 0
    db.session.commit()
    flash(f"Mapping saved. {updated} pending transaction(s) updated.", "success")
    return redirect(url_for("main.intelligence"))


@bp.route("/merchant-mappings/<int:alias_id>", methods=["POST"])
def update_merchant_mapping(alias_id):
    alias = db.session.get(MerchantAlias, alias_id)
    if not alias:
        flash("Merchant mapping not found.", "error")
        return redirect(url_for("main.intelligence"))
    alias.alias = request.form.get("alias_text", alias.alias).strip().lower()
    merchant_name = request.form.get("merchant_name", alias.merchant.name).strip()
    merchant = Merchant.query.filter_by(name=merchant_name).first() or Merchant(name=merchant_name)
    db.session.add(merchant)
    db.session.flush()
    alias.merchant_id = merchant.id
    category_name = request.form.get("category_name", "").strip()
    alias.category_id = ensure_category(db.session, category_name).id if category_name else None
    alias.household_flag = request.form.get("household_flag") if request.form.get("household_flag") in HOUSEHOLD_FLAGS else "unknown"
    alias.active = request.form.get("active") == "on"
    db.session.commit()
    flash("Merchant mapping updated; no transactions were changed.", "success")
    return redirect(url_for("main.intelligence"))


@bp.route("/merchant-mappings/<int:alias_id>/delete", methods=["POST"])
def delete_merchant_mapping(alias_id):
    alias = db.session.get(MerchantAlias, alias_id)
    if alias:
        db.session.delete(alias)
        db.session.commit()
    flash("Merchant mapping deleted.", "success")
    return redirect(url_for("main.intelligence"))


@bp.route("/recurring-candidates")
def recurring_candidates():
    return render_template("recurring_candidates.html", candidates=RecurringCandidate.query.order_by(RecurringCandidate.status, RecurringCandidate.confidence_score.desc()).all(), categories=Category.query.order_by(Category.name).all(), frequencies=FREQUENCIES, household_flags=HOUSEHOLD_FLAGS)


@bp.route("/recurring-candidates/refresh", methods=["POST"])
def refresh_recurring_candidates():
    created, updated = refresh_candidates(db.session)
    db.session.commit()
    flash(f"Candidate refresh complete: {created} created, {updated} updated.", "success")
    return redirect(url_for("main.recurring_candidates"))


@bp.route("/recurring-candidates/<int:candidate_id>/reject", methods=["POST"])
def reject_recurring_candidate(candidate_id):
    candidate = db.get_or_404(RecurringCandidate, candidate_id)
    deactivate_candidate_and_bill(db.session, candidate)
    db.session.commit()
    flash("Recurring candidate rejected and its alert deactivated.", "success")
    return redirect(url_for("main.recurring_candidates"))


@bp.route("/recurring-candidates/<int:candidate_id>/confirm", methods=["POST"])
def confirm_recurring_candidate(candidate_id):
    candidate = db.get_or_404(RecurringCandidate, candidate_id)
    try:
        expected_amount = parse_money(request.form.get("expected_amount"), non_negative=True)
        tolerance = parse_money(request.form.get("amount_tolerance", "0"), non_negative=True)
        next_date = date.fromisoformat(request.form["expected_next_date"]) if request.form.get("expected_next_date") else None
    except (ValueError, KeyError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.recurring_candidates"))
    category = ensure_category(db.session, request.form.get("category_name"))
    confirm_candidate(db.session, candidate, {"display_name": request.form.get("display_name", candidate.display_name).strip(), "category_id": category.id, "frequency": request.form.get("frequency") if request.form.get("frequency") in FREQUENCIES else "irregular", "expected_amount": expected_amount, "amount_tolerance": tolerance, "expected_next_date": next_date, "household_flag": request.form.get("household_flag") if request.form.get("household_flag") in HOUSEHOLD_FLAGS else "unknown", "active": request.form.get("active") == "on"})
    db.session.commit()
    flash("Recurring candidate confirmed.", "success")
    return redirect(url_for("main.recurring_candidates"))


@bp.route("/recurring-bills/<int:bill_id>/deactivate", methods=["POST"])
def deactivate_recurring_bill(bill_id):
    """Deactivate a false recurring alert without changing its source transactions."""
    bill = db.get_or_404(RecurringBill, bill_id)
    deactivate_bill_service(db.session, bill)
    db.session.commit()
    flash("Recurring alert deactivated. Source transactions were not changed.", "success")
    return redirect(request.referrer or url_for("main.recurring_candidates"))


@bp.route("/savings-recovery", methods=["GET", "POST"])
def savings_recovery():
    """Track an emergency-fund baseline and append-only recovery events."""
    if request.method == "POST":
        action = request.form.get("action", "goal")
        try:
            if action == "event":
                goal = db.session.get(SavingsGoal, request.form.get("goal_id", type=int))
                if not goal:
                    raise ValueError("Create a recovery goal before adding events.")
                add_recovery_event(db.session, goal, event_date=date.fromisoformat(request.form.get("event_date", "")), amount=parse_money(request.form.get("amount"), non_negative=True), event_type=request.form.get("event_type", ""), reason=request.form.get("reason", "").strip() or "General", note=request.form.get("note"))
                message = "Savings recovery event added."
            else:
                target_text = request.form.get("target_amount", "").strip()
                target = parse_money(target_text, non_negative=True) if target_text else None
                baseline = parse_money(request.form.get("current_amount"), non_negative=True)
                repayment_text = request.form.get("repayment_per_payday", "").strip()
                repayment = parse_money(repayment_text, non_negative=True) if repayment_text else None
                goal = SavingsGoal.query.order_by(SavingsGoal.id).first()
                if not goal:
                    goal = SavingsGoal(name="Emergency Fund", target_amount=target, current_amount=baseline)
                    db.session.add(goal)
                goal.name = request.form.get("goal_name", "Emergency Fund").strip() or "Emergency Fund"
                goal.target_amount = target
                goal.current_amount = baseline
                goal.repayment_per_payday = repayment
                message = "Savings recovery plan updated."
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("main.savings_recovery"))
        db.session.commit()
        flash(message, "success")
        return redirect(url_for("main.savings_recovery"))

    return render_template(
        "savings_recovery.html",
        recovery_snapshot=savings_recovery_summary(db.session),
        reason_categories=REASON_CATEGORIES,
    )

@bp.route("/accounts", methods=["GET", "POST"])
def accounts():
    """Create and list local accounts used for transaction imports."""
    if request.method == "POST":
        account_name = request.form.get("account_name", "").strip()
        account_type = request.form.get("account_type", "checking").strip().lower() or "checking"
        statement_account_key = request.form.get("statement_account_key", "").strip() or None

        if not account_name:
            flash("Account name is required.", "error")
            return redirect(url_for("main.accounts"))

        user = User.query.first()
        if not user:
            user = User(name=os.environ.get("DEFAULT_USER_NAME", "Sample User"))
            db.session.add(user)
            db.session.flush()

        if statement_account_key and Account.query.filter_by(statement_account_key=statement_account_key).first():
            flash("That statement account key is already bound to another account.", "error")
            return redirect(url_for("main.accounts"))
        account = Account(user_id=user.id, name=account_name, account_type=account_type,
                          statement_account_key=statement_account_key)
        db.session.add(account)
        db.session.commit()
        flash(f"Account '{account_name}' created.", "success")
        return redirect(url_for("main.accounts"))

    account_rows = []
    for account in Account.query.order_by(Account.name.asc()).all():
        balance = account_balance_at(db.session, account, date.today())
        transaction_count = Transaction.query.filter_by(account_id=account.id, excluded_from_analysis=False).count()
        excluded_count = Transaction.query.filter_by(account_id=account.id, excluded_from_analysis=True).count()
        internal_transfer_count = Transaction.query.filter_by(account_id=account.id, internal_transfer=True).count()
        account_rows.append(
            {
                "account": account,
                "balance": balance,
                "available_funds": Decimal(balance) + Decimal(account.overdraft_limit or 0),
                "transaction_count": transaction_count,
                "excluded_count": excluded_count,
                "internal_transfer_count": internal_transfer_count,
            }
        )

    return render_template("accounts.html", account_rows=account_rows, savings_accounts=savings_account_health(db.session))


@bp.route("/accounts/<int:account_id>/balance", methods=["POST"])
def update_account_balance(account_id):
    """Save a bank-provided balance snapshot and optional overdraft limit."""
    account = db.get_or_404(Account, account_id)
    try:
        account.current_balance = parse_money(request.form.get("current_balance"), allow_negative=True)
        account.overdraft_limit = parse_money(request.form.get("overdraft_limit", "0"), non_negative=True)
        account.balance_as_of = date.fromisoformat(request.form.get("balance_as_of", ""))
        statement_account_key = request.form.get("statement_account_key", "").strip() or None
        conflict = Account.query.filter(
            Account.statement_account_key == statement_account_key,
            Account.id != account.id,
        ).first() if statement_account_key else None
        if conflict:
            raise ValueError("That statement account key is already bound to another account.")
        account.statement_account_key = statement_account_key
        scope = request.form.get("reporting_scope", "household_operating")
        if scope not in {"household_operating", "personal", "savings_tracking"}:
            raise ValueError("Select a supported reporting scope.")
        account.reporting_scope = scope
        db.session.commit()
        flash("Account balance snapshot saved.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.accounts"))


@bp.route("/accounts/paypal-internal/exclude", methods=["POST"])
def exclude_paypal_internal_rows():
    """Explicitly exclude detected PayPal bookkeeping rows from financial analysis."""
    excluded = exclude_legacy_paypal_internal_rows(db.session)
    db.session.commit()
    flash(f"Excluded {excluded} PayPal internal-processing transaction(s).", "success")
    return redirect(url_for("main.accounts"))


@bp.route("/accounts/paypal-internal/restore", methods=["POST"])
def restore_paypal_internal_rows():
    """Reverse PayPal internal-row exclusions while retaining the raw records."""
    restored = restore_excluded_paypal_internal_rows(db.session)
    db.session.commit()
    flash(f"Restored {restored} PayPal internal-processing transaction(s).", "success")
    return redirect(url_for("main.accounts"))


@bp.route("/accounts/credit-union-internal/mark", methods=["POST"])
def mark_credit_union_internal_rows():
    """Explicitly mark user-confirmed Credit Union ledger movements as internal transfers."""
    updated = mark_credit_union_internal_movements(db.session)
    db.session.commit()
    flash(f"Marked {updated} Credit Union movement(s) as internal transfers.", "success")
    return redirect(url_for("main.accounts"))


@bp.route("/transactions")
def transactions():
    """Render imported transactions without mutating reconciliation metadata."""
    txns = (
        Transaction.query
        .filter(Transaction.excluded_from_analysis.is_(False))
        .filter(~Transaction.cleaned_description.like("PayPal %"))
        .order_by(Transaction.posted_date.desc(), Transaction.id.desc())
        .all()
    )
    return render_template("transactions.html", transactions=txns)


@bp.route("/reviews")
def reviews():
    """Show transactions that still need category/flag review."""
    pending_transactions = (
        Transaction.query.filter_by(review_state="pending", excluded_from_analysis=False, internal_transfer=False)
        .order_by(Transaction.posted_date.desc(), Transaction.id.desc())
        .all()
    )
    reviewed_transactions = (
        Transaction.query.filter_by(review_state="reviewed", excluded_from_analysis=False, internal_transfer=False)
        .order_by(Transaction.posted_date.desc(), Transaction.id.desc())
        .limit(100)
        .all()
    )
    categories = Category.query.order_by(Category.name.asc()).all()
    category_names = [category.name for category in categories]
    category_flag_rules = {
        rule.category.name: rule.household_flag
        for rule in db.session.query(CategoryFlagRule).join(Category).all()
        if rule.category
    }
    smart_bulk_groups = _build_smart_review_groups(pending_transactions)
    return render_template(
        "reviews.html",
        pending_transactions=pending_transactions,
        reviewed_transactions=reviewed_transactions,
        categories=categories,
        category_names=category_names,
        category_flag_rules=category_flag_rules,
        smart_bulk_groups=smart_bulk_groups,
        household_flags=HOUSEHOLD_FLAGS,
    )


@bp.route("/reviews/bulk-apply", methods=["POST"])
def bulk_update_reviews():
    """Review a canonical pattern once, across variable amounts and references."""
    account_id = request.form.get("account_id", type=int)
    pattern_key = request.form.get("pattern_key", "").strip()
    direction = request.form.get("direction", "").strip()

    if not account_id or not pattern_key or direction not in {"in", "out"}:
        flash("Bulk update requires account, direction, and pattern details.", "error")
        return redirect(url_for("main.reviews"))

    category_name = request.form.get("category_name", "").strip()
    category_name_custom = request.form.get("category_name_custom", "").strip()
    if category_name == "__new__":
        category_name = ""
    category_name = category_name_custom or category_name

    household_flag = request.form.get("household_flag", "unknown").strip().lower()
    review_state = request.form.get("review_state", "reviewed").strip().lower()
    interlock_flag = request.form.get("interlock_flag") == "on"
    use_linked_flag = request.form.get("use_linked_flag") != "off"

    candidate_rows = (
        Transaction.query.filter_by(
            account_id=account_id,
            review_state="pending",
            excluded_from_analysis=False,
            internal_transfer=False,
        ).all()
    )

    target_ids = [
        txn.id for txn in candidate_rows
        if transaction_direction(txn.amount) == direction
        and _description_pattern_key(txn.cleaned_description, txn.amount) == pattern_key
    ]
    if not target_ids:
        flash("No matching pending transactions found for bulk update.", "error")
        return redirect(url_for("main.reviews"))

    category = None
    linked_rule = None
    if category_name:
        category = Category.query.filter_by(name=category_name).first()
        if not category:
            category = Category(name=category_name)
            db.session.add(category)
            db.session.flush()

        linked_rule = CategoryFlagRule.query.filter_by(category_id=category.id).first()
        if use_linked_flag and linked_rule:
            household_flag = linked_rule.household_flag

    if household_flag not in HOUSEHOLD_FLAGS:
        household_flag = "unknown"

    targets = Transaction.query.filter(Transaction.id.in_(target_ids), Transaction.excluded_from_analysis.is_(False), Transaction.internal_transfer.is_(False)).all()
    for target in targets:
        target.category_id = category.id if category else None
        target.household_flag = household_flag
        target.canonical_pattern = pattern_key
        target.payment_method = payment_method_for(target.cleaned_description, target.amount)
        target.review_state = "reviewed" if review_state == "reviewed" else "pending"

    _remember_pattern_rule(
        account_id, pattern_key, direction, category, household_flag,
        payment_method_for(targets[0].cleaned_description, targets[0].amount),
    )

    if interlock_flag and category:
        if not linked_rule:
            linked_rule = CategoryFlagRule(category_id=category.id, household_flag=household_flag)
            db.session.add(linked_rule)
        else:
            linked_rule.household_flag = household_flag

    db.session.commit()
    flash(f"Smart bulk update applied to {len(targets)} transaction(s).", "success")
    return redirect(url_for("main.reviews"))


@bp.route("/reviews/auto-align", methods=["POST"])
def auto_align_reviews():
    """Auto-align reviewed classification outliers for high-confidence repeat groups."""
    summary = _auto_align_reviewed_classifications()
    db.session.commit()
    flash(
        (
            f"Auto-align complete: {summary['rows_updated']} row(s) updated across "
            f"{summary['groups_aligned']} group(s). "
            f"Skipped {summary['skipped_ambiguous']} ambiguous group(s)."
        ),
        "success",
    )
    return redirect(url_for("main.reviews"))


@bp.route("/reviews/<int:transaction_id>", methods=["POST"])
def update_review(transaction_id):
    """Persist category, household flag, and review-state updates for one transaction."""
    transaction = Transaction.query.filter_by(id=transaction_id, excluded_from_analysis=False, internal_transfer=False).first()
    if not transaction:
        flash("Transaction not found.", "error")
        return redirect(url_for("main.reviews"))

    category_name = request.form.get("category_name", "").strip()
    category_name_custom = request.form.get("category_name_custom", "").strip()
    if category_name == "__new__":
        category_name = ""
    category_name = category_name_custom or category_name

    household_flag = request.form.get("household_flag", "unknown").strip().lower()
    review_state = request.form.get("review_state", "reviewed").strip().lower()
    apply_scope = request.form.get("apply_scope", "matching_description").strip().lower()
    interlock_flag = request.form.get("interlock_flag") == "on"
    use_linked_flag = request.form.get("use_linked_flag") != "off"

    category = None
    linked_rule = None

    if category_name:
        category = Category.query.filter_by(name=category_name).first()
        if not category:
            category = Category(name=category_name)
            db.session.add(category)
            db.session.flush()

        linked_rule = CategoryFlagRule.query.filter_by(category_id=category.id).first()
        if use_linked_flag and linked_rule:
            household_flag = linked_rule.household_flag

    if household_flag not in HOUSEHOLD_FLAGS:
        household_flag = "unknown"

    query = Transaction.query.filter_by(id=transaction.id, excluded_from_analysis=False, internal_transfer=False)
    if apply_scope == "matching_description":
        query = Transaction.query.filter_by(
            account_id=transaction.account_id,
            cleaned_description=transaction.cleaned_description,
            excluded_from_analysis=False,
            internal_transfer=False,
        )
    elif apply_scope == "matching_pattern":
        candidates = Transaction.query.filter_by(
            account_id=transaction.account_id,
            excluded_from_analysis=False,
            internal_transfer=False,
        ).all()
        pattern_key = description_pattern_key(transaction.cleaned_description, transaction.amount)
        direction = transaction_direction(transaction.amount)
        target_ids = [row.id for row in candidates if transaction_direction(row.amount) == direction and description_pattern_key(row.cleaned_description, row.amount) == pattern_key]
        query = Transaction.query.filter(Transaction.id.in_(target_ids))

    targets = query.all()
    for target in targets:
        target.category_id = category.id if category else None
        target.household_flag = household_flag
        target.canonical_pattern = description_pattern_key(target.cleaned_description, target.amount)
        target.payment_method = payment_method_for(target.cleaned_description, target.amount)
        target.review_state = "reviewed" if review_state == "reviewed" else "pending"

    if apply_scope == "matching_pattern" and targets:
        _remember_pattern_rule(
            transaction.account_id,
            description_pattern_key(transaction.cleaned_description, transaction.amount),
            transaction_direction(transaction.amount),
            category,
            household_flag,
            payment_method_for(transaction.cleaned_description, transaction.amount),
        )

    if interlock_flag and category:
        if not linked_rule:
            linked_rule = CategoryFlagRule(category_id=category.id, household_flag=household_flag)
            db.session.add(linked_rule)
        else:
            linked_rule.household_flag = household_flag

    db.session.commit()

    flash(
        f"Updated {len(targets)} transaction(s) for description '{transaction.cleaned_description}'.",
        "success",
    )
    return redirect(url_for("main.reviews"))


def get_or_create_default_account():
    """Return the default account for imports, creating a placeholder user/account when missing."""
    default_user_name = os.environ.get("DEFAULT_USER_NAME", "Sample User")
    default_account_name = os.environ.get("DEFAULT_ACCOUNT_NAME", "Default Account")

    user = User.query.first()
    if not user:
        user = User(name=default_user_name)
        db.session.add(user)
        db.session.flush()

    account = Account.query.filter_by(user_id=user.id, name=default_account_name).first()
    if not account:
        account = Account(user_id=user.id, name=default_account_name, account_type="checking")
        db.session.add(account)
        db.session.commit()

    return account


@bp.route("/imports", methods=["GET", "POST"])
def imports():
    """Handle statement uploads and render import metadata history."""
    statement_type_options = [
        ("auto", DOCUMENT_TYPE_LABELS["auto"]),
        ("bank", DOCUMENT_TYPE_LABELS["bank"]),
        ("paypal", DOCUMENT_TYPE_LABELS["paypal"]),
        ("credit_union", DOCUMENT_TYPE_LABELS["credit_union"]),
    ]

    if request.method == "POST":
        csv_file = request.files.get("csv_file")
        statement_type = request.form.get("statement_type", "auto")
        statement_bank_name = request.form.get("statement_bank_name", "").strip()
        statement_account_key = request.form.get("statement_account_key", "").strip()
        if not csv_file or not csv_file.filename:
            flash("Please choose a CSV or PDF statement file.", "error")
            return redirect(url_for("main.imports"))

        account_id = request.form.get("account_id", type=int)
        account = db.session.get(Account, account_id) if account_id else None
        if not account:
            flash("Select the financial account this statement belongs to.", "error")
            return redirect(url_for("main.imports"))

        try:
            result = import_transactions(
                csv_file,
                account.id,
                declared_source=statement_type,
                manual_account_key=statement_account_key,
                manual_bank_name=statement_bank_name,
            )
            reconciled_count = result.get("reconciled", 0)
            unmatched_paypal = result.get("paypal_unmatched", 0)

            if result["created"] == 0 and reconciled_count == 0:
                unmatched_text = (
                    f" {unmatched_paypal} unmatched PayPal rows were skipped."
                    if unmatched_paypal
                    else ""
                )
                flash(
                    (
                        "No new transactions were imported. "
                        f"{result['duplicates']} duplicate rows were skipped."
                        f"{unmatched_text}"
                    ),
                    "error",
                )
            else:
                reconcile_text = (
                    f", {reconciled_count} bank transactions enriched from PayPal details"
                    if reconciled_count
                    else ""
                )
                unmatched_text = (
                    f", {unmatched_paypal} unmatched PayPal rows skipped"
                    if unmatched_paypal
                    else ""
                )
                flash(
                    (
                        f"Import complete: {result['created']} created, "
                        f"{result['duplicates']} duplicate rows skipped"
                        f"{reconcile_text}"
                        f"{unmatched_text}."
                    ),
                    "success",
                )
        except CSVImportError as exc:
            db.session.rollback()
            flash(str(exc), "error")

        return redirect(url_for("main.imports", import_page=1))

    import_page = request.args.get("import_page", default=1, type=int)
    import_page = import_page if import_page and import_page > 0 else 1
    per_page = 5

    # Keep the most recent import in its own summary card.
    latest_import = (
        ImportBatch.query.order_by(ImportBatch.imported_at.desc(), ImportBatch.id.desc())
        .first()
    )
    # Paginated history excludes the most recent import already shown above.
    history_query = ImportBatch.query
    if latest_import:
        history_query = history_query.filter(ImportBatch.id != latest_import.id)

    history_query = history_query.order_by(ImportBatch.imported_at.desc(), ImportBatch.id.desc())
    history_total = history_query.count()
    max_pages = max((history_total + per_page - 1) // per_page, 1)
    import_page = min(import_page, max_pages)
    import_history = (
        history_query.offset((import_page - 1) * per_page).limit(per_page).all()
    )

    return render_template(
        "imports.html",
        latest_import=latest_import,
        import_history=import_history,
        import_page=import_page,
        import_max_pages=max_pages,
        source_needs_account_key=SOURCE_NEEDS_ACCOUNT_KEY,
        statement_type_options=statement_type_options,
        accounts=Account.query.order_by(Account.name).all(),
        duplicate_candidate_count=len(verified_duplicate_rows(db.session)),
    )


@bp.route("/imports/amend-metadata", methods=["POST"])
def amend_import_metadata():
    """Run legacy import metadata maintenance only after explicit confirmation."""
    amended = amend_existing_import_metadata(db.session)
    db.session.commit()
    flash(f"Import metadata maintenance complete: {amended} batch(es) amended.", "success")
    return redirect(url_for("main.imports"))


@bp.route("/imports/exclude-duplicates", methods=["POST"])
def exclude_duplicate_import_rows():
    """Explicitly exclude verified later cross-batch duplicates without deleting raw rows."""
    excluded = exclude_verified_duplicates(db.session)
    db.session.commit()
    flash(f"Excluded {excluded} verified duplicate transaction(s).", "success")
    return redirect(url_for("main.imports"))


@bp.route("/imports/update-account-key", methods=["POST"])
def update_import_account_key():
    """Update stored account key metadata for an existing import batch."""
    batch_id = request.form.get("batch_id", type=int)
    account_key = request.form.get("account_key", "").strip()
    import_page = request.form.get("import_page", default=1, type=int)

    if not batch_id or not account_key:
        flash("Batch and account key are required.", "error")
        return redirect(url_for("main.imports", import_page=import_page))

    batch = ImportBatch.query.filter_by(id=batch_id).first()
    if not batch or not batch.statement_import:
        flash("Import batch metadata not found.", "error")
        return redirect(url_for("main.imports", import_page=import_page))

    batch.statement_import.account_key = account_key
    db.session.commit()
    flash(f"Account key updated for Batch #{batch.id}.", "success")
    return redirect(url_for("main.imports", import_page=import_page))

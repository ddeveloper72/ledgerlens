import os
from collections import defaultdict
from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import Account, Category, ImportBatch, Transaction, User
from app.services.csv_import import (
    CSVImportError,
    DOCUMENT_TYPE_LABELS,
    SOURCE_NEEDS_ACCOUNT_KEY,
    amend_existing_import_metadata,
    backfill_paypal_alternate_descriptions,
    import_transactions,
)
from app.services.financial_intelligence import (
    apply_mapping_to_pending_transactions,
    cash_flow_calendar,
    ensure_category,
    ensure_merchant_with_alias,
    household_analytics_snapshot,
    infer_financial_labels,
    recurring_expected_vs_missing,
    savings_recovery_summary,
    sync_recurring_bills,
)

bp = Blueprint("main", __name__)

HOUSEHOLD_FLAGS = ["household", "personal", "shared", "reimbursable", "unknown"]


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
    total_transactions = Transaction.query.count()
    pending_transactions = Transaction.query.filter_by(review_state="pending").count()
    month_start, next_month_start = _month_bounds()
    month_transactions = (
        Transaction.query.filter(
            Transaction.posted_date >= month_start,
            Transaction.posted_date < next_month_start,
        )
        .order_by(Transaction.posted_date.desc(), Transaction.id.desc())
        .all()
    )

    monthly_income = Decimal("0.00")
    monthly_spending = Decimal("0.00")
    household_spending = Decimal("0.00")
    category_totals = defaultdict(lambda: Decimal("0.00"))

    for txn in month_transactions:
        amount = Decimal(txn.amount)
        if amount > 0:
            monthly_income += amount
        else:
            expense = abs(amount)
            monthly_spending += expense
            if txn.household_flag == "household":
                household_spending += expense

            category_name = txn.category.name if txn.category else "Uncategorized"
            category_totals[category_name] += expense

    uncategorised_transactions = (
        Transaction.query.outerjoin(Category)
        .filter(
            db.or_(
                Transaction.category_id.is_(None),
                Category.name == "Uncategorized",
            )
        )
        .count()
    )

    top_categories = sorted(category_totals.items(), key=lambda item: item[1], reverse=True)[:5]
    recurring_snapshot = recurring_expected_vs_missing(db.session)
    cash_calendar = cash_flow_calendar(db.session)
    analytics_snapshot = household_analytics_snapshot(db.session)
    recovery_snapshot = savings_recovery_summary(db.session)
    recent_transactions = (
        Transaction.query.order_by(Transaction.posted_date.desc(), Transaction.id.desc())
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
        uncategorised_transactions=uncategorised_transactions,
        top_categories=top_categories,
        recurring_expected=recurring_snapshot["expected"],
        recurring_missing=recurring_snapshot["missing"],
        cash_calendar=cash_calendar,
        analytics_snapshot=analytics_snapshot,
        recovery_snapshot=recovery_snapshot,
        recent_transactions=recent_transactions,
    )

@bp.route("/intelligence", methods=["GET", "POST"])
def intelligence():
    """Manage merchant mappings and preview recurring/cash-flow intelligence outputs."""
    if request.method == "POST":
        alias_text = request.form.get("alias_text", "").strip()
        merchant_name = request.form.get("merchant_name", "").strip()
        category_name = request.form.get("category_name", "").strip() or "Uncategorized"
        apply_existing = request.form.get("apply_existing") == "on"

        if not alias_text or not merchant_name:
            flash("Alias and merchant name are required.", "error")
            return redirect(url_for("main.intelligence"))

        merchant = ensure_merchant_with_alias(db.session, alias_text, merchant_name)
        category = ensure_category(db.session, category_name)
        updated_count = 0
        if apply_existing:
            updated_count = apply_mapping_to_pending_transactions(
                db.session,
                alias_text,
                merchant.name,
                category.name,
            )

        db.session.commit()
        message = f"Mapping saved for alias '{alias_text}'."
        if updated_count:
            message += f" Updated {updated_count} pending transaction(s)."
        flash(message, "success")
        return redirect(url_for("main.intelligence"))

    recurring_candidates = sync_recurring_bills(db.session)
    db.session.commit()
    recurring_snapshot = recurring_expected_vs_missing(db.session)
    cash_calendar = cash_flow_calendar(db.session)

    recent_transactions = (
        Transaction.query.order_by(Transaction.posted_date.desc(), Transaction.id.desc())
        .limit(20)
        .all()
    )
    merchant_journey = []
    for txn in recent_transactions:
        merchant_name = txn.merchant.name if txn.merchant else "Unknown"
        category_name = txn.category.name if txn.category else "Uncategorized"
        labels = infer_financial_labels(merchant_name, category_name, txn.cleaned_description)
        merchant_journey.append(
            {
                "transaction": txn,
                "merchant_name": merchant_name,
                "category_name": category_name,
                "labels": labels,
            }
        )

    return render_template(
        "intelligence.html",
        recurring_candidates=recurring_candidates,
        recurring_expected=recurring_snapshot["expected"],
        recurring_missing=recurring_snapshot["missing"],
        cash_calendar=cash_calendar,
        merchant_journey=merchant_journey,
    )


@bp.route("/savings-recovery", methods=["GET", "POST"])
def savings_recovery():
    """Track emergency-fund target and progress toward recovery."""
    if request.method == "POST":
        target_amount = request.form.get("target_amount", type=float)
        current_amount = request.form.get("current_amount", type=float)
        goal_name = request.form.get("goal_name", "Emergency Fund").strip() or "Emergency Fund"

        if target_amount is None or current_amount is None:
            flash("Current and target amounts are required.", "error")
            return redirect(url_for("main.savings_recovery"))

        existing = (
            db.session.query(User).first()
        )
        if not existing:
            user = User(name=os.environ.get("DEFAULT_USER_NAME", "Sample User"))
            db.session.add(user)
            db.session.flush()

        from app.models import SavingsGoal

        goal = (
            db.session.query(SavingsGoal)
            .filter(SavingsGoal.name.ilike("%emergency%"))
            .first()
        )
        if not goal:
            goal = SavingsGoal(name=goal_name, target_amount=Decimal("0.00"), current_amount=Decimal("0.00"))
            db.session.add(goal)

        goal.name = goal_name
        goal.target_amount = Decimal(str(target_amount)).quantize(Decimal("0.01"))
        goal.current_amount = Decimal(str(current_amount)).quantize(Decimal("0.01"))
        db.session.commit()
        flash("Savings recovery goal updated.", "success")
        return redirect(url_for("main.savings_recovery"))

    return render_template(
        "savings_recovery.html",
        recovery_snapshot=savings_recovery_summary(db.session),
    )

@bp.route("/accounts", methods=["GET", "POST"])
def accounts():
    """Create and list local accounts used for transaction imports."""
    if request.method == "POST":
        account_name = request.form.get("account_name", "").strip()
        account_type = request.form.get("account_type", "checking").strip().lower() or "checking"

        if not account_name:
            flash("Account name is required.", "error")
            return redirect(url_for("main.accounts"))

        user = User.query.first()
        if not user:
            user = User(name=os.environ.get("DEFAULT_USER_NAME", "Sample User"))
            db.session.add(user)
            db.session.flush()

        account = Account(user_id=user.id, name=account_name, account_type=account_type)
        db.session.add(account)
        db.session.commit()
        flash(f"Account '{account_name}' created.", "success")
        return redirect(url_for("main.accounts"))

    account_rows = []
    for account in Account.query.order_by(Account.name.asc()).all():
        balance = (
            db.session.query(db.func.coalesce(db.func.sum(Transaction.amount), Decimal("0.00")))
            .filter(Transaction.account_id == account.id)
            .scalar()
        )
        transaction_count = Transaction.query.filter_by(account_id=account.id).count()
        account_rows.append(
            {
                "account": account,
                "balance": balance,
                "transaction_count": transaction_count,
            }
        )

    return render_template("accounts.html", account_rows=account_rows)


@bp.route("/transactions")
def transactions():
    """Render imported transactions with standalone PayPal rows hidden."""
    backfill_paypal_alternate_descriptions(db.session)
    db.session.commit()
    txns = (
        Transaction.query
        .filter(~Transaction.cleaned_description.like("PayPal %"))
        .order_by(Transaction.posted_date.desc(), Transaction.id.desc())
        .all()
    )
    return render_template("transactions.html", transactions=txns)


@bp.route("/reviews")
def reviews():
    """Show transactions that still need category/flag review."""
    pending_transactions = (
        Transaction.query.filter_by(review_state="pending")
        .order_by(Transaction.posted_date.desc(), Transaction.id.desc())
        .all()
    )
    categories = Category.query.order_by(Category.name.asc()).all()
    return render_template(
        "reviews.html",
        pending_transactions=pending_transactions,
        categories=categories,
        household_flags=HOUSEHOLD_FLAGS,
    )


@bp.route("/reviews/<int:transaction_id>", methods=["POST"])
def update_review(transaction_id):
    """Persist category, household flag, and review-state updates for one transaction."""
    transaction = Transaction.query.filter_by(id=transaction_id).first()
    if not transaction:
        flash("Transaction not found.", "error")
        return redirect(url_for("main.reviews"))

    category_name = request.form.get("category_name", "").strip()
    household_flag = request.form.get("household_flag", "unknown").strip().lower()
    review_state = request.form.get("review_state", "reviewed").strip().lower()

    if category_name:
        category = Category.query.filter_by(name=category_name).first()
        if not category:
            category = Category(name=category_name)
            db.session.add(category)
            db.session.flush()
        transaction.category_id = category.id

    if household_flag not in HOUSEHOLD_FLAGS:
        household_flag = "unknown"

    transaction.household_flag = household_flag
    transaction.review_state = "reviewed" if review_state == "reviewed" else "pending"
    db.session.commit()

    flash(f"Transaction #{transaction.id} updated.", "success")
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

        account = get_or_create_default_account()

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

    amend_existing_import_metadata(db.session)
    db.session.commit()

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
    )


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

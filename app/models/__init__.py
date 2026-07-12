from datetime import datetime

from app.extensions import db


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    accounts = db.relationship("Account", backref="user", lazy=True)


class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    account_type = db.Column(db.String(50), nullable=False, default="checking")
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    transactions = db.relationship("Transaction", backref="account", lazy=True)


class Merchant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)


class MerchantAlias(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alias = db.Column(db.String(120), unique=True, nullable=False)
    merchant_id = db.Column(db.Integer, db.ForeignKey("merchant.id"), nullable=False)
    origin = db.Column(db.String(20), nullable=False, default="manual")
    active = db.Column(db.Boolean, nullable=False, default=True)

    merchant = db.relationship("Merchant", backref="aliases")


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)


class CategoryFlagRule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False, unique=True)
    household_flag = db.Column(db.String(20), nullable=False, default="unknown")

    category = db.relationship("Category")


class ImportBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source_filename = db.Column(db.String(255), nullable=False)
    imported_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    row_count = db.Column(db.Integer, nullable=False, default=0)

    transactions = db.relationship("Transaction", backref="import_batch", lazy=True)
    statement_import = db.relationship(
        "StatementImport", backref="import_batch", uselist=False, lazy=True
    )


class StatementImport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    import_batch_id = db.Column(db.Integer, db.ForeignKey("import_batch.id"), nullable=False, unique=True)
    fingerprint = db.Column(db.String(64), nullable=False, unique=True)
    declared_source = db.Column(db.String(40), nullable=False, default="auto")
    detected_source = db.Column(db.String(40), nullable=False, default="generic")
    bank_name = db.Column(db.String(40), nullable=True)
    account_key = db.Column(db.String(64), nullable=True)
    statement_start_date = db.Column(db.Date, nullable=True)
    statement_end_date = db.Column(db.Date, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.now, nullable=False)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    import_batch_id = db.Column(db.Integer, db.ForeignKey("import_batch.id"), nullable=True)

    posted_date = db.Column(db.Date, nullable=False)
    original_description = db.Column(db.String(255), nullable=False)
    cleaned_description = db.Column(db.String(255), nullable=False)

    merchant_id = db.Column(db.Integer, db.ForeignKey("merchant.id"), nullable=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)

    amount = db.Column(db.Numeric(12, 2), nullable=False)
    household_flag = db.Column(db.String(20), nullable=False, default="unknown")
    notes = db.Column(db.Text, nullable=True)
    review_state = db.Column(db.String(20), nullable=False, default="pending")
    excluded_from_analysis = db.Column(db.Boolean, nullable=False, default=False)
    exclusion_reason = db.Column(db.String(120), nullable=True)
    excluded_at = db.Column(db.DateTime, nullable=True)
    internal_transfer = db.Column(db.Boolean, nullable=False, default=False)
    internal_transfer_reason = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    merchant = db.relationship("Merchant")
    category = db.relationship("Category")


class RecurringBill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    merchant_id = db.Column(db.Integer, db.ForeignKey("merchant.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    expected_amount = db.Column(db.Numeric(12, 2), nullable=True)
    cadence = db.Column(db.String(30), nullable=False, default="monthly")
    display_name = db.Column(db.String(120), nullable=True)
    amount_tolerance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    expected_next_date = db.Column(db.Date, nullable=True)
    household_flag = db.Column(db.String(20), nullable=False, default="unknown")
    active = db.Column(db.Boolean, nullable=False, default=True)

    merchant = db.relationship("Merchant")
    category = db.relationship("Category")


class RecurringCandidate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    merchant_id = db.Column(db.Integer, db.ForeignKey("merchant.id"), nullable=True)
    normalized_description = db.Column(db.String(120), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    observed_count = db.Column(db.Integer, nullable=False)
    first_observed_date = db.Column(db.Date, nullable=False)
    last_observed_date = db.Column(db.Date, nullable=False)
    typical_amount = db.Column(db.Numeric(12, 2), nullable=False)
    amount_variation = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    frequency = db.Column(db.String(20), nullable=False, default="irregular")
    estimated_next_date = db.Column(db.Date, nullable=True)
    confidence_score = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="pending")
    amount_tolerance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    household_flag = db.Column(db.String(20), nullable=False, default="unknown")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    merchant = db.relationship("Merchant")
    category = db.relationship("Category")


class SavingsGoal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    target_amount = db.Column(db.Numeric(12, 2), nullable=False)
    current_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    target_date = db.Column(db.Date, nullable=True)
    repayment_per_payday = db.Column(db.Numeric(12, 2), nullable=True)


class SavingsRecoveryEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    savings_goal_id = db.Column(db.Integer, db.ForeignKey("savings_goal.id"), nullable=False)
    event_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    event_type = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(120), nullable=False)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    savings_goal = db.relationship("SavingsGoal", backref="recovery_events")


class IncomeSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(120), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    frequency = db.Column(db.String(20), nullable=False)
    next_expected_date = db.Column(db.Date, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    account = db.relationship("Account")


class PlannedCommitment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(120), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    household_flag = db.Column(db.String(20), nullable=False, default="household")
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    frequency = db.Column(db.String(20), nullable=False)
    next_expected_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    commitment_type = db.Column(db.String(20), nullable=False, default="other")
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    category = db.relationship("Category")


class OneOffForecastEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    event_date = db.Column(db.Date, nullable=False)
    direction = db.Column(db.String(10), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    household_flag = db.Column(db.String(20), nullable=False, default="household")
    status = db.Column(db.String(20), nullable=False, default="planned")
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    category = db.relationship("Category")


class SinkingFundProvision(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(120), nullable=False)
    target_amount = db.Column(db.Numeric(12, 2), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    amount_reserved = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    savings_goal_id = db.Column(db.Integer, db.ForeignKey("savings_goal.id"), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    savings_goal = db.relationship("SavingsGoal")

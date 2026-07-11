import re

from app.models import Category, CategoryFlagRule, Transaction

KEYWORD_CATEGORY_MAP = {
    "tv lic": "Tax",
    "tv licence": "Tax",
    "an post tv": "Tax",
    "property tax": "Tax",
    "car tax": "Tax",
    "motor tax": "Tax",
    "home insurance": "Insurance",
    "house insurance": "Insurance",
    "car insurance": "Insurance",
    "motor insurance": "Insurance",
    "health insurance": "Insurance",
    "pet insurance": "Insurance",
    "insurance": "Insurance",
    "grocery": "Groceries",
    "market": "Groceries",
    "utility": "Utilities",
    "electric": "Utilities",
    "water": "Utilities",
    "fuel": "Transport",
    "transit": "Transport",
    "subscription": "Subscriptions",
    "streaming": "Subscriptions",
    "restaurant": "Dining",
    "coffee": "Dining",
    "transfer": "Transfers",
    "credit union": "Transfers",
    "drawdown": "Transfers",
    "draw down": "Transfers",
    "repayment": "Loan Payments",
    "loan": "Loan Payments",
}


def get_or_create_category(session, category_name):
    """Fetch an existing category by name or create it on demand."""
    category = session.query(Category).filter_by(name=category_name).first()
    if not category:
        category = Category(name=category_name)
        session.add(category)
        session.flush()

    if category_name == "Insurance Claims":
        rule = session.query(CategoryFlagRule).filter_by(category_id=category.id).first()
        if not rule:
            session.add(CategoryFlagRule(category_id=category.id, household_flag="household"))

    return category


def assign_category(session, merchant_name, description, amount=None):
    """Assign a category using keyword mapping over merchant/description and optional amount direction."""
    text = f"{merchant_name} {description}".lower()

    # Claims paid into your account by health insurers should not be mixed with premium outflows.
    if amount is not None and amount > 0:
        if any(keyword in text for keyword in ["vhi", "health insurance", "insurance claim", "claim refund"]):
            return get_or_create_category(session, "Insurance Claims")

    if re.search(r"\blpt\b", text):
        return get_or_create_category(session, "Tax")

    for keyword, category_name in KEYWORD_CATEGORY_MAP.items():
        if keyword in text:
            return get_or_create_category(session, category_name)

    return get_or_create_category(session, "Uncategorized")


def backfill_pending_categories(session):
    """Categorize pending uncategorized rows without overwriting reviewed decisions."""
    rows = (
        session.query(Transaction)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .filter(Transaction.review_state == "pending")
        .filter(Transaction.excluded_from_analysis.is_(False))
        .filter(
            (Transaction.category_id.is_(None)) | (Category.name == "Uncategorized")
        )
        .all()
    )

    updated = 0
    for transaction in rows:
        merchant_name = transaction.merchant.name if transaction.merchant else ""
        category = assign_category(
            session,
            merchant_name,
            transaction.cleaned_description,
            transaction.amount,
        )
        if category.name == "Uncategorized":
            continue
        transaction.category_id = category.id
        if category.name == "Insurance Claims" and transaction.household_flag == "unknown":
            transaction.household_flag = "household"
        updated += 1

    return updated

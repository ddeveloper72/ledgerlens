from app.models import Category

KEYWORD_CATEGORY_MAP = {
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
    if category:
        return category

    category = Category(name=category_name)
    session.add(category)
    session.flush()
    return category


def assign_category(session, merchant_name, description):
    """Assign a category using keyword mapping over merchant and description text."""
    text = f"{merchant_name} {description}".lower()

    for keyword, category_name in KEYWORD_CATEGORY_MAP.items():
        if keyword in text:
            return get_or_create_category(session, category_name)

    return get_or_create_category(session, "Uncategorized")

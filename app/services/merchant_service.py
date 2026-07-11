import re

from app.models import Category, Merchant, MerchantAlias, Transaction


def canonical_merchant_hint(description):
    """Convert noisy bank text into a stable, bounded alias hint."""
    value = (description or "").strip().lower()
    value = re.sub(r"^(d/d|card|pos)\s+", "", value)
    value = re.sub(r"^(paypal)\s*[\*-]?\s*", "", value)
    return re.sub(r"\s+", " ", value)[:120]


def ensure_category(session, category_name):
    """Return a category by name, creating it only in an explicit write workflow."""
    name = (category_name or "Uncategorized").strip() or "Uncategorized"
    category = session.query(Category).filter_by(name=name).first()
    if not category:
        category = Category(name=name)
        session.add(category)
        session.flush()
    return category


def save_mapping(session, alias_text, merchant_name, *, origin="manual"):
    """Create or update an active merchant alias mapping."""
    merchant = session.query(Merchant).filter_by(name=merchant_name.strip()).first()
    if not merchant:
        merchant = Merchant(name=merchant_name.strip())
        session.add(merchant)
        session.flush()
    alias_value = canonical_merchant_hint(alias_text)
    alias = session.query(MerchantAlias).filter_by(alias=alias_value).first()
    if not alias:
        alias = MerchantAlias(alias=alias_value, merchant_id=merchant.id)
        session.add(alias)
    alias.merchant_id = merchant.id
    alias.origin = origin
    alias.active = True
    session.flush()
    return alias


def preview_mapping_count(session, alias_text):
    """Count pending rows affected by an alias without changing them."""
    alias_value = canonical_merchant_hint(alias_text)
    if not alias_value:
        return 0
    return sum(
        alias_value in canonical_merchant_hint(txn.cleaned_description)
        for txn in session.query(Transaction).filter_by(review_state="pending", excluded_from_analysis=False).all()
    )


def apply_mapping(session, alias, category_name):
    """Apply a saved active mapping after explicit confirmation."""
    if not alias.active:
        return 0
    category = ensure_category(session, category_name)
    updated = 0
    for txn in session.query(Transaction).filter_by(review_state="pending", excluded_from_analysis=False).all():
        if alias.alias not in canonical_merchant_hint(txn.cleaned_description):
            continue
        txn.merchant_id = alias.merchant_id
        txn.category_id = category.id
        updated += 1
    session.flush()
    return updated


def infer_financial_labels(merchant_name, category_name, description):
    """Infer explainable display labels without persisting a classification."""
    merchant_text = (merchant_name or "").lower()
    category_text = (category_name or "Uncategorized").lower()
    description_text = (description or "").lower()
    domain, subtype, recurrence = "General", "Variable", "Ad hoc"
    if any(key in merchant_text for key in ["microsoft", "google", "github"]):
        domain = "Technology"
    elif "spotify" in merchant_text or "broadband" in description_text:
        domain = "Subscriptions"
    elif "mortgage" in description_text or "loan" in category_text:
        domain = "Debt"
    elif "insurance" in description_text:
        domain = "Insurance"
    if "subscription" in category_text or "subscription" in description_text:
        subtype, recurrence = "Subscription", "Recurring Monthly"
    elif "mortgage" in description_text:
        subtype, recurrence = "Mortgage", "Recurring Monthly"
    elif "electric" in description_text or "utility" in category_text:
        subtype, recurrence = "Utilities", "Recurring Monthly"
    return {"domain": domain, "subtype": subtype, "recurrence": recurrence}


# Compatibility names retained for existing callers.
ensure_merchant_with_alias = save_mapping


def apply_mapping_to_pending_transactions(session, alias_text, merchant_name, category_name):
    alias = save_mapping(session, alias_text, merchant_name)
    return apply_mapping(session, alias, category_name)

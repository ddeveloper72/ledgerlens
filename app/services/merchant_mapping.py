from app.models import Merchant, MerchantAlias


KNOWN_PAYEE_NAMES = {
    "an post tv lic": "An Post TV Licence",
}


def canonical_payee_name(description):
    """Return a stable display merchant for user-confirmed noisy payee descriptions."""
    lowered = (description or "").strip().lower()
    return next((name for marker, name in KNOWN_PAYEE_NAMES.items() if marker in lowered), description.strip())


def resolve_merchant(session, description):
    """Resolve a merchant from alias matches, falling back to exact-name lookup."""
    description_lower = description.strip().lower()
    aliases = session.query(MerchantAlias).filter_by(active=True).all()

    for alias in aliases:
        if alias.alias.lower() in description_lower:
            return alias.merchant

    merchant = session.query(Merchant).filter_by(name=description.strip()).first()
    return merchant


def create_or_get_merchant(session, name):
    """Return an existing merchant by name or create a new one."""
    canonical_name = canonical_payee_name(name)
    merchant = session.query(Merchant).filter_by(name=canonical_name).first()
    if merchant:
        return merchant

    merchant = Merchant(name=canonical_name)
    session.add(merchant)
    session.flush()
    return merchant


def canonicalize_known_payees(session):
    """Reassign historical noisy descriptions to stable known-payee merchant records."""
    from app.models import Transaction

    updated = 0
    for marker, display_name in KNOWN_PAYEE_NAMES.items():
        merchant = session.query(Merchant).filter_by(name=display_name).first()
        if not merchant:
            merchant = Merchant(name=display_name)
            session.add(merchant)
            session.flush()
        rows = session.query(Transaction).filter(Transaction.cleaned_description.ilike(f"%{marker}%")).all()
        for row in rows:
            if row.merchant_id == merchant.id:
                continue
            row.merchant_id = merchant.id
            updated += 1
    session.flush()
    return updated

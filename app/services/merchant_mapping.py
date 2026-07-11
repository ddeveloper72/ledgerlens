from app.models import Merchant, MerchantAlias


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
    merchant = session.query(Merchant).filter_by(name=name.strip()).first()
    if merchant:
        return merchant

    merchant = Merchant(name=name.strip())
    session.add(merchant)
    session.flush()
    return merchant

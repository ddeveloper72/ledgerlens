import re

from app.models import Transaction


def description_pattern_key(description):
    """Normalize changing numeric references while preserving stable payee text."""
    normalized = " ".join((description or "").upper().split())
    normalized = re.sub(r"\d{3,}", "<NUMSEQ>", normalized)
    normalized = re.sub(r"\b\d+\b", "<NUM>", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def learned_pattern_classification(session, account_id, description, min_examples=2):
    """Return a unanimous reviewed classification for a normalized description pattern."""
    pattern = description_pattern_key(description)
    if not pattern:
        return None
    reviewed = (
        session.query(Transaction)
        .filter_by(
            account_id=account_id,
            review_state="reviewed",
            excluded_from_analysis=False,
            internal_transfer=False,
        )
        .all()
    )
    matches = [row for row in reviewed if description_pattern_key(row.cleaned_description) == pattern]
    if len(matches) < min_examples:
        return None
    category_ids = {row.category_id for row in matches}
    flags = {row.household_flag for row in matches}
    if len(category_ids) != 1 or None in category_ids or len(flags) != 1:
        return None
    merchant_ids = {row.merchant_id for row in matches}
    return {
        "category_id": category_ids.pop(),
        "household_flag": flags.pop(),
        "merchant_id": merchant_ids.pop() if len(merchant_ids) == 1 and None not in merchant_ids else None,
        "examples": len(matches),
        "pattern_key": pattern,
    }

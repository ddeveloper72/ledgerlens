import re

from app.models import Transaction, TransactionPatternRule


PAYMENT_METHODS = {
    "direct_debit": "Direct debit",
    "card": "Visa debit / card",
    "mobile_transfer": "Mobile transfer",
    "sepa_transfer": "SEPA transfer",
    "payroll": "Salary / payroll",
    "cash": "Cash withdrawal",
    "bank_transfer": "Bank transfer",
    "unknown": "Unknown",
}


def transaction_description_context(description, amount=None):
    """Separate a bank narrative into channel, note/reference, and counterparty hint.

    This is deliberately conservative: ambiguous mobile notes, account aliases,
    payroll identifiers, and changing bank references never become counterparties.
    """
    raw = " ".join((description or "").strip().split())
    upper = raw.upper()
    method = payment_method_for(raw, amount)
    counterparty = None
    user_note = None
    reference_kind = None
    if method == "mobile_transfer":
        user_note = re.sub(r"^\*MOBI\s+", "", raw, flags=re.IGNORECASE).strip() or None
        reference_kind = "user_note"
    elif re.fullmatch(r"(?:\d{2}-)?\d{10,}", raw) or re.fullmatch(r"IE\d{8,}", upper):
        reference_kind = "sensitive_reference"
    elif re.fullmatch(r"CURRENT-\d{3,4}", upper):
        reference_kind = "account_alias"
    elif method == "direct_debit":
        candidate = re.sub(r"^(D/D|DD)\s+", "", raw, flags=re.IGNORECASE)
        candidate = re.sub(r"\s+IE\d{8,}.*$", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s+-?\s*(?:UNP\s*-\s*)?\d{5,}.*$", "", candidate, flags=re.IGNORECASE)
        counterparty = candidate.strip(" -") or None
        reference_kind = "bank_reference" if counterparty != re.sub(r"^(D/D|DD)\s+", "", raw, flags=re.IGNORECASE) else None
    elif method in {"card", "unknown"} and not re.search(r"\b(?:IE\d{8,}|\d{10,}|CURRENT-\d{3,4})\b", upper):
        candidate = re.sub(r"^(VDP|VDC|CARD|POS)[- ]+", "", raw, flags=re.IGNORECASE).strip()
        counterparty = candidate or None
    if amount is not None and amount > 0 and (reference_kind == "sensitive_reference" or "PAYROLL" in upper):
        reference_kind = "payroll_reference"
        counterparty = None
    return {"raw_description": raw, "payment_method": method,
            "payment_method_label": PAYMENT_METHODS.get(method, "Unknown"),
            "counterparty_hint": counterparty, "user_note": user_note,
            "reference_kind": reference_kind,
            "contains_sensitive_reference": reference_kind in {"sensitive_reference", "payroll_reference"}}


def is_counterparty_candidate(value):
    """Reject raw narratives and identifiers from canonical counterparty choices."""
    context = transaction_description_context(value)
    text = (value or "").strip()
    if not text or context["user_note"] or context["contains_sensitive_reference"]:
        return False
    if context["reference_kind"] == "account_alias" or text.upper().startswith(("D/D ", "DD ", "SEPA ")):
        return False
    return not bool(re.search(r"\b(?:IE\d{8,}|\d{10,}|CURRENT-\d{3,4})\b", text.upper()))


def payment_method_for(description, amount=None):
    """Classify the payment rail without treating it as a spending category."""
    text = " ".join((description or "").upper().split())
    if text.startswith("D/D ") or text.startswith("DD "):
        return "direct_debit"
    if text.startswith(("VDP-", "VDC-")):
        return "card"
    if text.startswith("*MOBI "):
        return "mobile_transfer"
    if text.startswith("SEPA "):
        return "sepa_transfer"
    if "PAYROLL" in text or "PAYPATH" in text:
        return "payroll"
    if text.startswith(("ATM ", "CASH ")):
        return "cash"
    if text.startswith(("EFT ", "TRANSFER ")):
        return "bank_transfer"
    return "unknown"


def description_pattern_key(description, amount=None):
    """Return a stable payee pattern while preserving the payment rail separately."""
    normalized = " ".join((description or "").upper().split())
    method = payment_method_for(normalized, amount)
    normalized = re.sub(r"^(D/D|DD)\s+", "", normalized)
    normalized = re.sub(r"^(VDP|VDC)-", "", normalized)
    normalized = re.sub(r"^\*MOBI\s+", "", normalized)
    normalized = re.sub(r"^SEPA\s+(PYMT|PAYMENT)?\s*", "", normalized)
    # Short account suffixes identify distinct transfer sources/destinations and
    # must not be erased with changing authorization/reference numbers.
    account_suffixes = []
    def preserve_account_suffix(match):
        account_suffixes.append(match.group(0))
        return f"ACCOUNTSUFFIXTOKEN{len(account_suffixes) - 1}"
    normalized = re.sub(r"\bCURRENT-\d{3,4}\b", preserve_account_suffix, normalized)
    normalized = re.sub(r"\bIE\d{8,}\b", "<BANKREF>", normalized)
    normalized = re.sub(r"\b(?:REF|REFERENCE|AUTH|ID)[- :]*[A-Z0-9-]{5,}\b", "<REF>", normalized)
    normalized = re.sub(r"\d{3,}", "<NUMSEQ>", normalized)
    normalized = re.sub(r"\b\d+\b", "<NUM>", normalized)
    for index, value in enumerate(account_suffixes):
        normalized = normalized.replace(f"ACCOUNTSUFFIXTOKEN{index}", value)
    normalized = re.sub(r"\s+", " ", normalized).strip(" -")
    return f"{method}:{normalized}" if normalized else method


def transaction_direction(amount):
    if amount is None:
        return "any"
    return "in" if amount > 0 else "out" if amount < 0 else "any"


def matching_pattern_rule(session, account_id, description, amount):
    """Find the most specific active durable classification rule."""
    pattern = description_pattern_key(description, amount)
    direction = transaction_direction(amount)
    return (
        session.query(TransactionPatternRule)
        .filter(
            TransactionPatternRule.active.is_(True),
            TransactionPatternRule.pattern_key == pattern,
            TransactionPatternRule.direction.in_((direction, "any")),
            TransactionPatternRule.account_id.in_((account_id, None)),
        )
        .order_by(TransactionPatternRule.account_id.is_(None), TransactionPatternRule.direction == "any")
        .first()
    )


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
    matches = [row for row in reviewed if description_pattern_key(row.cleaned_description, row.amount) == pattern]
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

from decimal import Decimal, InvalidOperation


def parse_money(value, *, non_negative=False):
    """Parse finite financial input without passing through binary floating point."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Amount is required.")
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid amount.") from None
    if not amount.is_finite():
        raise ValueError("Amount must be finite.")
    amount = amount.quantize(Decimal("0.01"))
    if non_negative and amount < 0:
        raise ValueError("Amount cannot be negative.")
    return amount

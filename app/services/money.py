from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re


def parse_money(value: str, *, allow_negative=False, non_negative=None) -> Decimal:
    """Parse a finite monetary string and return a two-decimal ``Decimal``.

    ``non_negative`` remains as a compatibility option for existing callers.
    New code should use the clearer ``allow_negative`` argument.
    """
    if not isinstance(value, str):
        raise ValueError("Amount must be entered as text.")
    if non_negative is not None:
        allow_negative = not non_negative
    text = value.strip()
    if not text:
        raise ValueError("Amount is required.")
    text = re.sub(r"^[€£$]\s*", "", text).replace(",", "")
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid amount.") from None
    if not amount.is_finite():
        raise ValueError("Amount must be finite.")
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not allow_negative and amount < 0:
        raise ValueError("Amount cannot be negative.")
    return amount

import re
from decimal import Decimal

from pypdf import PdfReader

from app.services.imports.exceptions import CSVImportError
from app.services.imports.normalization import clean_description, normalize_amount, normalize_date


def infer_credit_union_context(description, amount, existing_notes):
    text = description.lower()
    keywords = [
        "credit union",
        "community union",
        "hse cu",
        "hsecu",
        "loan",
        "repayment",
        "drawdown",
        "draw down",
    ]
    if not any(keyword in text for keyword in keywords):
        return existing_notes

    inferred = None
    if amount > Decimal("0"):
        inferred = "Inferred transfer: Credit Union -> Personal Account"
    elif amount < Decimal("0"):
        inferred = "Inferred transfer: Personal Account -> Credit Union"

    if not inferred:
        return existing_notes

    if existing_notes:
        return f"{existing_notes} | {inferred}"

    return inferred


def derive_amount_from_balance(previous_balance, current_balance, fallback_amount, description):
    if previous_balance is not None:
        delta = (current_balance - previous_balance).quantize(Decimal("0.01"))
        if delta != Decimal("0.00"):
            return delta

    if fallback_amount is None:
        return Decimal("0.00")

    description_lower = description.lower()
    outgoing_markers = ["disbur", "fee", "repayment", "loan", "payroll", "debit"]
    incoming_markers = ["lodgement", "refund", "credit", "topup", "top up"]

    if any(marker in description_lower for marker in outgoing_markers):
        return (fallback_amount * Decimal("-1")).quantize(Decimal("0.01"))

    if any(marker in description_lower for marker in incoming_markers):
        return fallback_amount

    return fallback_amount


def parse_hsecu_pdf_text(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    opening_balance_pattern = re.compile(
        r"^(?P<date>\d{2}\s+[A-Za-z]{3}\s+\d{2})\s+Opening Balance\s+(?P<balance>[\d,]+\.\d{2})$",
        flags=re.IGNORECASE,
    )
    line_pattern = re.compile(
        r"^(?:(?P<date>\d{2}\s+[A-Za-z]{3}\s+\d{2})\s+)?(?P<description>.+?)\s+(?P<amount>[\d,]+\.\d{2})\s+(?P<balance>[\d,]+\.\d{2})$"
    )

    rows = []
    last_date = None
    previous_balance = None

    for line in lines:
        lowered = line.lower()
        if lowered.startswith("date description") or lowered.startswith("page:"):
            continue

        opening_match = opening_balance_pattern.match(line)
        if opening_match:
            last_date = normalize_date(opening_match.group("date"))
            previous_balance = normalize_amount(opening_match.group("balance"))
            continue

        match = line_pattern.match(line)
        if not match:
            continue

        date_value = match.group("date")
        description = clean_description(match.group("description"))
        fallback_amount = normalize_amount(match.group("amount"))
        current_balance = normalize_amount(match.group("balance"))

        if date_value:
            last_date = normalize_date(date_value)

        if last_date is None:
            continue

        amount_value = derive_amount_from_balance(
            previous_balance, current_balance, fallback_amount, description
        )
        previous_balance = current_balance

        if amount_value == Decimal("0.00"):
            continue

        notes_value = infer_credit_union_context(description, amount_value, "Source: HSE CU PDF")

        rows.append(
            {
                "posted_date": last_date,
                "original_description": description,
                "cleaned_description": clean_description(description),
                "amount": amount_value,
                "household_flag": "unknown",
                "notes": notes_value,
                "source": "hsecu_pdf",
            }
        )

    if not rows:
        raise CSVImportError("No transactions were detected in this PDF statement.")

    return rows


def parse_pdf_statement(file_storage):
    file_storage.stream.seek(0)
    reader = PdfReader(file_storage.stream)
    text_content = "\n".join((page.extract_text() or "") for page in reader.pages)

    if not text_content.strip():
        raise CSVImportError("Unable to read text from this PDF statement.")

    rows = parse_hsecu_pdf_text(text_content)
    file_storage.stream.seek(0)
    return rows

from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.services.imports.exceptions import CSVImportError


def normalize_date(raw_date):
    candidate = raw_date.strip()
    supported_formats = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %y"]

    for format_str in supported_formats:
        try:
            return datetime.strptime(candidate, format_str).date()
        except ValueError:
            continue

    raise CSVImportError(f"Unrecognized date format: {raw_date}")


def normalize_amount(raw_amount):
    candidate = (
        raw_amount.strip()
        .replace(",", "")
        .replace("$", "")
        .replace("£", "")
        .replace("€", "")
    )

    if candidate.startswith("(") and candidate.endswith(")"):
        candidate = f"-{candidate[1:-1]}"

    try:
        return Decimal(candidate).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise CSVImportError(f"Invalid amount value: {raw_amount}") from exc


def normalize_optional_amount(raw_amount):
    if raw_amount is None:
        return None

    if not str(raw_amount).strip():
        return None

    return normalize_amount(str(raw_amount))


def clean_description(description):
    cleaned = " ".join(description.strip().split())
    return cleaned[:255]


def row_value(row, key):
    if not key:
        return ""
    return str(row.get(key, "")).strip()


def derive_amount(row, schema):
    if schema["amount"]:
        return normalize_amount(row.get(schema["amount"], "0"))

    debit = normalize_optional_amount(row.get(schema["debit"], "") if schema["debit"] else "")
    credit = normalize_optional_amount(row.get(schema["credit"], "") if schema["credit"] else "")

    if debit is None and credit is None:
        raise CSVImportError("Transaction row has neither debit nor credit amount.")

    if debit is not None and credit is not None:
        return (credit - debit).quantize(Decimal("0.01"))

    if credit is not None:
        return credit

    return (debit * Decimal("-1")).quantize(Decimal("0.01"))

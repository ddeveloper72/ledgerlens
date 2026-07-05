import re

from app.services.imports.exceptions import CSVImportError

REQUIRED_COLUMNS = {"date", "description", "amount"}

FIELD_ALIASES = {
    "date": [
        "date",
        "posted transactions date",
        "transaction date",
        "value date",
        "date completed",
    ],
    "amount": ["amount", "transaction amount", "local currency amount", "net", "gross"],
    "debit": [
        "debit",
        "debit amount",
        "withdrawal",
        "withdrawals",
        "outflow",
        "money out",
        "paid out",
    ],
    "credit": [
        "credit",
        "credit amount",
        "deposit",
        "lodgement",
        "lodgements",
        "inflow",
        "money in",
        "paid in",
    ],
    "household_flag": ["household_flag", "household flag", "flag"],
    "notes": ["notes", "memo", "reference", "transaction notes", "note", "subject"],
    "transaction_type": ["transaction type", "type"],
    "status": ["status", "transaction status"],
    "transaction_id": ["transaction id", "txn id", "id"],
    "reference_transaction_id": ["reference txn id", "reference transaction id"],
    "counterparty": ["name", "counterparty", "payee", "payer"],
    "item_title": ["item title", "item", "product", "service"],
    "from_email": ["from email address", "from email"],
    "to_email": ["to email address", "to email"],
    "time": ["time", "transaction time"],
    "currency": ["currency", "transaction currency"],
}

DESCRIPTION_ALIASES = [
    "description",
    "narrative",
    "details",
    "merchant",
    "name",
    "item title",
    "description1",
    "description2",
    "description3",
]


def normalize_header(header):
    """Normalize header text for resilient alias matching."""
    normalized = re.sub(r"[^a-z0-9]+", " ", (header or "").strip().lower())
    return " ".join(normalized.split())


def _find_matching_headers(headers, aliases):
    """Return all headers that match any normalized alias."""
    alias_set = {normalize_header(alias) for alias in aliases}
    return [header for header in headers if normalize_header(header) in alias_set]


def _find_first_matching_header(headers, aliases):
    """Return the first matching header for a given alias set."""
    matches = _find_matching_headers(headers, aliases)
    return matches[0] if matches else None


def detect_schema(headers):
    """Detect supported CSV schema and return canonical field mapping."""
    if not headers:
        raise CSVImportError("CSV file is empty or missing headers.")

    date_header = _find_first_matching_header(headers, FIELD_ALIASES["date"])
    amount_header = _find_first_matching_header(headers, FIELD_ALIASES["amount"])
    debit_header = _find_first_matching_header(headers, FIELD_ALIASES["debit"])
    credit_header = _find_first_matching_header(headers, FIELD_ALIASES["credit"])
    description_headers = _find_matching_headers(headers, DESCRIPTION_ALIASES)

    if debit_header or credit_header:
        amount_header = None

    if not date_header:
        raise CSVImportError(
            "CSV is missing a supported date column (for example: date or posted transactions date)."
        )

    if not description_headers:
        raise CSVImportError(
            "CSV is missing a supported description column (for example: description or description1)."
        )

    if not amount_header and not (debit_header or credit_header):
        raise CSVImportError(
            "CSV is missing amount fields. Provide amount or debit and credit columns."
        )

    schema = {
        "date": date_header,
        "amount": amount_header,
        "debit": debit_header,
        "credit": credit_header,
        "description_parts": description_headers,
        "household_flag": _find_first_matching_header(headers, FIELD_ALIASES["household_flag"]),
        "notes": _find_first_matching_header(headers, FIELD_ALIASES["notes"]),
        "transaction_type": _find_first_matching_header(headers, FIELD_ALIASES["transaction_type"]),
        "status": _find_first_matching_header(headers, FIELD_ALIASES["status"]),
        "transaction_id": _find_first_matching_header(headers, FIELD_ALIASES["transaction_id"]),
        "reference_transaction_id": _find_first_matching_header(
            headers, FIELD_ALIASES["reference_transaction_id"]
        ),
        "counterparty": _find_first_matching_header(headers, FIELD_ALIASES["counterparty"]),
        "item_title": _find_first_matching_header(headers, FIELD_ALIASES["item_title"]),
        "from_email": _find_first_matching_header(headers, FIELD_ALIASES["from_email"]),
        "to_email": _find_first_matching_header(headers, FIELD_ALIASES["to_email"]),
        "time": _find_first_matching_header(headers, FIELD_ALIASES["time"]),
        "currency": _find_first_matching_header(headers, FIELD_ALIASES["currency"]),
    }

    schema["source"] = "paypal" if schema["transaction_id"] and schema["counterparty"] else "generic"
    return schema


def validate_csv_headers(headers):
    """Validate headers against required amount/date/description support."""
    schema = detect_schema(headers)
    if schema["amount"]:
        return

    if not (schema["debit"] or schema["credit"]):
        formatted = ", ".join(sorted(REQUIRED_COLUMNS))
        raise CSVImportError(f"CSV missing required columns: {formatted}")

from datetime import datetime, timedelta
from decimal import Decimal

from app.models import Account, Transaction
from app.services.imports.normalization import clean_description, derive_amount, row_value


def _build_paypal_description(row, schema):
    """Build user-facing merchant description from PayPal row fields."""
    counterparty = row.get(schema["counterparty"], "").strip() if schema["counterparty"] else ""
    item_title = row.get(schema["item_title"], "").strip() if schema["item_title"] else ""

    if item_title:
        return item_title

    if counterparty:
        return counterparty

    return "PayPal Transaction"


def _build_paypal_bank_description(row, schema):
    """Build bank-style cleaned description from PayPal transaction type."""
    transaction_type = row.get(schema["transaction_type"], "").strip() if schema["transaction_type"] else ""
    lowered_type = transaction_type.lower()

    if "preapproved" in lowered_type or "bill user payment" in lowered_type:
        return "D/D PayPal Europe"

    if "checkout" in lowered_type or "payment" in lowered_type:
        return "PayPal Card Payment"

    if transaction_type:
        return f"PayPal {transaction_type}"

    return "PayPal Transaction"


def _paypal_type_lower(row, schema):
    """Return lowercase PayPal transaction type for rule matching."""
    if not schema["transaction_type"]:
        return ""
    return row.get(schema["transaction_type"], "").strip().lower()


def _is_paypal_currency_conversion(row, schema):
    """Check whether a PayPal row represents a currency conversion entry."""
    return "currency conversion" in _paypal_type_lower(row, schema)


def _is_paypal_payment_row(row, schema):
    """Identify PayPal rows that represent ledger-relevant payment activity."""
    type_lower = _paypal_type_lower(row, schema)
    include_markers = ["payment", "checkout", "bill user payment"]
    exclude_markers = [
        "currency conversion",
        "bank deposit",
        "card deposit",
        "authorization",
        "transfer",
        "withdrawal",
    ]

    if not any(marker in type_lower for marker in include_markers):
        counterparty = row_value(row, schema["counterparty"]) if schema["counterparty"] else ""
        try:
            row_amount = derive_amount(row, schema)
        except Exception:
            return False

        if counterparty and row_amount < Decimal("0"):
            return True

        return False

    if any(marker in type_lower for marker in exclude_markers):
        return False

    return True


def should_import_paypal_row(row, schema):
    """Filter PayPal rows to completed payment rows only."""
    status = row.get(schema["status"], "").strip().lower() if schema["status"] else ""
    if status and status != "completed":
        return False

    return _is_paypal_payment_row(row, schema)


PAYPAL_INTERNAL_MARKERS = {
    "bank deposit": "PayPal bank funding entry",
    "card deposit": "PayPal card funding entry",
    "currency conversion": "PayPal currency conversion entry",
    "authorization": "PayPal authorization entry",
    "transfer": "PayPal internal transfer entry",
    "withdrawal": "PayPal withdrawal entry",
}


def paypal_internal_reason(transaction):
    """Return an auditable exclusion reason for a legacy PayPal processing row."""
    text = " ".join(
        value for value in [transaction.original_description, transaction.cleaned_description, transaction.notes]
        if value
    ).lower()
    for marker, reason in PAYPAL_INTERNAL_MARKERS.items():
        if marker in text:
            return reason
    return None


def exclude_legacy_paypal_internal_rows(session):
    """Mark legacy PayPal wallet bookkeeping rows excluded without deleting raw history."""
    candidates = (
        session.query(Transaction)
        .join(Transaction.account)
        .filter(
            Transaction.excluded_from_analysis.is_(False),
            Account.account_type == "wallet",
            Account.name.ilike("%paypal%"),
        )
        .all()
    )
    excluded = 0
    for transaction in candidates:
        reason = paypal_internal_reason(transaction)
        if not reason:
            continue
        transaction.excluded_from_analysis = True
        transaction.exclusion_reason = reason
        transaction.excluded_at = datetime.now()
        excluded += 1
    from app.services.recurrence_service import deactivate_ineligible_recurring_records

    deactivate_ineligible_recurring_records(session)
    session.flush()
    return excluded


def restore_excluded_paypal_internal_rows(session):
    """Restore rows excluded by this maintenance rule if an audit reversal is needed."""
    rows = (
        session.query(Transaction)
        .join(Transaction.account)
        .filter(
            Transaction.excluded_from_analysis.is_(True),
            Transaction.exclusion_reason.like("PayPal %"),
            Account.account_type == "wallet",
            Account.name.ilike("%paypal%"),
        )
        .all()
    )
    for transaction in rows:
        transaction.excluded_from_analysis = False
        transaction.exclusion_reason = None
        transaction.excluded_at = None
    session.flush()
    return len(rows)


def find_paypal_fx_settlement_amount(payment_row, raw_rows, schema, payment_amount):
    """Find matching FX conversion amount that maps a payment to bank-settled value."""
    if not schema["time"]:
        return None

    payment_date = row_value(payment_row, schema["date"])
    payment_time = row_value(payment_row, schema["time"])
    if not payment_date or not payment_time:
        return None

    candidates = []
    for row in raw_rows:
        if not _is_paypal_currency_conversion(row, schema):
            continue

        status = row_value(row, schema["status"]).lower() if schema["status"] else ""
        if status and status != "completed":
            continue

        if row_value(row, schema["date"]) != payment_date:
            continue

        if row_value(row, schema["time"]) != payment_time:
            continue

        try:
            conversion_amount = derive_amount(row, schema)
        except Exception:
            continue

        if conversion_amount * payment_amount <= 0:
            continue

        if abs(conversion_amount) < abs(payment_amount):
            continue

        candidates.append(conversion_amount)

    if not candidates:
        return None

    candidates.sort(key=lambda value: abs(abs(value) - abs(payment_amount)))
    return candidates[0]


def build_paypal_notes(row, schema):
    """Build normalized notes string from PayPal metadata columns."""
    raw_note = row.get(schema["notes"], "").strip() if schema["notes"] else ""
    status = row.get(schema["status"], "").strip() if schema["status"] else ""
    transaction_id = row.get(schema["transaction_id"], "").strip() if schema["transaction_id"] else ""
    reference_id = (
        row.get(schema["reference_transaction_id"], "").strip()
        if schema["reference_transaction_id"]
        else ""
    )
    from_email = row.get(schema["from_email"], "").strip() if schema["from_email"] else ""
    to_email = row.get(schema["to_email"], "").strip() if schema["to_email"] else ""

    note_parts = []
    if raw_note:
        note_parts.append(raw_note)
    if status:
        note_parts.append(f"Status: {status}")
    if transaction_id:
        note_parts.append(f"Txn ID: {transaction_id}")
    if reference_id:
        note_parts.append(f"Ref Txn ID: {reference_id}")
    if from_email:
        note_parts.append(f"From: {from_email}")
    if to_email:
        note_parts.append(f"To: {to_email}")

    return " | ".join(note_parts)


def _extract_alt_description_from_paypal_row(description):
    """Extract alternate merchant description from PayPal-style text."""
    if not description:
        return None

    cleaned = clean_description(description)
    lowered = cleaned.lower()
    if lowered.startswith("paypal "):
        cleaned = cleaned[7:]

    first_segment = cleaned.split("|")[0].strip()
    return first_segment or None


def _remove_alt_description_metadata(notes):
    """Remove alternate-description metadata fragments from notes."""
    if not notes:
        return notes

    parts = [part.strip() for part in notes.split("|") if part.strip()]
    filtered = [
        part
        for part in parts
        if not part.lower().startswith("alt description:")
        and part.lower() != "linked from historical paypal row"
    ]
    return " | ".join(filtered) if filtered else None


def _extract_alt_description_from_notes(notes):
    """Read the first alternate-description metadata segment from notes."""
    if not notes:
        return None

    for part in [segment.strip() for segment in notes.split("|") if segment.strip()]:
        if part.lower().startswith("alt description:"):
            return part.split(":", 1)[1].strip()
    return None


def _is_low_signal_alt_description(alt_description):
    """Flag low-value alternate descriptions that should not be surfaced."""
    if not alt_description:
        return True

    lowered = alt_description.lower()
    noisy_markers = ["general currency conversion", "bank deposit", "card deposit", "transfer", "authorization"]
    return any(marker in lowered for marker in noisy_markers)


def _find_paypal_reconciliation_candidate(session, account_id, posted_date, amount):
    """Find likely bank transaction candidate for PayPal reconciliation."""
    start_date = posted_date - timedelta(days=5)
    end_date = posted_date + timedelta(days=5)

    candidates = (
        session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.excluded_from_analysis.is_(False),
            Transaction.amount == amount,
            Transaction.posted_date >= start_date,
            Transaction.posted_date <= end_date,
        )
        .all()
    )

    if not candidates:
        return None

    paypal_candidates = []
    for candidate in candidates:
        haystack = f"{candidate.cleaned_description} {(candidate.notes or '')}".lower()
        if "paypal" in haystack or "d/d" in haystack or "direct debit" in haystack:
            paypal_candidates.append(candidate)

    if not paypal_candidates:
        return None

    paypal_candidates.sort(
        key=lambda candidate: (abs((candidate.posted_date - posted_date).days), candidate.id)
    )
    return paypal_candidates[0]


def _find_historical_paypal_source_rows(session, account_id, posted_date, amount):
    """Locate historical PayPal source rows for alternate-description backfill."""
    start_date = posted_date - timedelta(days=5)
    end_date = posted_date + timedelta(days=5)

    candidates = (
        session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.excluded_from_analysis.is_(False),
            Transaction.posted_date >= start_date,
            Transaction.posted_date <= end_date,
        )
        .all()
    )

    direct_matches = []
    conversion_rows = []
    merchant_rows = []

    for candidate in candidates:
        if not candidate.cleaned_description:
            continue

        lower_description = candidate.cleaned_description.lower()
        if not lower_description.startswith("paypal "):
            continue

        notes_lower = (candidate.notes or "").lower()
        if "status: completed" not in notes_lower:
            continue

        alt = _extract_alt_description_from_paypal_row(candidate.cleaned_description)
        if candidate.amount == amount and not _is_low_signal_alt_description(alt):
            direct_matches.append(candidate)
            continue

        if "currency conversion" in lower_description and candidate.amount == amount:
            conversion_rows.append(candidate)
            continue

        if _is_low_signal_alt_description(alt):
            continue

        if candidate.amount * amount <= 0:
            continue

        merchant_rows.append(candidate)

    if direct_matches:
        direct_matches.sort(
            key=lambda candidate: (abs((candidate.posted_date - posted_date).days), candidate.id)
        )
        return direct_matches

    if conversion_rows and merchant_rows:
        merchant_rows.sort(
            key=lambda candidate: (
                abs((candidate.posted_date - posted_date).days),
                abs(abs(candidate.amount) - abs(amount)),
                candidate.id,
            )
        )
        return merchant_rows

    return []


def _merge_notes(existing_notes, extra_note):
    """Append note fragments while avoiding duplicates and empty values."""
    if not extra_note:
        return existing_notes

    if not existing_notes:
        return extra_note

    if extra_note in existing_notes:
        return existing_notes

    return f"{existing_notes} | {extra_note}"


def backfill_paypal_alternate_descriptions(session, account_id=None):
    """Backfill PayPal alternate descriptions onto matching bank direct-debit rows."""
    bank_rows_query = session.query(Transaction).filter(Transaction.excluded_from_analysis.is_(False))
    if account_id is not None:
        bank_rows_query = bank_rows_query.filter(Transaction.account_id == account_id)

    bank_rows = bank_rows_query.all()
    updated_count = 0

    for bank_row in bank_rows:
        description_lower = (bank_row.cleaned_description or "").lower()
        notes_lower = (bank_row.notes or "").lower()

        if description_lower.startswith("paypal "):
            cleaned_notes = _remove_alt_description_metadata(bank_row.notes)
            if cleaned_notes != bank_row.notes:
                bank_row.notes = cleaned_notes
            continue

        existing_alt = _extract_alt_description_from_notes(bank_row.notes)
        if existing_alt and not _is_low_signal_alt_description(existing_alt):
            continue

        if existing_alt:
            bank_row.notes = _remove_alt_description_metadata(bank_row.notes)
            notes_lower = (bank_row.notes or "").lower()

        if (
            not description_lower.startswith("d/d paypal") and "paypal europe" not in description_lower
        ):
            continue

        if "direct debit" not in notes_lower:
            continue

        source_rows = _find_historical_paypal_source_rows(
            session, bank_row.account_id, bank_row.posted_date, bank_row.amount
        )
        if not source_rows:
            continue

        alt_description = _extract_alt_description_from_paypal_row(source_rows[0].cleaned_description)
        if not alt_description:
            continue

        bank_row.notes = _merge_notes(bank_row.notes, f"Alt Description: {alt_description}")
        bank_row.notes = _merge_notes(bank_row.notes, "Linked from historical PayPal row")
        updated_count += 1

    return updated_count


def reconcile_paypal_to_bank_transaction(session, account_id, row):
    """Enrich a matching bank transaction with metadata from a PayPal import row."""
    candidate = _find_paypal_reconciliation_candidate(
        session, account_id, row["posted_date"], row["amount"]
    )
    if not candidate:
        return False

    if candidate.original_description:
        candidate.cleaned_description = clean_description(candidate.original_description)

    alt_description = row.get("paypal_alt_description") or row["cleaned_description"]
    alt_description_note = f"Alt Description: {clean_description(alt_description)}"
    candidate.notes = _merge_notes(candidate.notes, alt_description_note)
    candidate.notes = _merge_notes(candidate.notes, row.get("notes"))
    candidate.notes = _merge_notes(candidate.notes, "Linked from PayPal import")
    return True

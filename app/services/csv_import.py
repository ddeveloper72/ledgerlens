import csv
import io

from app.extensions import db
from app.models import ImportBatch, StatementImport, Transaction
from app.services.categorization import assign_category
from app.services.imports.credit_union_import import (
    infer_credit_union_context,
    parse_hsecu_pdf_text,
    parse_pdf_statement,
)
from app.services.imports.duplicate_detection import is_transaction_duplicate
from app.services.imports.exceptions import CSVImportError
from app.services.imports.import_service import (
    DOCUMENT_TYPE_LABELS,
    SOURCE_NEEDS_ACCOUNT_KEY,
    amend_existing_import_metadata,
    build_statement_metadata,
    compute_file_fingerprint,
    read_csv_text,
)
from app.services.imports.normalization import (
    clean_description,
    derive_amount,
    normalize_amount,
    normalize_date,
    row_value,
)
from app.services.imports.paypal_import import (
    backfill_paypal_alternate_descriptions,
    build_paypal_notes,
    find_paypal_fx_settlement_amount,
    reconcile_paypal_to_bank_transaction,
    should_import_paypal_row,
    _build_paypal_bank_description,
    _build_paypal_description,
)
from app.services.imports.schema_detection import detect_schema, validate_csv_headers
from app.services.merchant_mapping import create_or_get_merchant, resolve_merchant


def _build_description(row, description_headers):
    """Compose a normalized description from the configured description columns."""
    parts = [row.get(header, "").strip() for header in description_headers if row.get(header)]
    combined = " ".join(part for part in parts if part)
    return combined or "Unknown Transaction"


def _merge_notes(existing_notes, extra_note):
    """Append note fragments while avoiding duplicates and empty values."""
    if not extra_note:
        return existing_notes

    if not existing_notes:
        return extra_note

    if extra_note in existing_notes:
        return existing_notes

    return f"{existing_notes} | {extra_note}"


def parse_csv(file_storage):
    """Parse CSV statement content into normalized transaction dictionaries."""
    csv_text = read_csv_text(file_storage)
    text_stream = io.StringIO(csv_text)
    sample = csv_text[:4096]

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(text_stream, dialect=dialect, skipinitialspace=True)
    schema = detect_schema(reader.fieldnames)
    validate_csv_headers(reader.fieldnames)

    raw_rows = list(reader)
    normalized_rows = []
    seen_rows = 0
    paypal_seen_types = set()
    paypal_skipped_rows = 0

    for row in raw_rows:
        seen_rows += 1

        if schema["source"] == "paypal":
            type_name = row_value(row, schema["transaction_type"]) if schema["transaction_type"] else ""
            if type_name:
                paypal_seen_types.add(type_name)

            if not should_import_paypal_row(row, schema):
                paypal_skipped_rows += 1
                continue

            paypal_detail_description = _build_paypal_description(row, schema)
            description = _build_paypal_bank_description(row, schema)
        else:
            description = _build_description(row, schema["description_parts"])

        amount_value = derive_amount(row, schema)

        if schema["source"] == "paypal":
            fx_settled_amount = find_paypal_fx_settlement_amount(
                row, raw_rows, schema, amount_value
            )
            if fx_settled_amount is not None:
                amount_value = fx_settled_amount

        notes_value = row.get(schema["notes"], "").strip() if schema["notes"] else ""
        transaction_type = (
            row.get(schema["transaction_type"], "").strip() if schema["transaction_type"] else ""
        )

        if schema["source"] == "paypal":
            notes_value = build_paypal_notes(row, schema)
            source_currency = row_value(row, schema["currency"]).upper() if schema["currency"] else ""
            if source_currency and source_currency != "EUR":
                notes_value = _merge_notes(notes_value, f"Source Currency: {source_currency}")
            if clean_description(paypal_detail_description) != clean_description(description):
                notes_value = _merge_notes(
                    notes_value,
                    f"Alt Description: {clean_description(paypal_detail_description)}",
                )

        if transaction_type and not notes_value:
            notes_value = f"Type: {transaction_type}"

        notes_value = infer_credit_union_context(description, amount_value, notes_value)

        normalized_rows.append(
            {
                "posted_date": normalize_date(row.get(schema["date"], "")),
                "original_description": description,
                "cleaned_description": clean_description(description),
                "amount": amount_value,
                "household_flag": (
                    row.get(schema["household_flag"], "unknown").strip()
                    if schema["household_flag"]
                    else "unknown"
                )
                or "unknown",
                "notes": notes_value or None,
                "source": schema["source"],
                "paypal_alt_description": (
                    clean_description(paypal_detail_description)
                    if schema["source"] == "paypal"
                    else None
                ),
            }
        )

    if not normalized_rows:
        if schema["source"] == "paypal" and seen_rows > 0:
            observed = ", ".join(sorted(paypal_seen_types)[:6])
            observed_suffix = f" Observed types: {observed}." if observed else ""
            raise CSVImportError(
                (
                    "No ledger-relevant completed PayPal payments were found in this file."
                    f" {paypal_skipped_rows} PayPal rows were skipped."
                    f"{observed_suffix}"
                )
            )
        raise CSVImportError(
            "The statement file contains headers but no transaction rows. Export a full transactions report and try again."
        )

    file_storage.stream.seek(0)
    return normalized_rows


def import_transactions(
    file_storage,
    account_id,
    declared_source="auto",
    manual_account_key=None,
    manual_bank_name=None,
):
    """Import statement rows into SQLite with dedupe, reconciliation, and metadata capture."""
    fingerprint = compute_file_fingerprint(file_storage)
    existing_import = StatementImport.query.filter_by(fingerprint=fingerprint).first()
    if existing_import:
        raise CSVImportError(
            (
                "This statement was already imported "
                f"as Batch #{existing_import.import_batch_id}. Duplicate import blocked."
            )
        )

    filename = (file_storage.filename or "").lower()
    if filename.endswith(".pdf"):
        rows = parse_pdf_statement(file_storage)
    else:
        rows = parse_csv(file_storage)

    if not rows:
        raise CSVImportError("No transactions were detected in the uploaded statement.")

    metadata = build_statement_metadata(
        file_storage,
        rows,
        declared_source,
        bank_name=manual_bank_name,
    )
    manual_key = (manual_account_key or "").strip() or None
    if manual_key:
        metadata["account_key"] = manual_key

    if metadata["declared_source"] in SOURCE_NEEDS_ACCOUNT_KEY and not metadata["account_key"]:
        raise CSVImportError(
            "Account key was not detected for this statement source. Please provide an account key in the import form."
        )

    import_batch = ImportBatch(source_filename=file_storage.filename, row_count=0)
    db.session.add(import_batch)
    db.session.flush()

    statement_import = StatementImport(
        import_batch_id=import_batch.id,
        fingerprint=fingerprint,
        declared_source=metadata["declared_source"],
        detected_source=metadata["detected_source"],
        bank_name=metadata.get("bank_name"),
        account_key=metadata["account_key"],
        statement_start_date=metadata["statement_start_date"],
        statement_end_date=metadata["statement_end_date"],
    )
    db.session.add(statement_import)

    created_count = 0
    skipped_duplicates = 0
    reconciled_count = 0
    skipped_unmatched_paypal = 0

    for row in rows:
        if row.get("source") == "paypal":
            if reconcile_paypal_to_bank_transaction(db.session, account_id, row):
                reconciled_count += 1
                continue

            skipped_unmatched_paypal += 1
            continue

        if is_transaction_duplicate(
            db.session,
            account_id,
            row["posted_date"],
            row["amount"],
            row["cleaned_description"],
        ):
            skipped_duplicates += 1
            continue

        merchant = resolve_merchant(db.session, row["cleaned_description"])
        if merchant is None:
            merchant = create_or_get_merchant(db.session, row["cleaned_description"])

        category = assign_category(db.session, merchant.name, row["cleaned_description"])
        category_id = None if category.name == "Uncategorized" else category.id

        transaction = Transaction(
            account_id=account_id,
            import_batch_id=import_batch.id,
            posted_date=row["posted_date"],
            original_description=row["original_description"],
            cleaned_description=row["cleaned_description"],
            merchant_id=merchant.id,
            category_id=category_id,
            amount=row["amount"],
            household_flag=row["household_flag"],
            notes=row["notes"],
            review_state="pending",
        )

        db.session.add(transaction)
        created_count += 1

    import_batch.row_count = created_count + reconciled_count
    db.session.commit()

    return {
        "created": created_count,
        "duplicates": skipped_duplicates,
        "reconciled": reconciled_count,
        "paypal_unmatched": skipped_unmatched_paypal,
        "batch_id": import_batch.id,
        "statement_from": metadata["statement_start_date"],
        "statement_to": metadata["statement_end_date"],
        "declared_source": metadata["declared_source"],
        "detected_source": metadata["detected_source"],
        "bank_name": metadata.get("bank_name"),
        "account_key": metadata["account_key"],
    }

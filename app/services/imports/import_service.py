import csv
import hashlib
import io
import re

from app.extensions import db
from app.models import ImportBatch, StatementImport, Transaction
from app.services.imports.schema_detection import normalize_header

DOCUMENT_TYPE_LABELS = {
    "auto": "Auto Detect",
    "aib_bank": "AIB Bank Statement",
    "paypal": "PayPal Statement",
    "credit_union": "Credit Union Statement",
}

SOURCE_NEEDS_ACCOUNT_KEY = {"paypal", "credit_union"}


def normalize_document_type(document_type):
    key = (document_type or "auto").strip().lower()
    return key if key in DOCUMENT_TYPE_LABELS else "auto"


def compute_file_fingerprint(file_storage):
    file_storage.stream.seek(0)
    raw_bytes = file_storage.stream.read()
    file_storage.stream.seek(0)
    return hashlib.sha256(raw_bytes).hexdigest()


def read_csv_text(file_storage):
    file_storage.stream.seek(0)
    raw_bytes = file_storage.stream.read()
    file_storage.stream.seek(0)
    return raw_bytes.decode("utf-8-sig", errors="replace")


def extract_aib_account_key(file_storage):
    csv_text = read_csv_text(file_storage)
    text_stream = io.StringIO(csv_text)
    sample = csv_text[:4096]

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(text_stream, dialect=dialect, skipinitialspace=True)
    if not reader.fieldnames:
        return None

    account_headers = [
        header
        for header in reader.fieldnames
        if normalize_header(header) in {"posted account", "account", "account number"}
    ]

    account_header = account_headers[0] if account_headers else None
    account_pattern = re.compile(r"(\d{6})\s*-\s*(\d{4,})")

    for row in reader:
        candidate = row.get(account_header, "") if account_header else ""
        if not candidate and reader.fieldnames:
            candidate = row.get(reader.fieldnames[0], "")

        candidate = (candidate or "").strip()
        match = account_pattern.search(candidate)
        if match:
            return f"{match.group(1)}-{match.group(2)}"

    return None


def build_statement_metadata(file_storage, rows, declared_source):
    declared_key = normalize_document_type(declared_source)
    dates = sorted({row["posted_date"] for row in rows if row.get("posted_date")})
    statement_start = dates[0] if dates else None
    statement_end = dates[-1] if dates else None

    detected = rows[0].get("source", "generic") if rows else "generic"
    account_key = None

    if declared_key == "aib_bank" or detected == "generic":
        account_key = extract_aib_account_key(file_storage)

    return {
        "declared_source": declared_key,
        "detected_source": detected,
        "account_key": account_key,
        "statement_start_date": statement_start,
        "statement_end_date": statement_end,
    }


def _infer_legacy_source_from_filename(filename):
    lower_name = (filename or "").lower()
    if lower_name.endswith(".pdf"):
        return "credit_union"
    if "transaction_export" in lower_name:
        return "aib_bank"
    if lower_name.startswith("download") and lower_name.endswith(".csv"):
        return "paypal"
    return "auto"


def _legacy_fingerprint_for_batch(batch):
    text = f"legacy-batch-{batch.id}-{batch.source_filename}-{batch.imported_at}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def amend_existing_import_metadata(session, default_aib_account_keys=None):
    default_aib_account_keys = default_aib_account_keys or []
    batches = ImportBatch.query.order_by(ImportBatch.id.asc()).all()
    amended_count = 0

    for batch in batches:
        declared_source = _infer_legacy_source_from_filename(batch.source_filename)

        if batch.statement_import:
            continue

        start_date = (
            session.query(db.func.min(Transaction.posted_date))
            .filter(Transaction.import_batch_id == batch.id)
            .scalar()
        )
        end_date = (
            session.query(db.func.max(Transaction.posted_date))
            .filter(Transaction.import_batch_id == batch.id)
            .scalar()
        )

        meta = StatementImport(
            import_batch_id=batch.id,
            fingerprint=_legacy_fingerprint_for_batch(batch),
            declared_source=declared_source,
            detected_source=declared_source,
            account_key=None,
            statement_start_date=start_date,
            statement_end_date=end_date,
        )
        session.add(meta)
        amended_count += 1

    session.flush()

    if default_aib_account_keys:
        pending_aib = (
            session.query(StatementImport)
            .filter(
                StatementImport.declared_source == "aib_bank",
                StatementImport.account_key.is_(None),
            )
            .order_by(StatementImport.import_batch_id.asc())
            .all()
        )

        for key_index, meta in enumerate(pending_aib):
            if key_index >= len(default_aib_account_keys):
                break
            meta.account_key = default_aib_account_keys[key_index]

    return amended_count

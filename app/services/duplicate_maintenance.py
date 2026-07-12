from collections import defaultdict
from datetime import datetime

from app.models import Transaction


def verified_duplicate_rows(session):
    """Return later cross-batch rows with an exact earlier ledger identity."""
    rows = (
        session.query(Transaction)
        .filter(
            Transaction.import_batch_id.isnot(None),
            Transaction.excluded_from_analysis.is_(False),
        )
        .order_by(Transaction.import_batch_id, Transaction.id)
        .all()
    )
    groups = defaultdict(list)
    for row in rows:
        key = (
            row.account_id,
            row.posted_date,
            row.amount,
            row.cleaned_description,
        )
        groups[key].append(row)
    duplicates = []
    for matches in groups.values():
        batch_ids = {row.import_batch_id for row in matches}
        if len(batch_ids) < 2:
            continue
        earliest_batch = min(batch_ids)
        duplicates.extend(row for row in matches if row.import_batch_id != earliest_batch)
    return duplicates


def exclude_verified_duplicates(session):
    """Preserve later raw duplicate rows but exclude them from analysis and review."""
    rows = verified_duplicate_rows(session)
    for row in rows:
        row.excluded_from_analysis = True
        row.exclusion_reason = "Duplicate of earlier imported transaction"
        row.excluded_at = datetime.now()
    session.flush()
    return len(rows)

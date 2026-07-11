from datetime import date, timedelta

from app.models import Account, Category, Transaction


def data_completeness_report(session, period, today=None, recent_days=45):
    """Describe analysis coverage and expose visible reasons results may be incomplete."""
    today = today or date.today()
    account_rows = []
    stale_cutoff = today - timedelta(days=recent_days)
    for account in session.query(Account).order_by(Account.name).all():
        latest_row = session.query(Transaction.posted_date).filter_by(account_id=account.id, excluded_from_analysis=False).order_by(Transaction.posted_date.desc()).first()
        latest = latest_row[0] if latest_row else None
        account_rows.append({"account": account, "latest_date": latest, "stale": latest is None or latest < stale_cutoff})
    unreviewed = session.query(Transaction).filter_by(review_state="pending", excluded_from_analysis=False).count()
    uncategorized = session.query(Transaction).outerjoin(Category).filter(Transaction.excluded_from_analysis.is_(False), (Transaction.category_id.is_(None)) | (Category.name == "Uncategorized")).count()
    first_row = session.query(Transaction.posted_date).filter(Transaction.excluded_from_analysis.is_(False)).order_by(Transaction.posted_date.asc()).first()
    last_row = session.query(Transaction.posted_date).filter(Transaction.excluded_from_analysis.is_(False)).order_by(Transaction.posted_date.desc()).first()
    represented_start = first_row[0] if first_row else None
    represented_end = last_row[0] if last_row else None
    warnings = []
    if unreviewed: warnings.append(f"{unreviewed} transaction(s) still require review.")
    if uncategorized: warnings.append(f"{uncategorized} transaction(s) are uncategorized.")
    if any(row["stale"] for row in account_rows): warnings.append("One or more accounts have no recent import.")
    if represented_start is None or represented_start > period.start_date or represented_end < period.end_date: warnings.append("The selected reporting period is not fully represented by imported data.")
    return {"accounts": account_rows, "unreviewed": unreviewed, "uncategorized": uncategorized, "represented_start": represented_start, "represented_end": represented_end, "warnings": warnings, "complete": not warnings}
